from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from dirtywork import rundir, runs

from .fake_docker import FakeCaptured


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init")
    _git(r, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-m", "i")
    return r


def _write_run(runs_dir: Path, slug: str, data: dict) -> Path:
    run_dir = runs_dir / slug
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(data))
    return run_dir


def test_cmd_list_prints_table_with_status_and_started(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "fix-bug-0101", {
        "status": "stalled", "started": "2026-08-16T00:00:00+00:00",
        "branch": "dirtywork/fix-bug-0101", "repo": str(repo),
        "worktree": str(repo / ".worktrees" / "dw-fix-bug-0101"),
        "container": None, "volume": None,
    })
    rc = runs.cmd_list(argparse.Namespace(json=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "SLUG" in out and "STATUS" in out and "RESUMED" in out
    assert "fix-bug-0101" in out
    assert "stalled" in out           # SP2.5 status must render like any other


def test_cmd_list_json_output(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "running", "started": "t", "branch": "b", "repo": str(repo),
        "worktree": str(repo), "container": None, "volume": None,
    })
    rc = runs.cmd_list(argparse.Namespace(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["slug"] == "slug1"
    assert payload[0]["status"] == "running"


def test_cmd_list_marks_resumed_runs(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "aaa-first", {
        "status": "max_turns", "started": "t", "branch": "dirtywork/aaa-first",
        "repo": str(repo), "worktree": str(repo), "container": None, "volume": None,
        "resumed_from": None, "resumed_by": "bbb-second",
    })
    _write_run(tmp_path / "runs", "bbb-second", {
        "status": "completed", "started": "t", "branch": "dirtywork/aaa-first",
        "repo": str(repo), "worktree": str(repo), "container": None, "volume": None,
        "resumed_from": "aaa-first", "resumed_by": None,
    })
    assert runs.cmd_list(argparse.Namespace(json=False)) == 0
    table = capsys.readouterr().out
    assert "by bbb-second" in table
    assert "from aaa-first" in table

    assert runs.cmd_list(argparse.Namespace(json=True)) == 0
    payload = {row["slug"]: row for row in json.loads(capsys.readouterr().out)}
    assert payload["aaa-first"]["resumed_by"] == "bbb-second"
    assert payload["bbb-second"]["resumed_from"] == "aaa-first"
    assert payload["bbb-second"]["resumed"] == "from aaa-first"


def test_cmd_list_no_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    rc = runs.cmd_list(argparse.Namespace(json=False))
    assert rc == 0
    assert "no runs found" in capsys.readouterr().out


def test_cmd_list_worktree_present_detection(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-present"
    _git(repo, "worktree", "add", "-b", "dirtywork/present", str(wt), "HEAD")
    _write_run(tmp_path / "runs", "present", {
        "status": "completed", "started": "t", "branch": "dirtywork/present", "repo": str(repo),
        "worktree": str(wt), "container": None, "volume": None,
    })
    rc = runs.cmd_list(argparse.Namespace(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["worktree"] == "yes"


def test_cmd_list_docker_state_columns(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")

    def fake_run(argv, timeout=None):
        if argv[:2] == ["ps", "-a"]:
            return FakeCaptured(0, b"dw-slug1\texited\n")
        if argv[:2] == ["volume", "ls"]:
            return FakeCaptured(0, b"dw-slug1-work\n")
        return FakeCaptured(1)

    monkeypatch.setattr(runs.docker_cli, "run", fake_run)
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "started": "t", "branch": "b", "repo": str(repo),
        "worktree": str(repo), "container": "dw-slug1", "volume": "dw-slug1-work",
    })
    assert runs.cmd_list(argparse.Namespace(json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["container"] == "exited"
    assert payload[0]["volume"] == "present"


def test_cmd_list_docker_query_failure_is_non_fatal(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")

    def fake_run(*a, **k):
        raise RuntimeError("docker not installed")

    monkeypatch.setattr(runs.docker_cli, "run", fake_run)
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "started": "t", "branch": "b", "repo": str(repo),
        "worktree": str(repo), "container": "dw-slug1", "volume": "dw-slug1-work",
    })
    rc = runs.cmd_list(argparse.Namespace(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["container"] == "-"     # best effort, never fatal
    assert payload[0]["volume"] == "absent"


def test_cmd_list_unreadable_run_json_is_a_row_not_a_crash(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = (tmp_path / "runs" / "broken")
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text("{not json")
    rc = runs.cmd_list(argparse.Namespace(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["slug"] == "broken"
    assert payload[0]["status"] == "?"
    assert "error" in payload[0]


def test_cmd_show_prints_summary_run_json_and_timeline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {
        "status": "stalled", "slug": "slug1", "task": "fix the bug",
        "model": "qwen/qwen3-coder-next", "provider": "openai", "turns": 12,
        "resumed_from": "older-run", "resumed_by": None, "sandbox": "docker",
    })
    (run_dir / "transcript.jsonl").write_text(
        json.dumps({"ts": "t1", "event": "run_start", "model": "qwen/qwen3-coder-next"}) + "\n"
        + json.dumps({"ts": "t2", "event": "tool_result", "tool": "bash",
                      "args": "{\"command\": \"ls\"}", "result": "exit code: 0"}) + "\n"
        + json.dumps({"ts": "t3", "event": "nudge", "kind": "stall", "turn": 6}) + "\n"
        + json.dumps({"ts": "t4", "event": "run_end", "status": "stalled", "turns": 12}) + "\n"
    )
    rc = runs.cmd_show(argparse.Namespace(slug="slug1", diff=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "task: fix the bug" in out
    assert "model: qwen/qwen3-coder-next" in out
    assert "provider: openai" in out
    assert "turns: 12" in out
    assert "resumed_from: older-run" in out
    assert "resumed_by: -" in out
    assert '"status": "stalled"' in out          # the full run.json is still printed
    assert "timeline:" in out
    assert "kind=stall" in out                   # nudge events are visible in the timeline
    assert "bash" in out


def test_cmd_show_unknown_slug_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    rc = runs.cmd_show(argparse.Namespace(slug="nope", diff=False))
    assert rc == 2
    assert "no such run" in capsys.readouterr().err


def test_cmd_show_diff_prints_patch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {"status": "completed"})
    (run_dir / "diff.patch").write_text("--- a/x\n+++ b/x\n")
    rc = runs.cmd_show(argparse.Namespace(slug="slug1", diff=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "diff:" in out
    assert "--- a/x" in out


def test_cmd_show_diff_missing_patch_notes_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {"status": "completed"})
    rc = runs.cmd_show(argparse.Namespace(slug="slug1", diff=True))
    assert rc == 0
    assert "no diff.patch" in capsys.readouterr().out
