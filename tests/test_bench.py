from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
from pathlib import Path

from dirtywork import bench
from dirtywork.sandbox import docker_args

from .fake_docker import FakeCaptured

BENCH_REPOS = Path(__file__).resolve().parent.parent / "bench" / "repos"
TASK_NAMES = ["py-fix-off-by-one", "node-add-cli-flag", "sh-fix-script"]


def _bench_json(task_name: str) -> dict:
    return json.loads((BENCH_REPOS / task_name / "bench.json").read_text())


def _local_command(task_dir: Path, command: str) -> str:
    """The container command with /acceptance rewritten to this checkout's copy,
    so the same string can be exercised on the host."""
    return command.replace("/acceptance/", str(task_dir / "acceptance") + "/")


def test_every_task_dir_exists_and_is_tiny():
    assert BENCH_REPOS.is_dir()
    for name in TASK_NAMES:
        task_dir = BENCH_REPOS / name
        assert task_dir.is_dir(), f"missing bench fixture: {name}"
        files = [p for p in task_dir.rglob("*") if p.is_file()]
        assert len(files) <= 5, f"{name} has {len(files)} files, expected <= 5"


def test_bench_json_schema():
    for name in TASK_NAMES:
        data = _bench_json(name)
        assert isinstance(data.get("task"), str) and data["task"]
        acceptance = data.get("acceptance")
        assert isinstance(acceptance, dict)
        assert isinstance(acceptance.get("command"), str) and acceptance["command"]
        assert isinstance(acceptance.get("hashes"), dict) and acceptance["hashes"]


def test_bench_json_hashes_match_files_on_disk():
    for name in TASK_NAMES:
        task_dir = BENCH_REPOS / name
        data = _bench_json(name)
        for rel_path, expected_hash in data["acceptance"]["hashes"].items():
            assert rel_path.startswith("acceptance/"), (
                f"{name}: hashed path '{rel_path}' is not under acceptance/")
            p = task_dir / rel_path
            assert p.is_file(), f"{name}: hashed path '{rel_path}' does not exist"
            actual = hashlib.sha256(p.read_bytes()).hexdigest()
            assert actual == expected_hash, (
                f"{name}: {rel_path} hash mismatch -- recompute the hashes map with "
                f"hashlib.sha256(path.read_bytes()).hexdigest()")


def test_acceptance_commands_use_the_mounted_copy():
    # Spec SP3 section 5: the acceptance command never comes from the worktree.
    # Task 14 mounts acceptance/ read-only at /acceptance, so every command must
    # name that absolute path and never a /work-relative one.
    for name in TASK_NAMES:
        command = _bench_json(name)["acceptance"]["command"]
        assert "/acceptance/" in command, f"{name}: command does not use /acceptance"
        assert not command.startswith("acceptance/"), f"{name}: command is worktree-relative"


def test_task_source_files_are_unsolved():
    # Fixtures ship the BUGGY state -- if the acceptance check already passes
    # against the fixture as committed, the task gives the model nothing to do.
    runtimes = {"py-fix-off-by-one": "python3", "sh-fix-script": "bash",
                "node-add-cli-flag": "node"}
    for name in TASK_NAMES:
        if shutil.which(runtimes[name]) is None:
            continue  # optional runtime not installed in this environment
        task_dir = BENCH_REPOS / name
        command = _local_command(task_dir, _bench_json(name)["acceptance"]["command"])
        result = subprocess.run(shlex.split(command), cwd=str(task_dir),
                                capture_output=True, text=True)
        assert result.returncode != 0, (
            f"{name}: the acceptance check already passes on the unsolved fixture")


