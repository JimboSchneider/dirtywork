from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dirtywork import rundir, runs
from dirtywork.budget import DEFAULT_MAX_WORKTREE_FILES, DEFAULT_MAX_WORKTREE_MB
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


def _export_args(slug, **over):
    args = dict(slug=slug, max_patch_mb=10, keep_volume=False,
               max_worktree_mb=DEFAULT_MAX_WORKTREE_MB,
               max_worktree_files=DEFAULT_MAX_WORKTREE_FILES)
    args.update(over)
    return argparse.Namespace(**args)


def test_cmd_export_not_docker_sandbox_rejected(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "hostrun", {"status": "completed", "sandbox": "none"})
    rc = runs.cmd_export(_export_args("hostrun"))
    assert rc == 2
    assert "not a docker-sandbox run" in capsys.readouterr().err


def test_cmd_export_live_run_rejected(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    _docker_run_json(tmp_path / "runs", "slug1", repo, wt,
                     status="running", host_pid=os.getpid())
    rc = runs.cmd_export(_export_args("slug1"))
    assert rc == 2
    assert "still running" in capsys.readouterr().err


def test_cmd_export_non_empty_worktree_rejected(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    (wt / "already-exported.txt").write_text("x")
    _docker_run_json(tmp_path / "runs", "slug1", repo, wt)
    rc = runs.cmd_export(_export_args("slug1"))
    assert rc == 2
    assert "not empty" in capsys.readouterr().err


def test_cmd_export_missing_volume_exits_2(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    _docker_run_json(tmp_path / "runs", "slug1", repo, wt)
    monkeypatch.setattr(runs.docker_cli, "run", lambda *a, **k: FakeCaptured(1))
    rc = runs.cmd_export(_export_args("slug1"))
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
    rc = runs.cmd_export(_export_args("slug1"))
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
    rc = runs.cmd_export(_export_args("slug1"))
    assert rc == 0
    data = json.loads((run_dir / "run.json").read_text())
    assert data["export_status"] == "ok"
    assert data["status"] == "budget_exceeded"   # why the run ended is not rewritten


def test_cmd_export_failure_reports_and_returns_1(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    run_dir = _docker_run_json(tmp_path / "runs", "slug1", repo, wt, status="completed")
    _export_ok(monkeypatch, RunArtifacts(export_status="export_failed: archive too large"))
    rc = runs.cmd_export(_export_args("slug1"))
    assert rc == 1
    assert "export failed" in capsys.readouterr().err
    data = json.loads((run_dir / "run.json").read_text())
    assert data["status"] == "export_failed"


def test_cmd_export_passes_worktree_limits_into_cfg(tmp_path, repo, monkeypatch, capsys):
    # E1: --max-worktree-mb/--max-worktree-files must reach the DockerConfig
    # export_run builds, not just the defaults baked into DockerConfig itself.
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = _empty_worktree(repo, "slug1")
    _docker_run_json(tmp_path / "runs", "slug1", repo, wt)
    monkeypatch.setattr(runs.docker_cli, "run", lambda *a, **k: FakeCaptured(0))
    monkeypatch.setattr(runs.docker_cli, "validate_objects_dir",
                        lambda repo: Path(repo) / ".git" / "objects")
    monkeypatch.setattr(runs.docker_cli, "resolve_image",
                        lambda image, **kw: f"sha256:{'a' * 64}")
    seen_cfg = []

    def fake_export_run(cfg, **kw):
        seen_cfg.append(cfg)
        return RunArtifacts(export_status="ok")

    monkeypatch.setattr(runs.export, "export_run", fake_export_run)
    rc = runs.cmd_export(_export_args("slug1", max_worktree_mb=777, max_worktree_files=123))
    assert rc == 0
    assert seen_cfg[0].max_worktree_mb == 777
    assert seen_cfg[0].max_worktree_files == 123


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


def test_clean_completed_docker_run_with_resources_already_gone_removes_run_dir(tmp_path, repo, monkeypatch, capsys):
    # The normal end state of a completed docker run: sandbox.stop() already
    # removed the container and volume. That is not a refusal -- the run dir
    # goes, and the exit code is 0 without --force.
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": None,
        "container": "dw-slug1", "volume": "dw-slug1-work", "branch": None,
    })
    monkeypatch.setattr(runs.docker_cli, "run",
                        lambda argv, timeout=None: FakeCaptured(1, b"Error: No such object"))
    rc = runs.cmd_clean(_clean_args("slug1"))
    out = capsys.readouterr().out
    assert "absent-container" in out and "absent-volume" in out
    assert "kept-run-dir" not in out
    assert not (tmp_path / "runs" / "slug1").exists()
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
    run_dir = _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": str(wt),
        "container": None, "volume": None, "branch": "dirtywork/slug1",
    })
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
    out = capsys.readouterr().out
    assert "uncommitted changes" in out
    assert wt.exists()
    # B4: a skip anywhere in the run's log means the run dir is kept too --
    # this test now also covers that (was: only checked the worktree survived).
    assert "kept-run-dir" in out
    assert run_dir.exists()
    assert (run_dir / "run.json").exists()
    assert rc == 1


