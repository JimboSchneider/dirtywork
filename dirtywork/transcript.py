from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class Transcript:
    """Append-only JSONL event log, flushed per line so `tail -f` works."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def write(self, event: str, **fields) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
        record.update(fields)
        # allow_nan=False keeps the JSONL strictly valid (NaN/Infinity from a
        # hostile server response would otherwise emit invalid JSON); fall back to
        # a coerced dump so a bad value can never crash the run mid-transcript.
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
