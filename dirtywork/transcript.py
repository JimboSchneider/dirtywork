from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class Transcript:
    """Append-only JSONL event log.

    Outside a turn every `write` reaches disk immediately (`tail -f` sees it
    at once). Inside `turn()` writes are buffered and land together, in write
    order, when the turn exits -- normally or by ANY exception, KeyboardInterrupt
    included -- so the runner can amend a turn's records (the `finish` result,
    a `follow_up`) until the turn's wire messages are final (spec #60 §6.1).
    `tail -f` therefore shows a run one turn at a time; a hard kill loses at
    most the current turn's events.

    One lock covers the buffer AND the physical write, so a write from another
    thread (the docker watchdog's `sandbox_reset`) can neither interleave a
    partial line nor slip between the closing flush and the close itself:
    leaving a turn is a single critical section.

    The parent directory must already exist (created by
    `dirtywork.rundir.create_run_dir` before this is constructed) — this
    class no longer creates it. Opened with O_EXCL so a slug collision (or a
    symlink planted at the transcript path) is a loud failure instead of a
    silent append/overwrite, and O_NOFOLLOW so a symlink at the exact path
    is refused rather than followed.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_NOFOLLOW
        fd = os.open(str(self.path), flags, 0o600)
        self._fh = os.fdopen(fd, "a", encoding="utf-8")
        self._lock = threading.Lock()
        self._buffer = None      # a list while a turn is open, else None

    def write(self, event: str, **fields) -> dict | None:
        """Record the event. Returns the record (a dict the caller may amend
        until its turn flushes) while a turn is open; None otherwise, when the
        line is already on disk. `ts` is stamped here, never at flush time."""
        record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
        record.update(fields)
        with self._lock:
            if self._buffer is not None:
                self._buffer.append(record)
                return record
            self._write_line(record)
            return None

    def _write_line(self, record: dict) -> None:
        # Caller holds self._lock.
        # allow_nan=False keeps the JSONL strictly valid; a NaN/Infinity that
        # reached here (e.g. from a hostile server response) is re-dumped with
        # those constants coerced to null rather than emitting invalid JSON.
        try:
            line = json.dumps(record, ensure_ascii=False, allow_nan=False)
        except ValueError:
            scrubbed = json.loads(
                json.dumps(record, ensure_ascii=False),
                parse_constant=lambda _c: None,  # NaN/Infinity -> null
            )
            line = json.dumps(scrubbed, ensure_ascii=False, allow_nan=False)
        self._fh.write(line + "\n")
        self._fh.flush()

    def _flush_locked(self, close_turn: bool) -> None:
        # Caller holds self._lock. Swap the list out, write it, and -- when
        # closing -- mark the turn closed in the SAME critical section, so no
        # racing write can enter a buffer that is then cleared unflushed.
        pending = self._buffer or []
        self._buffer = None if close_turn else ([] if self._buffer is not None else None)
        for record in pending:
            self._write_line(record)

    def flush(self) -> None:
        """Write whatever is buffered now, in order, leaving the turn open."""
        with self._lock:
            self._flush_locked(close_turn=False)

    @contextmanager
    def turn(self):
        """Buffer every write until the block exits (see the class docstring)."""
        with self._lock:
            if self._buffer is not None:
                raise RuntimeError("Transcript.turn() is not reentrant")
            self._buffer = []
        try:
            yield
        finally:
            with self._lock:
                self._flush_locked(close_turn=True)

    def close(self) -> None:
        with self._lock:
            self._flush_locked(close_turn=True)
            self._fh.close()