def test_clean_refuses_branch_with_commits_beyond_base_without_force(tmp_path, repo, monkeypatch, capsys):
    # B1: even a CLEAN worktree must not be force-deleted if its branch carries
    # commits beyond the run's base_commit -- an --allow-commit run's real work
    # would otherwise be silently lost (the dirty-worktree check alone misses this
    # since a committed change leaves the worktree clean).
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    wt = repo / ".worktrees" / "dw-slug1"
    _git(repo, "worktree", "add", "-b", "dirtywork/slug1", str(wt), "HEAD")
    (wt / "new.txt").write_text("work")
    _git(wt, "add", "new.txt")
    _git(wt, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "work")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": str(wt),
        "container": None, "volume": None, "branch": "dirtywork/slug1",
        "base_commit": base,
    })
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
    out = capsys.readouterr().out
    assert "commit(s) beyond base" in out
    assert wt.exists()
    assert "kept-run-dir" in out
    assert rc == 1


def test_clean_force_removes_branch_with_commits_beyond_base(tmp_path, repo, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    wt = repo / ".worktrees" / "dw-slug1"
    _git(repo, "worktree", "add", "-b", "dirtywork/slug1", str(wt), "HEAD")
    (wt / "new.txt").write_text("work")
    _git(wt, "add", "new.txt")
    _git(wt, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "work")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": str(wt),
        "container": None, "volume": None, "branch": "dirtywork/slug1",
        "base_commit": base,
    })
    rc = runs.cmd_clean(_clean_args("slug1", force=True))
    out = capsys.readouterr().out
    assert "removed-worktree" in out
    assert "removed-branch" in out
    assert not wt.exists()
    assert rc == 0


def test_clean_skips_branch_delete_when_worktree_checked_out_a_different_branch(
        tmp_path, repo, monkeypatch, capsys):
    # B2: run.json is data, not authority -- `git branch -D` must only ever
    # target the branch the worktree actually had checked out, read BEFORE
    # removal via `git worktree list --porcelain`.
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    wt = repo / ".worktrees" / "dw-slug1"
    _git(repo, "worktree", "add", "-b", "dirtywork/actual", str(wt), "HEAD")
    _git(wt, "checkout", "-b", "dirtywork/other")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": str(wt),
        "container": None, "volume": None, "branch": "dirtywork/actual",
        "base_commit": base,
    })
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
    out = capsys.readouterr().out
    assert "removed-worktree" in out
    assert "skip-branch" in out
    assert "not the branch checked out" in out
    assert not wt.exists()
    assert "dirtywork/actual" in _git(repo, "branch", "--list", "dirtywork/actual").stdout
    assert rc == 1                     # skip-branch is a "skip" -> exit 1, run dir kept
    assert "kept-run-dir" in out


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


