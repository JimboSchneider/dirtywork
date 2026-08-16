from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


class Transcript:
    """Append-only JSONL event log, flushed per line so `tail -f` works.

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

    def write(self, event: str, **fields) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
        record.update(fields)
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

    def close(self) -> None:
        self._fh.close()
