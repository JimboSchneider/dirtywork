from datetime import datetime, timezone
import json
import os

import pytest

from dirtywork import review

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)


def test_terminal_transcript_allows_review_without_rewriting_status(tmp_path):
    data = {"status": "running", "host_pid": os.getpid()}
    (tmp_path / "transcript.jsonl").write_text(json.dumps({"event": "run_end", "status": "completed"}) + "\n")
    updated = review.start_review(data, NOW, run_dir=tmp_path)
    assert updated["review_started_at"] == NOW.isoformat()
    assert updated["status"] == "running"
    assert data == {"status": "running", "host_pid": os.getpid()}


def test_dead_worker_is_eligible(monkeypatch):
    monkeypatch.setattr(review, "pid_alive", lambda pid: False)
    assert review.effective_run_status({"status": "running", "host_pid": 123}) == ("running", "inactive")
    assert review.start_review({"status": "running", "host_pid": 123}, NOW)["review_started_at"]


@pytest.mark.parametrize("data", [{}, {"status": "unknown"}, {"status": "running"},
    {"status": "running", "host_pid": 0}, {"status": "running", "host_pid": True},
    {"status": "running", "host_pid": "123"}, {"status": "running", "host_pid": os.getpid()}])
def test_unknown_or_unconfirmed_or_live_workers_are_blocked(data):
    with pytest.raises(ValueError, match="finish|status"):
        review.start_review(data, NOW)


def test_parsed_terminal_evidence_wins_running_metadata():
    assert review.effective_run_status({"status": "running"}, {"event": "run_end", "status": "timeout"}) == ("timeout", "ended")
    assert review.start_review({"status": "running"}, NOW, end={"event": "run_end"})["review_started_at"]


def test_incomplete_terminal_record_does_not_unlock_review(tmp_path):
    (tmp_path / "transcript.jsonl").write_text('{"event":"run_end","status":"completed"}')
    with pytest.raises(ValueError):
        review.start_review({"status": "running"}, NOW, run_dir=tmp_path)


def test_superseded_run_cannot_start_review():
    with pytest.raises(ValueError, match="superseded"):
        review.start_review({"status": "completed", "resumed_by": "new"}, NOW)


def test_symlink_transcript_is_not_terminal_evidence(tmp_path):
    target = tmp_path / "outside.jsonl"
    target.write_text('{"event":"run_end","status":"completed"}\n')
    (tmp_path / "transcript.jsonl").symlink_to(target)
    with pytest.raises(ValueError):
        review.start_review({"status": "running"}, NOW, run_dir=tmp_path)
