from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

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
        "--pids-limit", "256",          # docker_args.security_args: shared with worker/export containers
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "501:20",
        "--memory", "2g", "--memory-swap", "2g",
        "--cpus", "2",
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


def _never_called(*a, **k):
    raise AssertionError("must not touch docker when acceptance fields are missing/malformed")


def test_run_acceptance_skipped_when_bench_json_has_no_acceptance_key(monkeypatch):
    # D4: _run_acceptance's "never raises" docstring must hold for a malformed
    # bench.json too, not just a docker failure.
    monkeypatch.setattr(bench.docker_cli, "resolve_image", _never_called)
    assert bench._run_acceptance("sh-fix-script", {}, "dw-x-work", run=_never_called) == "skipped"


def test_run_acceptance_skipped_when_acceptance_missing_command(monkeypatch):
    monkeypatch.setattr(bench.docker_cli, "resolve_image", _never_called)
    data = {"acceptance": {"hashes": {"foo.txt": "abc"}}}   # no "command" key
    assert bench._run_acceptance("sh-fix-script", data, "dw-x-work", run=_never_called) == "skipped"


def test_run_acceptance_skipped_when_acceptance_hashes_not_a_dict(monkeypatch):
    monkeypatch.setattr(bench.docker_cli, "resolve_image", _never_called)
    data = {"acceptance": {"hashes": None, "command": "true"}}
    assert bench._run_acceptance("sh-fix-script", data, "dw-x-work", run=_never_called) == "skipped"


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


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX permissions only")
def test_cmd_bench_creates_bench_dir_and_results_file_with_safe_perms(tmp_path, monkeypatch, capsys):
    # D2: the default ~/.dirtywork/bench directory and results file must get
    # the same 0700/0600 treatment as ~/.dirtywork/runs and run.json.
    home = tmp_path / "home"
    home.mkdir()
    bench_home = home / ".dirtywork" / "bench"
    monkeypatch.setattr(bench, "BENCH_HOME", bench_home)

    def fake_case(model, task, repeat, *, provider, base_url, stamp, max_turns, timeout):
        return {"stamp": stamp, "model": model, "task": task, "repeat": repeat,
                "provider": provider, "status": "completed", "acceptance": "pass"}

    monkeypatch.setattr(bench, "run_one_bench_case", fake_case)
    rc = bench.cmd_bench(argparse.Namespace(models="m1", provider=None, base_url=None,
                                            repeats=1, tasks="sh-fix-script", out=None,
                                            max_turns=40, timeout=1800))
    assert rc == 0
    assert stat.S_IMODE(bench_home.stat().st_mode) == 0o700
    results = list(bench_home.glob("*.jsonl"))
    assert len(results) == 1
    assert stat.S_IMODE(results[0].stat().st_mode) == 0o600


def test_cmd_bench_rejects_an_unknown_task(tmp_path, capsys):
    rc = bench.cmd_bench(argparse.Namespace(models="m1", provider=None, base_url=None,
                                            repeats=1, tasks="no-such-task",
                                            out=str(tmp_path / "r.jsonl"),
                                            max_turns=40, timeout=1800))
    assert rc == 2
    assert "no-such-task" in capsys.readouterr().err


def _result_row(**over):
    row = {"model": "m1", "task": "t", "repeat": 0, "status": "completed",
           "acceptance": "pass", "turns": 4, "wall_s": 2.0,
           "prompt_tokens": 10, "completion_tokens": 5, "slug": "s1",
           "harness": {"nudge_stall": 0, "nudge_empty": 0, "nudge_truncated": 0,
                       "nudge_text_tool_call": 0, "nudge_other": 0, "empty_reply": 0,
                       "stalled": 0, "max_turns": 0, "sandbox_error": 0, "abort_kind": None}}
    row.update(over)
    return row


