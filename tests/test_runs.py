from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dirtywork import rundir, runs
from dirtywork.resume import stash_dir_for
from dirtywork.sandbox import RunArtifacts, docker_args

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


def _docker_run_json(runs_dir: Path, slug: str, repo: Path, worktree: Path, **over):
    data = {
        "status": "export_failed", "sandbox": "docker", "slug": slug,
        "repo": str(repo), "worktree": str(worktree), "base_commit": "abc123",
        "volume": f"dw-{slug}-work", "container": f"dw-{slug}",
        "image": "ghcr.io/jimboschneider/dirtywork-worker:0.5", "host_pid": 999999,
    }
    data.update(over)
    return _write_run(runs_dir, slug, data)


def _empty_worktree(repo: Path, slug: str) -> Path:
    """A worktree in the state export_run requires: the .git file and nothing else."""
    wt = repo / ".worktrees" / f"dw-{slug}"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: /nowhere\n")
    return wt


def _export_ok(monkeypatch, artifacts):
    monkeypatch.setattr(runs.docker_cli, "run", lambda *a, **k: FakeCaptured(0))
    monkeypatch.setattr(runs.docker_cli, "validate_objects_dir",
                        lambda repo: Path(repo) / ".git" / "objects")
    monkeypatch.setattr(runs.docker_cli, "resolve_image",
                        lambda image, **kw: f"sha256:{'a' * 64}")
    monkeypatch.setattr(runs.export, "export_run", lambda cfg, **kw: artifacts)


def test_cmd_export_not_docker_sandbox_rejected(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "hostrun", {"status": "completed", "sandbox": "none"})
    rc = runs.cmd_export(argparse.Namespace(slug="hostrun", max_patch_mb=10, keep_volume=False))
    assert rc == 2
    assert "not a docker-sandbox run" in capsys.readouterr().err