def test_parse_model_spec_variants():
    assert bench.parse_model_spec("qwen/qwen3-coder-next") == (
        "qwen/qwen3-coder-next", None, None)
    assert bench.parse_model_spec("qwen/qwen3-coder-next", "openai", "http://localhost:1234/v1") == (
        "qwen/qwen3-coder-next", "openai", "http://localhost:1234/v1")
    assert bench.parse_model_spec("some-model@anthropic", "openai") == (
        "some-model", "anthropic", None)
    assert bench.parse_model_spec("m@openai=http://127.0.0.1:9/v1", "anthropic", "http://x") == (
        "m", "openai", "http://127.0.0.1:9/v1")


def test_hash_check_argv_exact():
    argv = bench._hash_check_argv("dw-slug-work", "sha256:deadbeef", 501, 20,
                                  ["/work/acceptance/check.sh"])
    assert argv == [
        "run", "--rm",
        "--network", "none",
        "--user", "501:20",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", "2g", "--memory-swap", "2g",
        "--cpus", "2",
        "--pids-limit", "256",
        "--tmpfs", "/tmp:rw,exec,size=256m,mode=1777",
        "--mount", "type=volume,src=dw-slug-work,dst=/work",
        "-e", f"PATH={docker_args.PATH_ENV}",
        "-e", "HOME=/tmp",
        "-e", "TMPDIR=/tmp",
        "-e", "LANG=C.UTF-8",
        "--entrypoint", "/usr/bin/sha256sum", "sha256:deadbeef",
        "/work/acceptance/check.sh",
    ]


def test_acceptance_run_argv_exact(tmp_path):
    acceptance_dir = tmp_path / "acceptance"
    acceptance_dir.mkdir()
    argv = bench._acceptance_run_argv("dw-slug-work", "sha256:deadbeef", 501, 20,
                                      acceptance_dir, "bash /acceptance/check.sh")
    assert argv[:2] == ["run", "--rm"]
    # the acceptance container never gets network, whatever the run used
    assert argv[argv.index("--network") + 1] == "none"
    assert "type=volume,src=dw-slug-work,dst=/work" in argv
    assert f"type=bind,src={acceptance_dir.resolve()},dst=/acceptance,readonly" in argv
    assert f"PATH={docker_args.PATH_ENV}" in argv
    assert argv[-5:] == ["--entrypoint", "/bin/sh", "sha256:deadbeef", "-c",
                         "cd /work && bash /acceptance/check.sh"]


def test_stage_repo_creates_unique_committed_git_repos():
    d1 = bench._stage_repo("sh-fix-script")
    d2 = bench._stage_repo("sh-fix-script")
    try:
        assert d1 != d2
        for d in (d1, d2):
            assert (d / "report.sh").exists()
            log = subprocess.run(["git", "-C", str(d), "log", "--oneline"],
                                 capture_output=True, text=True)
            assert log.returncode == 0 and log.stdout.strip()
    finally:
        shutil.rmtree(d1, ignore_errors=True)
        shutil.rmtree(d2, ignore_errors=True)


def _hash_lines(hashes, digest=None):
    return ("\n".join(f"{digest or h}  /work/{p}" for p, h in hashes.items()) + "\n").encode()


def _acceptance_fake(hashes, digest=None, command_rc=0, hash_rc=0):
    def _run(argv, timeout=None):
        if "/usr/bin/sha256sum" in argv:
            return FakeCaptured(hash_rc, _hash_lines(hashes, digest))
        return FakeCaptured(command_rc)
    return _run


def _patch_resolve_image(monkeypatch):
    monkeypatch.setattr(bench.docker_cli, "resolve_image",
                        lambda image, **kw: f"sha256:{'a' * 64}")


def test_run_acceptance_pass(monkeypatch):
    data = bench._bench_json("sh-fix-script")
    _patch_resolve_image(monkeypatch)
    assert bench._run_acceptance("sh-fix-script", data, "dw-x-work",
                                 run=_acceptance_fake(data["acceptance"]["hashes"])) == "pass"


