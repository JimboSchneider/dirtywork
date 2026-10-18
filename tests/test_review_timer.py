"""Review timing starts explicitly, not at worker completion."""
import argparse
import json
from datetime import datetime, timedelta, timezone

import pytest

from dirtywork import rundir, runs
from dirtywork.dashboard_data import RunStore


@pytest.fixture
def review_run(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    directory = root / "example"
    directory.mkdir(parents=True)
    rundir.write_run_json(directory, {"status": "completed", "ended": "2026-10-17T00:00:00Z"})
    monkeypatch.setattr(rundir, "RUNS_DIR", root)
    clock = {"now": datetime(2026, 10, 18, 12, tzinfo=timezone.utc)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(runs, "datetime", Clock)
    return directory, clock


def verdict(seconds=None):
    return argparse.Namespace(slug="example", verdict="accept", note="Reviewed", review_seconds=seconds)


def test_review_start_and_verdict_record_only_the_review_interval(review_run):
    directory, clock = review_run
    from dirtywork.__main__ import main
    assert main(["runs", "review-start", "example"]) == 0
    clock["now"] += timedelta(seconds=42)
    assert main(["runs", "verdict", "example", "accept"]) == 0
    data = rundir.read_run_json(directory)
    assert data["review_seconds"] == 42
    assert data["review_seconds_source"] == "timer"
    assert data["review_started_at"] == "2026-10-18T12:00:00+00:00"
    assert data["time_to_verdict_s"] > 86400


def test_repeated_start_does_not_reset_timer_and_repeated_verdict_preserves_duration(review_run):
    directory, clock = review_run
    assert runs.cmd_review_start(argparse.Namespace(slug="example")) == 0
    clock["now"] += timedelta(seconds=20)
    assert runs.cmd_review_start(argparse.Namespace(slug="example")) == 0
    clock["now"] += timedelta(seconds=22)
    assert runs.cmd_verdict(verdict()) == 0
    clock["now"] += timedelta(seconds=120)
    assert runs.cmd_verdict(verdict()) == 0
    assert rundir.read_run_json(directory)["review_seconds"] == 42


def test_explicit_duration_overrides_timer_and_remains_zero_when_given(review_run):
    directory, clock = review_run
    runs.cmd_review_start(argparse.Namespace(slug="example"))
    clock["now"] += timedelta(seconds=42)
    assert runs.cmd_verdict(verdict(0)) == 0
    data = rundir.read_run_json(directory)
    assert data["review_seconds"] == 0
    assert data["review_seconds_source"] == "manual"


@pytest.mark.parametrize("seconds", [-1, float("nan"), float("inf")])
def test_invalid_manual_duration_is_rejected_without_writing(review_run, seconds):
    directory, _ = review_run
    before = (directory / "run.json").read_bytes()
    assert runs.cmd_verdict(verdict(seconds)) == 2
    assert (directory / "run.json").read_bytes() == before


@pytest.mark.parametrize("metadata", [{"status": "running"}, {"status": "completed", "verdict": "accept"}])
def test_review_timer_refuses_running_or_already_reviewed_runs(review_run, metadata):
    directory, _ = review_run
    rundir.write_run_json(directory, metadata)
    assert runs.cmd_review_start(argparse.Namespace(slug="example")) == 2
    assert "review_started_at" not in rundir.read_run_json(directory)


@pytest.mark.parametrize("start", ["not a timestamp", "2026-10-19T12:00:00Z"])
def test_bad_timer_cannot_silently_record_an_invalid_duration(review_run, start):
    directory, _ = review_run
    rundir.write_run_json(directory, {"status": "completed", "review_started_at": start})
    assert runs.cmd_verdict(verdict()) == 2
    assert "verdict" not in rundir.read_run_json(directory)


def test_dashboard_shows_live_review_and_then_includes_finished_duration(review_run):
    directory, clock = review_run
    assert runs.cmd_review_start(argparse.Namespace(slug="example")) == 0
    store = RunStore(directory.parent)
    clock["now"] += timedelta(seconds=15)
    detail = store.detail("example", now=clock["now"])
    assert detail["review_in_progress"] is True
    assert detail["review_elapsed_s"] == 15
    assert detail["review_seconds"] is None
    assert store.snapshot(now=clock["now"])["summary"]["median_review_s"] is None
    clock["now"] += timedelta(seconds=27)
    runs.cmd_verdict(verdict())
    detail = store.detail("example", now=clock["now"])
    assert detail["review_in_progress"] is False
    assert detail["review_seconds"] == 42
    assert store.snapshot(now=clock["now"])["summary"]["median_review_s"] == 42