def test_cmd_export_live_run_rejected(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    _docker_run_json(tmp_path / "runs", "slug1", repo, wt,
                     status="running", host_pid=os.getpid())
    rc = runs.cmd_export(argparse.Namespace(slug="slug1", max_patch_mb=10, keep_volume=False))
    assert rc == 2
    assert "still running" in capsys.readouterr().err


def test_cmd_export_non_empty_worktree_rejected(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    (wt / "already-exported.txt").write_text("x")
    _docker_run_json(tmp_path / "runs", "slug1", repo, wt)
    rc = runs.cmd_export(argparse.Namespace(slug="slug1", max_patch_mb=10, keep_volume=False))
    assert rc == 2
    assert "not empty" in capsys.readouterr().err


def test_cmd_export_missing_volume_exits_2(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    _docker_run_json(tmp_path / "runs", "slug1", repo, wt)
    monkeypatch.setattr(runs.docker_cli, "run", lambda *a, **k: FakeCaptured(1))
    rc = runs.cmd_export(argparse.Namespace(slug="slug1", max_patch_mb=10, keep_volume=False))
    assert rc == 2
    assert "does not exist" in capsys.readouterr().err


def test_cmd_export_success_updates_run_json(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    run_dir = _docker_run_json(tmp_path / "runs", "slug1", repo, wt)
    _export_ok(monkeypatch, RunArtifacts(diff_stat=" 1 file changed",
                                         patch_path=str(run_dir / "diff.patch"),
                                         worktree_bytes=100, worktree_files=1,
                                         export_status="ok"))
    rc = runs.cmd_export(argparse.Namespace(slug="slug1", max_patch_mb=10, keep_volume=False))
    assert rc == 0
    assert "exported 'slug1'" in capsys.readouterr().out
    data = json.loads((run_dir / "run.json").read_text())
    assert data["export_status"] == "ok"
    assert data["diff_stat"] == " 1 file changed"
    assert data["status"] == "completed"       # export_failed -> completed


def test_cmd_export_success_keeps_a_non_export_status(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    run_dir = _docker_run_json(tmp_path / "runs", "slug1", repo, wt, status="budget_exceeded")
    _export_ok(monkeypatch, RunArtifacts(export_status="ok"))
    rc = runs.cmd_export(argparse.Namespace(slug="slug1", max_patch_mb=10, keep_volume=False))
    assert rc == 0
    data = json.loads((run_dir / "run.json").read_text())
    assert data["export_status"] == "ok"
    assert data["status"] == "budget_exceeded"   # why the run ended is not rewritten


def test_cmd_export_failure_reports_and_returns_1(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    run_dir = _docker_run_json(tmp_path / "runs", "slug1", repo, wt, status="completed")
    _export_ok(monkeypatch, RunArtifacts(export_status="export_failed: archive too large"))
    rc = runs.cmd_export(argparse.Namespace(slug="slug1", max_patch_mb=10, keep_volume=False))
    assert rc == 1
    assert "export failed" in capsys.readouterr().err
    data = json.loads((run_dir / "run.json").read_text())
    assert data["status"] == "export_failed"


def _fake_docker_run(container_label=None, volume_label=None, rm_ok=True):
    """container_label/volume_label are the `<run>\t<repo>` label pair the fake
    `docker inspect --format` prints; None means 'no such object'."""
    def _run(argv, timeout=None):
        if argv[:1] == ["inspect"]:
            return FakeCaptured(1) if container_label is None else FakeCaptured(
                0, container_label.encode())
        if argv[:2] == ["volume", "inspect"]:
            return FakeCaptured(1) if volume_label is None else FakeCaptured(
                0, volume_label.encode())
        if argv[:1] == ["rm"] or argv[:2] == ["volume", "rm"]:
            return FakeCaptured(0 if rm_ok else 1)
        return FakeCaptured(1)
    return _run


def _clean_args(slug=None, all=False, keep_transcript=False, force=False):
    return argparse.Namespace(slug=slug, all=all, keep_transcript=keep_transcript, force=force)


def test_clean_skips_unlabeled_container(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": None,
        "container": "dw-slug1", "volume": None, "branch": None,
    })
    monkeypatch.setattr(runs.docker_cli, "run",
                        _fake_docker_run(container_label="other-slug\twrong-repo-label"))
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
    out = capsys.readouterr().out
    assert "labels do not match" in out
    assert rc == 1


def test_clean_skips_not_owned_by_current_user(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": None,
        "container": None, "volume": None,
    })
    real_uid = os.getuid()
    # capture the real uid FIRST: the lambda must not call the patched getuid
    monkeypatch.setattr(runs.os, "getuid", lambda: real_uid + 1)
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
    assert "not owned by the current user" in capsys.readouterr().out
    assert rc == 1


def test_clean_skips_running_with_alive_pid(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "running", "host_pid": os.getpid(), "repo": str(repo),
        "worktree": None, "container": None, "volume": None,
    })
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
    assert "host process" in capsys.readouterr().out
    assert rc == 1


def test_clean_refuses_dead_pid_without_force(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "running", "host_pid": 999999, "repo": str(repo),
        "worktree": None, "container": None, "volume": None,
    })
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
    assert "dead host process" in capsys.readouterr().out
    assert rc == 1


def test_clean_removes_dead_pid_with_force(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {
        "status": "running", "host_pid": 999999, "repo": str(repo),
        "worktree": None, "container": None, "volume": None, "branch": None,
    })
    rc = runs.cmd_clean(_clean_args("slug1", force=True))
    assert "removed-rundir" in capsys.readouterr().out
    assert not run_dir.exists()
    assert rc == 0


def test_clean_removes_matching_container_and_volume(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    label = docker_args.repo_label(repo)
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": None,
        "container": "dw-slug1", "volume": "dw-slug1-work", "branch": None,
    })
    monkeypatch.setattr(runs.docker_cli, "run", _fake_docker_run(
        container_label=f"slug1\t{label}", volume_label=f"slug1\t{label}"))
    rc = runs.cmd_clean(_clean_args("slug1"))
    out = capsys.readouterr().out
    assert "removed-container: dw-slug1" in out
    assert "removed-volume: dw-slug1-work" in out
    assert rc == 0


def test_clean_refuses_dirty_worktree_without_force(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-slug1"
    _git(repo, "worktree", "add", "-b", "dirtywork/slug1", str(wt), "HEAD")
    (wt / "dirty.txt").write_text("uncommitted")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": str(wt),
        "container": None, "volume": None, "branch": "dirtywork/slug1",
    })
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
    out = capsys.readouterr().out
    assert "uncommitted changes" in out
    assert wt.exists()
    assert rc == 1