def test_run_acceptance_fail(monkeypatch):
    data = bench._bench_json("sh-fix-script")
    _patch_resolve_image(monkeypatch)
    assert bench._run_acceptance("sh-fix-script", data, "dw-x-work",
                                 run=_acceptance_fake(data["acceptance"]["hashes"],
                                                      command_rc=1)) == "fail"


def test_run_acceptance_gamed_on_hash_mismatch(monkeypatch):
    data = bench._bench_json("sh-fix-script")
    _patch_resolve_image(monkeypatch)
    assert bench._run_acceptance("sh-fix-script", data, "dw-x-work",
                                 run=_acceptance_fake(data["acceptance"]["hashes"],
                                                      digest="0" * 64)) == "gamed"


def test_run_acceptance_gamed_when_a_harness_file_is_missing(monkeypatch):
    data = bench._bench_json("sh-fix-script")
    _patch_resolve_image(monkeypatch)
    # sha256sum exits 1 and prints nothing for a file the worker deleted
    assert bench._run_acceptance("sh-fix-script", data, "dw-x-work",
                                 run=lambda argv, timeout=None: FakeCaptured(1, b"")) == "gamed"


def test_run_acceptance_skipped_when_docker_is_unavailable(monkeypatch):
    data = bench._bench_json("sh-fix-script")

    def boom(*a, **k):
        raise RuntimeError("no docker here")

    monkeypatch.setattr(bench.docker_cli, "resolve_image", boom)
    assert bench._run_acceptance("sh-fix-script", data, "dw-x-work", run=boom) == "skipped"


def _fake_run_environment(tmp_path, monkeypatch, *, payload, transcript_events=(), run_json=None):
    """Wires run_once/_stage_repo/_run_acceptance/docker_cli.run and lays down the
    run dir the real CLI would have produced. Returns the list argv is recorded into."""
    runs_dir = tmp_path / "runs"
    slug = "fixtask-0101-abcd"
    run_dir = runs_dir / slug
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(run_json if run_json is not None
                                                 else {"volume": "dw-fixtask-work",
                                                       "diff_stat": " 1 file changed"}))
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in transcript_events))
    payload = dict(payload)
    payload.setdefault("run_dir", str(run_dir))
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", runs_dir)

    seen = []
    monkeypatch.setattr(bench, "run_once", lambda argv: seen.append(argv) or payload)
    staged = tmp_path / "staged"
    staged.mkdir()
    monkeypatch.setattr(bench, "_stage_repo", lambda task: staged)
    monkeypatch.setattr(bench, "_run_acceptance", lambda *a, **k: "pass")
    monkeypatch.setattr(bench.docker_cli, "run",
                        lambda argv, timeout=None: seen.append(argv) or FakeCaptured(0))
    return seen


def test_run_one_bench_case_argv_and_row(tmp_path, monkeypatch):
    seen = _fake_run_environment(tmp_path, monkeypatch, payload={
        "status": "completed", "turns": 3,
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        "provider": "openai"})
    row = bench.run_one_bench_case("m1", "sh-fix-script", 0, provider="openai",
                                   base_url="http://127.0.0.1:9/v1", stamp="20260816T000000Z",
                                   max_turns=40, timeout=1800)
    run_argv = seen[0]
    assert run_argv[0] == "run"
    assert run_argv[1] == bench._bench_json("sh-fix-script")["task"]
    assert "--sandbox" in run_argv and run_argv[run_argv.index("--sandbox") + 1] == "docker"
    assert "--keep-volume" in run_argv
    assert run_argv[run_argv.index("--model") + 1] == "m1"
    assert run_argv[run_argv.index("--provider") + 1] == "openai"
    assert run_argv[run_argv.index("--base-url") + 1] == "http://127.0.0.1:9/v1"
    assert run_argv[run_argv.index("--max-turns") + 1] == "40"

    assert row["status"] == "completed"
    assert row["turns"] == 3
    assert row["prompt_tokens"] == 11 and row["completion_tokens"] == 7
    assert row["acceptance"] == "pass"
    assert row["slug"] == "fixtask-0101-abcd"
    assert row["provider"] == "openai"
    assert isinstance(row["wall_s"], float)
    assert any(argv[:2] == ["volume", "rm"] for argv in seen)     # volume removed afterwards