def test_clean_refuses_worktree_that_is_not_a_linked_worktree_of_the_repo(tmp_path, repo, monkeypatch, capsys):
    # run.json is data, not authority: a recorded worktree that is not a linked
    # worktree of the recorded repo (here: a plain directory with a file in it,
    # e.g. a user's own checkout or a corrupted record) must never be force-removed.
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    stray = tmp_path / "not-a-worktree"
    stray.mkdir()
    (stray / "precious.txt").write_text("keep me")
    head_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": str(stray),
        "container": None, "volume": None, "branch": head_branch,
    })
    rc = runs.cmd_clean(_clean_args("slug1", force=True))
    out = capsys.readouterr().out
    assert "not a dirtywork-managed worktree" in out
    assert (stray / "precious.txt").exists()
    assert head_branch in _git(repo, "branch", "--list", head_branch).stdout
    assert rc == 1


def test_clean_refuses_another_linked_worktree_of_the_repo(tmp_path, repo, monkeypatch, capsys):
    # A run.json edited to point at a linked worktree dirtywork did NOT create
    # (here: <repo>/.worktrees/feature -- e.g. the operator's own worktree) must
    # be refused even though it IS a linked worktree of the repo.
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    other = repo / ".worktrees" / "feature"
    _git(repo, "worktree", "add", "-b", "feature", str(other), "HEAD")
    (other / "work.txt").write_text("mine")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": str(other),
        "container": None, "volume": None, "branch": "feature",
    })
    rc = runs.cmd_clean(_clean_args("slug1", force=True))
    out = capsys.readouterr().out
    assert "not a dirtywork-managed worktree" in out
    assert (other / "work.txt").exists()
    assert "feature" in _git(repo, "branch", "--list", "feature").stdout
    assert rc == 1


def test_clean_docker_daemon_failure_is_a_refusal_and_keeps_worktree_and_run_dir(tmp_path, repo, monkeypatch, capsys):
    # `docker inspect` failing for any reason other than "no such object"
    # (daemon down, permission denied) means we could not verify the resource is
    # gone: refuse, and remove NOTHING else, so a retry can still finish.
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-slug1"
    _git(repo, "worktree", "add", "-b", "dirtywork/slug1", str(wt), "HEAD")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": str(wt),
        "container": "dw-slug1", "volume": "dw-slug1-work", "branch": "dirtywork/slug1",
    })
    monkeypatch.setattr(runs.docker_cli, "run",
                        lambda argv, timeout=None: FakeCaptured(1, b"Cannot connect to the Docker daemon at unix:///var/run/docker.sock"))
    rc = runs.cmd_clean(_clean_args("slug1", force=True))
    out = capsys.readouterr().out
    assert "skip-container" in out and "cannot inspect" in out
    assert "kept-worktree" in out and "kept-run-dir" in out
    assert wt.exists()
    assert (tmp_path / "runs" / "slug1").exists()
    assert rc == 1


@pytest.mark.parametrize("output", [
    b"Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
    b"error during connect: Get \"http://%2Fvar%2Frun%2Fdocker.sock/v1.47/containers/x/json\": dial unix /var/run/docker.sock: connect: no such file or directory",
    b"permission denied while trying to connect to the Docker daemon socket",
])
def test_clean_daemon_down_messages_are_refusals_even_when_they_say_no_such(tmp_path, repo, monkeypatch, capsys, output):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": None,
        "container": "dw-slug1", "volume": None, "branch": None,
    })
    monkeypatch.setattr(runs.docker_cli, "run", lambda argv, timeout=None: FakeCaptured(1, output))
    rc = runs.cmd_clean(_clean_args("slug1", force=True))
    out = capsys.readouterr().out
    assert "skip-container" in out and "absent-container" not in out
    assert (tmp_path / "runs" / "slug1").exists()
    assert rc == 1


@pytest.mark.parametrize("output", [
    b"Error: No such object: dw-slug1",
    b"Error response from daemon: get dw-slug1-work: no such volume",
    b"Error: No such container: dw-slug1",
])
def test_clean_object_level_no_such_is_absent(tmp_path, repo, monkeypatch, capsys, output):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": None,
        "container": "dw-slug1", "volume": None, "branch": None,
    })
    monkeypatch.setattr(runs.docker_cli, "run", lambda argv, timeout=None: FakeCaptured(1, output))
    rc = runs.cmd_clean(_clean_args("slug1"))
    out = capsys.readouterr().out
    assert "absent-container" in out
    assert not (tmp_path / "runs" / "slug1").exists()
    assert rc == 0