def test_clean_force_removes_dirty_worktree_and_branch(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-slug1"
    _git(repo, "worktree", "add", "-b", "dirtywork/slug1", str(wt), "HEAD")
    (wt / "dirty.txt").write_text("uncommitted")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": str(wt),
        "container": None, "volume": None, "branch": "dirtywork/slug1",
    })
    rc = runs.cmd_clean(_clean_args("slug1", force=True))
    out = capsys.readouterr().out
    assert "removed-worktree" in out
    assert "removed-branch" in out
    assert not wt.exists()
    assert rc == 0
    assert "dirtywork/slug1" not in _git(repo, "branch", "--list", "dirtywork/slug1").stdout


def test_clean_removes_the_runs_pre_resume_stash(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-prior"
    _git(repo, "worktree", "add", "-b", "dirtywork/prior", str(wt), "HEAD")
    stash = stash_dir_for(wt, "slug1")
    stash.mkdir()
    (stash / "kept.txt").write_text("prior content")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": str(wt),
        "container": None, "volume": None, "branch": "dirtywork/prior",
    })
    rc = runs.cmd_clean(_clean_args("slug1", force=True))
    out = capsys.readouterr().out
    assert f"removed-stash: {stash}" in out
    assert not stash.exists()
    assert rc == 0


def test_clean_keeps_worktree_shared_with_a_later_resume(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-first"
    _git(repo, "worktree", "add", "-b", "dirtywork/first", str(wt), "HEAD")
    run_dir = _write_run(tmp_path / "runs", "first", {
        "status": "max_turns", "repo": str(repo), "worktree": str(wt),
        "container": None, "volume": None, "branch": "dirtywork/first",
        "resumed_by": "second",
    })
    rc = runs.cmd_clean(_clean_args("first"))
    out = capsys.readouterr().out
    assert "kept-worktree" in out
    assert "second" in out
    assert wt.exists()                                   # the newer run still owns it
    assert "dirtywork/first" in _git(repo, "branch", "--list", "dirtywork/first").stdout
    assert not run_dir.exists()                          # but this run's own dir is gone
    assert rc == 0


def test_clean_keep_transcript_preserves_transcript_and_run_json(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": None,
        "container": None, "volume": None,
    })
    (run_dir / "transcript.jsonl").write_text('{"event": "run_start"}\n')
    (run_dir / "diff.patch").write_text("stuff")
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
    assert rc == 0
    assert (run_dir / "transcript.jsonl").exists()
    assert (run_dir / "run.json").exists()
    assert not (run_dir / "diff.patch").exists()


def test_clean_all_processes_every_run_dir(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    for slug in ("a", "b"):
        _write_run(tmp_path / "runs", slug, {
            "status": "completed", "repo": str(repo), "worktree": None,
            "container": None, "volume": None,
        })
    rc = runs.cmd_clean(_clean_args(all=True))
    assert rc == 0
    assert not (tmp_path / "runs" / "a").exists()
    assert not (tmp_path / "runs" / "b").exists()


def test_clean_unknown_slug_reports_skip(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    rc = runs.cmd_clean(_clean_args("nope", keep_transcript=True))
    assert "no such run" in capsys.readouterr().out
    assert rc == 1