def test_summarize_prints_detail_table_and_per_model_stats(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    results = tmp_path / "results.jsonl"
    rows = [
        _result_row(),
        _result_row(repeat=1, acceptance="fail", prompt_tokens=20, completion_tokens=10,
                    wall_s=4.0, slug="s2"),
        _result_row(model="m2", status="stalled", acceptance="skipped", turns=12,
                    wall_s=1.0, prompt_tokens=5, completion_tokens=1, slug="s3",
                    harness={"nudge_stall": 2, "nudge_empty": 1, "nudge_truncated": 0,
                             "nudge_text_tool_call": 0, "nudge_other": 0, "empty_reply": 1,
                             "stalled": 1, "max_turns": 0, "sandbox_error": 0,
                             "abort_kind": None}),
    ]
    results.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    for slug, verdict, review in [("s1", "accept", 30), ("s2", "reject", 90)]:
        run_dir = tmp_path / "runs" / slug
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"verdict": verdict,
                                                      "review_seconds": review}))

    rc = bench.cmd_summarize(argparse.Namespace(file=str(results)))
    assert rc == 0
    out = capsys.readouterr().out
    # detail table
    assert "MODEL" in out and "NUDGES" in out and "FAILURES" in out
    assert "2/1/0/0" in out            # m2's nudge counts
    assert "stalled" in out
    assert "accept" in out and "reject" in out
    # per-model block
    assert "model: m1" in out
    assert "runs: 2" in out
    assert "completion rate: 100%" in out
    assert "acceptance rate: 50%" in out
    assert "verdict rate: 50%" in out
    assert "median review_seconds: 60" in out
    # D3: exact mean tokens / mean wall_s, computed from the fixture rows above.
    # m1: tokens (10+5, 20+10) -> mean 22.5; wall_s (2.0, 4.0) -> mean 3.0.
    assert "mean tokens: 22.5" in out
    assert "mean wall_s: 3.0" in out
    assert "model: m2" in out
    # m2: a single row -- tokens 5+1=6, wall_s 1.0.
    assert "mean tokens: 6.0" in out
    assert "mean wall_s: 1.0" in out


def test_summarize_median_review_seconds_is_na_when_verdicts_have_no_numeric_review_seconds(
        tmp_path, monkeypatch, capsys):
    # D1: a model can have a verdict (so verdict_rate is not None) without any
    # numeric review_seconds recorded -- must print "n/a", not raise TypeError.
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(_result_row(slug="s1")) + "\n")
    run_dir = tmp_path / "runs" / "s1"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"verdict": "accept"}))  # no review_seconds
    rc = bench.cmd_summarize(argparse.Namespace(file=str(results)))
    assert rc == 0
    out = capsys.readouterr().out
    assert "verdict rate: 100%" in out
    assert "median review_seconds: n/a" in out


def test_summarize_missing_file_exits_2(tmp_path, capsys):
    rc = bench.cmd_summarize(argparse.Namespace(file=str(tmp_path / "nope.jsonl")))
    assert rc == 2
    assert "no such file" in capsys.readouterr().err


def test_summarize_ignores_blank_and_malformed_lines(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(_result_row()) + "\n\nnot json\n")
    assert bench.cmd_summarize(argparse.Namespace(file=str(results))) == 0
    assert "runs: 1" in capsys.readouterr().out


def test_dispatch_routes_summarize_and_bench(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    results = tmp_path / "r.jsonl"
    results.write_text(json.dumps(_result_row(slug=None)) + "\n")
    rc = bench.dispatch(argparse.Namespace(bench_cmd="summarize", file=str(results)))
    assert rc == 0
    monkeypatch.setattr(bench, "cmd_bench", lambda args: 7)
    assert bench.dispatch(argparse.Namespace(bench_cmd=None, models="m1")) == 7


def test_run_one_bench_case_staging_failure_becomes_bench_error_row(monkeypatch):
    # A staging failure (git missing/misconfigured, disk full) must degrade to a
    # recorded bench_error row -- never abort the whole sweep.
    def boom(task):
        raise RuntimeError("git: command not found")
    monkeypatch.setattr(bench, "_stage_repo", boom)
    called = []
    monkeypatch.setattr(bench, "run_once", lambda argv: called.append(argv))
    row = bench.run_one_bench_case("m", "py-fix-off-by-one", 1, provider=None, base_url=None,
                                   stamp="s", max_turns=1, timeout=1)
    assert row["status"] == "bench_error"
    assert "git: command not found" in row["error"]
    assert row["acceptance"] == "skipped"
    assert called == []


def test_verdict_for_falls_back_when_run_json_is_not_an_object(tmp_path, monkeypatch):
    # A corrupt/partially written run.json (valid JSON, wrong shape) must not
    # take down `bench summarize`; the row's own verdict is the fallback.
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    run_dir = tmp_path / "runs" / "slug1"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text("[1, 2, 3]")
    row = {"slug": "slug1", "verdict": "accept", "review_seconds": 12}
    assert bench._verdict_for(row) == ("accept", 12)


def test_python_m_dirtywork_bench_does_not_double_load_main(tmp_path):
    # `python -m dirtywork bench …` runs __main__.py as "__main__"; bench's lazy
    # `import dirtywork.__main__` must resolve to THAT module (aliased in
    # sys.modules), not execute the file a second time. Observed from inside a
    # subprocess that runs the module the way `-m` does, then imports it again.
    results = tmp_path / "r.jsonl"
    results.write_text("")
    probe = (
        "import runpy, sys\n"
        "sys.argv = ['dirtywork', 'bench', 'summarize', %r]\n"
        "try:\n"
        "    runpy.run_module('dirtywork', run_name='__main__', alter_sys=True)\n"
        "except SystemExit:\n"
        "    pass\n"
        "import dirtywork.__main__ as again\n"
        # The module that ran under -m keeps __name__ == '__main__'; a second,
        # freshly executed copy would be named 'dirtywork.__main__'.
        "print('SAME' if again.__name__ == '__main__' else 'DOUBLE-LOADED:' + again.__name__)\n"
    ) % str(results)
    cp = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                        cwd=str(Path(__file__).resolve().parent.parent), timeout=120)
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout.strip().splitlines()[-1] == "SAME", cp.stdout + cp.stderr