def test_clean_already_gone_worktree_is_absent_not_a_refusal(tmp_path, repo, monkeypatch, capsys):
    # A worktree removed by hand (or by an earlier partial clean) must not strand
    # the run record: prune git's bookkeeping, drop dirtywork's own orphaned
    # branch, remove the run dir, exit 0.
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-slug1"
    _git(repo, "worktree", "add", "-b", "dirtywork/slug1", str(wt), "HEAD")
    shutil.rmtree(wt)
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": str(wt),
        "container": None, "volume": None, "branch": "dirtywork/slug1",
    })
    rc = runs.cmd_clean(_clean_args("slug1"))
    out = capsys.readouterr().out
    assert "absent-worktree" in out
    assert "removed-branch" in out
    assert "dirtywork/slug1" not in _git(repo, "branch", "--list", "dirtywork/slug1").stdout
    assert not (tmp_path / "runs" / "slug1").exists()
    assert rc == 0


@pytest.mark.parametrize("bad", ["../escape", "/etc", ".", "..", "a/b", "", "-x"])
def test_open_run_rejects_slugs_that_are_not_plain_names(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    (tmp_path / "escape").mkdir()
    (tmp_path / "escape" / "run.json").write_text("{}")
    with pytest.raises(runs.RunsError):
        runs._open_run(bad)


def test_clean_with_escaping_slug_touches_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "run.json").write_text(json.dumps({"status": "completed", "repo": str(tmp_path),
                                                 "worktree": None, "container": None,
                                                 "volume": None, "branch": None}))
    rc = runs.cmd_clean(_clean_args("../victim", force=True))
    out = capsys.readouterr().out
    assert "invalid run slug" in out
    assert (victim / "run.json").exists()
    assert rc == 1


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


def test_clean_keeps_stash_when_worktree_removal_refused_removes_it_with_force(
        tmp_path, repo, monkeypatch, capsys):
    # B3: a stash beside a worktree that clean refused to remove must survive
    # too (it may still be needed to recover that worktree's content) --
    # unless --force is given, which removes both together.
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    wt = repo / ".worktrees" / "dw-slug1"
    _git(repo, "worktree", "add", "-b", "dirtywork/slug1", str(wt), "HEAD")
    (wt / "dirty.txt").write_text("uncommitted")
    stash = stash_dir_for(wt, "slug1")
    stash.mkdir()
    (stash / "kept.txt").write_text("prior content")
    _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "repo": str(repo), "worktree": str(wt),
        "container": None, "volume": None, "branch": "dirtywork/slug1",
    })
    rc = runs.cmd_clean(_clean_args("slug1", keep_transcript=True))
    out = capsys.readouterr().out
    assert "kept-stash" in out
    assert stash.exists()
    assert rc == 1

    rc2 = runs.cmd_clean(_clean_args("slug1", force=True))
    out2 = capsys.readouterr().out
    assert "removed-stash" in out2
    assert not stash.exists()
    assert rc2 == 0


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


def test_cmd_verdict_records_fields(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "ended": "2026-08-16T00:00:00+00:00",
    })
    rc = runs.cmd_verdict(argparse.Namespace(slug="slug1", verdict="accept",
                                             note="looks good", review_seconds=42))
    assert rc == 0
    data = json.loads((run_dir / "run.json").read_text())
    assert data["verdict"] == "accept"
    assert data["note"] == "looks good"
    assert data["review_seconds"] == 42
    assert "verdict_at" in data
    assert data["time_to_verdict_s"] >= 0
    assert data["status"] == "completed"        # the run's own fields are untouched


def test_cmd_verdict_missing_ended_leaves_time_to_verdict_none(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {"status": "running"})
    rc = runs.cmd_verdict(argparse.Namespace(slug="slug1", verdict="cleanup",
                                             note=None, review_seconds=None))
    assert rc == 0
    data = json.loads((run_dir / "run.json").read_text())
    assert data["time_to_verdict_s"] is None


