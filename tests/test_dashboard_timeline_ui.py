"""Run the actual worker timeline and separate review history with a small DOM."""
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dirtywork.dashboard_data import RunStore


def test_dashboard_timeline_renderers():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the dashboard browser regression")
    script = Path(__file__).with_name("dashboard_timeline.test.cjs")
    result = subprocess.run([node, "--test", str(script)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_review_only_history_preserves_calendar_dates_without_a_run_start(tmp_path):
    directory = tmp_path / "historical"
    directory.mkdir()
    (directory / "run.json").write_text(json.dumps({
        "status": "completed", "review_started_at": "2026-10-18 05:00:00-05:00",
        "verdict": "accept", "verdict_at": "2026-10-18T10:01:00Z",
    }))
    run = RunStore(tmp_path).detail("historical", now=datetime(2026, 10, 18, 12, tzinfo=timezone.utc))
    assert run["started"] is None
    timeline = run["timeline"]
    assert [point["ts"] for point in timeline["points"]] == [
        "2026-10-18T05:00:00-05:00", "2026-10-18T10:01:00+00:00",
    ]
    assert [point["at_s"] for point in timeline["points"]] == [0, 60]
    assert timeline["elapsed_s"] is None


def test_invalid_review_dates_remain_untimed_without_calendar_events(tmp_path):
    directory = tmp_path / "untimed"
    directory.mkdir()
    (directory / "run.json").write_text(json.dumps({
        "status": "completed", "review_started_at": "invalid review start",
        "verdict": "reject", "verdict_at": "invalid decision timestamp",
    }))
    timeline = RunStore(tmp_path).detail("untimed")["timeline"]
    assert timeline["points"] == []
    assert timeline["untimed_events"] == 2
    assert timeline["elapsed_s"] is None