def test_summarize_compare_pairs_rows_and_shows_deltas(tmp_path, monkeypatch, capsys):
    # Two sweeps of the same two tasks, plus one task only B ran. Rows carry
    # slug=None so _verdict_for never touches the (monkeypatched) runs dir.
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a_rows = [
        _result_row(model="m1", task="t1", turns=4, wall_s=2.0, slug=None),
        _result_row(model="m1", task="t1", repeat=1, turns=6, wall_s=4.0,
                    acceptance="fail", slug=None),
        # a bench_error row: every numeric field is None and harness is {}
        _result_row(model="m1", task="t2", status="bench_error", turns=None, wall_s=1.0,
                    prompt_tokens=None, completion_tokens=None, acceptance="skipped",
                    harness={}, slug=None),
    ]
    b_rows = [
        _result_row(model="m1", task="t1", turns=2, wall_s=1.0, slug=None),
        _result_row(model="m1", task="t1", repeat=1, turns=4, wall_s=3.0, slug=None),
        _result_row(model="m1", task="t2", status="bench_error", turns=None, wall_s=1.0,
                    prompt_tokens=None, completion_tokens=None, acceptance="skipped",
                    harness={}, slug=None),
        _result_row(model="m1", task="t3", turns=8, wall_s=5.0, slug=None),
    ]
    a.write_text("\n".join(json.dumps(r) for r in a_rows) + "\n")
    b.write_text("\n".join(json.dumps(r) for r in b_rows) + "\n")

    rc = bench.cmd_summarize(argparse.Namespace(file=str(a), compare=str(b)))
    assert rc == 0
    out = capsys.readouterr().out
    # header names both files and states the delta direction
    assert f"A = {a}" in out
    assert f"B = {b}" in out
    assert "Δ = B - A" in out
    # m1/t1: mean turns 5.0 -> 3.0, mean wall 3.0 -> 2.0, acceptance 50% -> 100%
    assert "5.0 -> 3.0 (-2.0)" in out
    assert "3.0 -> 2.0 (-1.0)" in out
    assert "50% -> 100% (+50%)" in out
    # the bench_error row aggregates instead of crashing: no numbers on either side
    assert "t2" in out
    assert "- -> -" in out
    # a key only B ran shows the dash on the A side
    assert "t3" in out
    assert "- -> 1" in out
    # the per-model block is paired the same way
    assert "per-model (A -> B):" in out
    assert "MODEL" in out and "GAMED" in out


def test_summarize_compare_missing_file_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench.rundir, "RUNS_DIR", tmp_path / "runs")
    a = tmp_path / "a.jsonl"
    a.write_text(json.dumps(_result_row(slug=None)) + "\n")
    rc = bench.cmd_summarize(argparse.Namespace(file=str(a),
                                                compare=str(tmp_path / "nope.jsonl")))
    assert rc == 2
    assert "no such file" in capsys.readouterr().err