def test_cmd_verdict_naive_ended_timestamp_assumes_utc(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "slug1", {
        "status": "completed", "ended": "2026-08-16T00:00:00",   # no tz offset
    })
    rc = runs.cmd_verdict(argparse.Namespace(slug="slug1", verdict="accept",
                                             note=None, review_seconds=None))
    assert rc == 0
    data = json.loads((run_dir / "run.json").read_text())
    assert data["time_to_verdict_s"] is not None
    assert data["time_to_verdict_s"] >= 0


def test_cmd_verdict_unknown_slug_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    rc = runs.cmd_verdict(argparse.Namespace(slug="nope", verdict="reject",
                                             note=None, review_seconds=None))
    assert rc == 2
    assert "no such run" in capsys.readouterr().err


FENCE = "`" * 3


def _markdown_run(tmp_path, **over):
    """A run dir with a two-turn transcript: a nudge, a guardrail_block, a
    finish call, and a run_end carrying usage."""
    data = {
        "status": "completed", "slug": "md1", "task": "fix the off-by-one",
        "model": "qwen/qwen3-coder-next", "provider": "openai", "turns": 2,
        "sandbox": "docker", "base_commit": "abc1234", "branch": "dirtywork/md1",
        "worktree": "/repo/.worktrees/dw-md1", "resumed_from": None, "resumed_by": None,
        "diff_stat": " x.py | 2 +-\n 1 file changed", "export_status": "ok",
        "verdict": "accept", "note": "clean patch",
    }
    data.update(over)
    run_dir = _write_run(tmp_path / "runs", "md1", data)
    (run_dir / "transcript.jsonl").write_text(
        json.dumps({"ts": "t0", "event": "run_start", "model": "qwen/qwen3-coder-next"}) + "\n"
        + json.dumps({"ts": "t1", "event": "assistant", "text": "Looking at the file.",
                      "tool_calls": [{"name": "bash", "arguments": "{}"}]}) + "\n"
        + json.dumps({"ts": "t2", "event": "tool_result", "tool": "bash",
                      "args": "{\"command\": \"rm -rf /\"}",
                      "result": "BLOCKED: refusing <destructive> command"}) + "\n"
        + json.dumps({"ts": "t3", "event": "guardrail_block", "tool": "bash",
                      "args": {"command": "rm -rf /"},
                      "reason": "BLOCKED: refusing <destructive> command"}) + "\n"
        + json.dumps({"ts": "t4", "event": "nudge", "kind": "stall", "turn": 1}) + "\n"
        + json.dumps({"ts": "t5", "event": "assistant", "text": "Fixed it.",
                      "tool_calls": [{"name": "finish", "arguments": "{}"}]}) + "\n"
        + json.dumps({"ts": "t6", "event": "tool_result", "tool": "finish",
                      "args": "{\"summary\": \"off-by-one corrected\"}",
                      "result": "run finished"}) + "\n"
        + json.dumps({"ts": "t7", "event": "run_end", "status": "completed", "turns": 2,
                      "usage": {"prompt_tokens": 1234, "completion_tokens": 56}}) + "\n"
    )
    return run_dir


