"""Best-effort live diff artifacts published serially by the worker harness."""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .osfs import open_nofollow

MAX_PATCH_BYTES = 1024 * 1024
MAX_RECORD_BYTES = 4 * 1024 * 1024


class LiveChangesPublisher:
    def __init__(self, run_dir, *, capture, now=None):
        self.run_dir = Path(run_dir)
        self.capture = capture
        self.now = now or (lambda: datetime.now(timezone.utc))

    def __call__(self):
        # Live evidence must never abort, delay indefinitely, or change the
        # outcome of the actual task. Capture supplies its own hard time cap.
        try:
            self._capture_and_publish()
        except Exception:
            pass  # A disappearing/unwritable run directory is non-fatal.

    def _capture_and_publish(self):
        from .sandbox.live_diff import LiveDiffTooLarge

        record = {"version": 1, "state": "ready", "patch": "",
                  "captured_at": self.now().isoformat(),
                  "message": "Live file changes · updates after worker actions"}
        try:
            patch = self.capture()
            if len(patch) > MAX_PATCH_BYTES:
                raise LiveDiffTooLarge("preview limit")
            decoded = patch.decode("utf-8", errors="replace")
            if len(decoded.encode("utf-8")) > MAX_PATCH_BYTES:
                raise LiveDiffTooLarge("decoded preview limit")
            record["patch"] = decoded
        except LiveDiffTooLarge:
            record.update(state="too_large", message="Live patch exceeds the 1 MiB preview limit.")
        except Exception:
            record.update(state="unavailable", message="Live preview unavailable; retrying after the next worker action.")
        encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        if len(encoded) > MAX_RECORD_BYTES:
            record.update(state="too_large", patch="", message="Live patch exceeds the preview size limit.")
            encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        target = self.run_dir / (".live-changes-" + secrets.token_hex(16) + ".tmp")
        fd = open_nofollow(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as source:
                source.write(encoded)
            os.replace(target, self.run_dir / "live_changes.json")
        finally:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
