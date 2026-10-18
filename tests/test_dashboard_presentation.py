"""Exercise the dashboard presentation helpers and their projected evidence."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dirtywork.dashboard_data import RunStore


def test_presentation_helpers():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the dashboard browser regression")
    script = Path(__file__).with_name("dashboard_presentation.test.cjs")
    result = subprocess.run([node, "--test", str(script)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_snapshot_preserves_canonical_review_eligibility(tmp_path):
    metadata = {
        "live": {"status": "running", "host_pid": os.getpid()},
        "unconfirmed": {"status": "running"},
        "unknown": {"status": "unknown"},
        "pending": {"status": "completed"},
        "reviewing": {"status": "completed", "review_started_at": "2026-10-18T11:00:00Z"},
        "superseded": {"status": "completed", "resumed_by": "pending"},
        "transcript-ended": {"status": "running", "host_pid": os.getpid()},
    }
    for slug, data in metadata.items():
        directory = tmp_path / slug
        directory.mkdir()
        (directory / "run.json").write_text(json.dumps(data))
        if slug == "transcript-ended":
            (directory / "transcript.jsonl").write_text(
                json.dumps({"event": "run_end", "status": "verify_failed"}) + "\n")
    store = RunStore(tmp_path)
    projected = {run["slug"]: run for run in store.snapshot()["runs"]}
    assert {slug: run.get("review_eligible") for slug, run in projected.items()} == {
        "live": False, "unconfirmed": False, "unknown": False,
        "pending": True, "reviewing": True, "superseded": False,
        "transcript-ended": True,
    }
    for slug, run in projected.items():
        assert run["review_eligible"] is store.detail(slug)["review_eligible"]