def test_cmd_show_markdown_renders_document(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _markdown_run(tmp_path)
    rc = runs.cmd_show(argparse.Namespace(slug="md1", diff=False, markdown=True, out=None))
    assert rc == 0
    out = capsys.readouterr().out
    # header block
    assert out.startswith("# md1\n")
    assert "- **task:** fix the off-by-one" in out
    assert "## Task\n" in out and "(full text below)" not in out
    assert "- **model:** qwen/qwen3-coder-next" in out
    assert "- **provider:** openai" in out
    assert "- **base_commit:** abc1234" in out
    assert "- **branch:** dirtywork/md1" in out
    assert "- **prompt_tokens:** 1234" in out
    assert "- **completion_tokens:** 56" in out
    assert "- **verdict:** accept" in out
    assert "- **note:** clean patch" in out
    # one section per assistant turn, in order
    assert "## Timeline" in out
    assert "### Turn 1" in out
    assert "### Turn 2" in out
    assert out.index("### Turn 1") < out.index("### Turn 2")
    assert "Looking at the file." in out
    # tool calls become collapsible blocks with the result in a fenced block,
    # where text is literal and therefore NOT html-escaped
    assert "<details>" in out and "</details>" in out
    assert "<summary>bash(" in out
    assert "BLOCKED: refusing <destructive> command" in out
    assert FENCE in out
    # harness events become blockquote callouts -- inline context, so escaped
    assert "> **nudge**" in out
    assert "> **guardrail_block**" in out
    assert "refusing &lt;destructive&gt; command" in out
    # result block
    assert "## Result" in out
    assert "- **status:** completed" in out
    assert "- **export_status:** ok" in out
    assert "1 file changed" in out
    assert "off-by-one corrected" in out          # final message from the finish call


def test_cmd_show_markdown_out_writes_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _markdown_run(tmp_path)
    target = tmp_path / "report.md"
    rc = runs.cmd_show(argparse.Namespace(slug="md1", diff=False, markdown=True,
                                          out=str(target)))
    assert rc == 0
    text = target.read_text()
    assert text.startswith("# md1\n")
    assert "### Turn 2" in text
    assert str(target) in capsys.readouterr().out   # the path is reported, not the document


def test_cmd_show_markdown_diff_embeds_fenced_patch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _markdown_run(tmp_path)
    (run_dir / "diff.patch").write_text("--- a/x.py\n+++ b/x.py\n@@\n-1\n+2\n")
    rc = runs.cmd_show(argparse.Namespace(slug="md1", diff=True, markdown=True, out=None))
    assert rc == 0
    out = capsys.readouterr().out
    assert "## Diff" in out
    assert FENCE + "diff" in out
    assert "--- a/x.py" in out


def test_cmd_show_out_without_markdown_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    _markdown_run(tmp_path)
    rc = runs.cmd_show(argparse.Namespace(slug="md1", diff=False, markdown=False,
                                          out=str(tmp_path / "x.md")))
    assert rc == 2
    assert "--out requires --markdown" in capsys.readouterr().err


def test_cmd_show_markdown_caps_long_results_and_survives_inner_fences(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = _write_run(tmp_path / "runs", "md1", {"status": "completed", "slug": "md1"})
    huge = FENCE + "\n" + ("x" * 5000)
    (run_dir / "transcript.jsonl").write_text(
        json.dumps({"ts": "t1", "event": "assistant", "text": "", "tool_calls": []}) + "\n"
        + json.dumps({"ts": "t2", "event": "tool_result", "tool": "read_file",
                      "args": "{\"path\": \"big.txt\"}", "result": huge}) + "\n"
    )
    rc = runs.cmd_show(argparse.Namespace(slug="md1", diff=False, markdown=True, out=None))
    assert rc == 0
    out = capsys.readouterr().out
    assert "x" * 100 in out
    assert "x" * 4000 not in out                  # capped at MD_RESULT_CHARS
    assert "[truncated]" in out
    assert FENCE + "`" in out                     # fence widened past the inner fence


def test_cmd_show_markdown_prints_the_full_task_text(tmp_path, monkeypatch, capsys):
    # A long task is previewed in the header (no "(full text below)" -- there is
    # no JSON dump in Markdown mode) and printed in full under "## Task".
    monkeypatch.setattr(rundir, "RUNS_DIR", tmp_path / "runs")
    long_task = "line one of a long task\n" + ("x" * 300) + "\nlast line"
    _write_run(tmp_path / "runs", "mdlong", {"status": "completed", "task": long_task,
                                             "model": "m", "provider": "openai"})
    (tmp_path / "runs" / "mdlong" / "transcript.jsonl").write_text("")
    rc = runs.cmd_show(argparse.Namespace(slug="mdlong", diff=False, markdown=True, out=None))
    out = capsys.readouterr().out
    assert rc == 0
    assert "(full text below)" not in out
    assert "## Task" in out
    assert long_task in out                     # verbatim, newlines intact
    assert "- **task:** line one of a long task" in out and " ..." in out