def test_run_one_bench_case_counts_harness_failures(tmp_path, monkeypatch):
    events = [
        {"event": "nudge", "kind": "stall", "turn": 6},
        {"event": "nudge", "kind": "empty", "turn": 7},
        {"event": "nudge", "kind": "truncated", "turn": 8},
        {"event": "nudge", "kind": "text_tool_call", "turn": 9},
        {"event": "guardrail_block", "tool": "bash", "reason": "BLOCKED: nope"},
        {"event": "sandbox_reset", "reason": "timeout"},
        {"event": "run_end", "status": "stalled"},
    ]
    _fake_run_environment(tmp_path, monkeypatch, transcript_events=events, payload={
        "status": "stalled", "turns": 12, "usage": {}, "final_message": ""})
    row = bench.run_one_bench_case("m1", "sh-fix-script", 0, provider=None, base_url=None,
                                   stamp="s", max_turns=40, timeout=1800)
    harness = row["harness"]
    assert harness["nudge_stall"] == 1
    assert harness["nudge_empty"] == 1
    assert harness["nudge_truncated"] == 1
    assert harness["nudge_text_tool_call"] == 1
    assert harness["empty_reply"] == 3       # every non-stall nudge is one empty_reply failure
    assert harness["stalled"] == 1
    assert harness["max_turns"] == 0
    assert harness["sandbox_error"] == 0
    assert row["guardrail_blocks"] == 1
    assert row["sandbox_resets"] == 1
    assert row["acceptance"] == "skipped"    # a non-completed run is never scored


def test_abort_kind_is_parsed_from_the_final_message():
    assert bench._abort_kind("aborted after 3 consecutive bad_args failures") == "bad_args"
    assert bench._abort_kind("aborted after 6 consecutive tool failures") == "mixed"
    assert bench._abort_kind("all done") is None
    assert bench._abort_kind(None) is None


def test_cmd_bench_requires_models(capsys):
    rc = bench.cmd_bench(argparse.Namespace(models=None, provider=None, base_url=None,
                                            repeats=1, tasks=None, out=None,
                                            max_turns=40, timeout=1800))
    assert rc == 2
    assert "--models is required" in capsys.readouterr().err


def test_cmd_bench_writes_one_row_per_model_spec(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_case(model, task, repeat, *, provider, base_url, stamp, max_turns, timeout):
        calls.append((model, task, repeat, provider, base_url))
        return {"stamp": stamp, "model": model, "task": task, "repeat": repeat,
                "provider": provider, "status": "completed", "acceptance": "pass"}

    monkeypatch.setattr(bench, "run_one_bench_case", fake_case)
    out_file = tmp_path / "results.jsonl"
    rc = bench.cmd_bench(argparse.Namespace(models="m1,m2@anthropic", provider=None,
                                            base_url=None, repeats=1, tasks="sh-fix-script",
                                            out=str(out_file), max_turns=40, timeout=1800))
    assert rc == 0
    rows = [json.loads(l) for l in out_file.read_text().splitlines()]
    assert len(rows) == 2
    assert {r["model"] for r in rows} == {"m1", "m2"}
    assert ("m2", "sh-fix-script", 0, "anthropic", None) in calls


def test_cmd_bench_rejects_an_unknown_task(tmp_path, capsys):
    rc = bench.cmd_bench(argparse.Namespace(models="m1", provider=None, base_url=None,
                                            repeats=1, tasks="no-such-task",
                                            out=str(tmp_path / "r.jsonl"),
                                            max_turns=40, timeout=1800))
    assert rc == 2
    assert "no-such-task" in capsys.readouterr().err
