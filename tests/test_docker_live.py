# tests/test_docker_live.py
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from dirtywork.__main__ import DEFAULT_MODEL
from tests.docker_live_helpers import _call, _make_live_repo, _resp


class ScriptedClient:
    """Stands in for LMStudioClient so these tests need a real Docker
    daemon but NOT a real LM Studio server."""

    def __init__(self, responses):
        self.responses = list(responses)

    def list_models(self):
        return [DEFAULT_MODEL]

    def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
        return self.responses.pop(0)


def _config_bytes(repo: Path) -> bytes:
    return (repo / ".git" / "config").read_bytes()


def _refs_listing(repo: Path) -> str:
    # dirtywork itself creates refs/heads/dirtywork/<slug> on the host before
    # the run even starts (create_worktree) -- that is expected, not a
    # sandbox breach, so it is excluded here. Any OTHER ref appearing or
    # changing is still a real isolation failure.
    out = subprocess.run(["git", "-C", str(repo), "for-each-ref"],
                          capture_output=True, text=True, check=True).stdout
    lines = [line for line in out.splitlines() if "refs/heads/dirtywork/" not in line]
    return "\n".join(lines)


def _object_hashes(repo: Path) -> dict:
    objects_dir = repo / ".git" / "objects"
    hashes = {}
    for path in objects_dir.rglob("*"):
        if path.is_file():
            hashes[str(path.relative_to(objects_dir))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _assert_status(payload: dict, expected) -> None:
    """Assert payload["status"] against expected (a single status string, or
    a tuple/set of acceptable statuses), folding payload.get("final_message")
    into the failure text -- a bare status mismatch tells us nothing about
    *why* docker-live failed, and that text is exactly what
    tools/ci_sandbox_smoke.py and this project's CI need to see."""
    status = payload["status"]
    ok = status == expected if isinstance(expected, str) else status in expected
    assert ok, (
        f"expected status {expected!r}, got {status!r} -- "
        f"final_message={payload.get('final_message')!r}"
    )


def _run_main(monkeypatch, tmp_path, responses, argv):
    import dirtywork.__main__ as m
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    client = ScriptedClient(responses)
    monkeypatch.setattr(m, "LMStudioClient", lambda base_url=None: client)
    return m.main(argv)


def _run_docker_main(monkeypatch, tmp_path, repo, responses, **extra_args):
    argv = ["run", "--repo", str(repo), "--sandbox", "docker"]
    for k, v in extra_args.items():
        flag = "--" + k.replace("_", "-")
        if v is True:
            argv.append(flag)
        elif v is not False:
            argv += [flag, str(v)]
    argv.append("do the task")
    return _run_main(monkeypatch, tmp_path, responses, argv)


@pytest.mark.docker
def test_docker_live_full_run_host_sentinels_and_isolation(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    sentinel_path = tmp_path / "filter_sentinel.txt"
    subprocess.run(["git", "-C", str(repo), "config", "--local", "filter.evil.clean",
                     f"touch {sentinel_path}"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "--local", "filter.evil.smudge",
                     f"touch {sentinel_path}"], check=True)
    outside_sentinel = tmp_path / "outside_sentinel.txt"
    outside_sentinel.write_text("do not touch\n")

    config_before = _config_bytes(repo)
    refs_before = _refs_listing(repo)
    objects_before = _object_hashes(repo)

    responses = [
        _resp(tool_calls=[_call("c1", "write_file", {"path": "hello.txt", "content": "from worker\n"})]),
        _resp(tool_calls=[_call("c2", "write_file", {"path": ".gitattributes", "content": "* filter=evil\n"})]),
        _resp(tool_calls=[_call("c3", "bash",
              {"command": "python3 -c \"open('/etc/dirtywork_sentinel','w')\""})]),
        _resp(tool_calls=[_call("c4", "bash", {"command": "git config core.hooksPath x && cat /gitdir/config"})]),
        _resp(tool_calls=[_call("c5", "bash", {"command": "curl -s -m 5 http://example.com/ ; echo curl_exit=$?"})]),
        _resp(tool_calls=[_call("c6", "bash", {"command": "git status"})]),
        _resp(content="done"),
    ]

    _run_docker_main(monkeypatch, tmp_path, repo, responses)
    payload = json.loads(capsys.readouterr().out)

    _assert_status(payload, "completed")
    worktree = Path(payload["worktree"])
    assert (worktree / "hello.txt").read_text() == "from worker\n"

    assert _config_bytes(repo) == config_before
    assert _refs_listing(repo) == refs_before
    assert _object_hashes(repo) == objects_before
    assert outside_sentinel.read_text() == "do not touch\n"
    assert not sentinel_path.exists()  # the operator's local filter never fired on the host

    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert len(bash_results) == 4
    assert "permission denied" in bash_results[0].lower() or "read-only" in bash_results[0].lower()
    assert "hooksPath" in bash_results[1]  # git config wrote only /gitdir/config, visible via cat
    assert "curl_exit=0" not in bash_results[2]  # curl must fail: --network none
    assert "exit code: 0" in bash_results[3]  # git status works with the GIT_DIR mapping


@pytest.mark.docker
def test_docker_live_timeout_kills_command_and_run_continues(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash", {"command": "sleep 600", "timeout": 2})]),
        _resp(tool_calls=[_call("c2", "bash", {"command": "echo still-alive"})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses)
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")
    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert "timed out" in bash_results[0].lower()
    assert "still-alive" in bash_results[1]


@pytest.mark.docker
def test_docker_live_backgrounded_process_is_dead_after_reap(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash",
              {"command": "nohup sh -c 'sleep 3; touch /tmp/dw_bg_marker' >/dev/null 2>&1 & echo started"})]),
        _resp(tool_calls=[_call("c2", "bash",
              {"command": "sleep 4; test -f /tmp/dw_bg_marker && echo FOUND || echo GONE"})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses)
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")
    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert "GONE" in bash_results[1]


@pytest.mark.docker
def test_docker_live_process_flood_triggers_reset(tmp_path, monkeypatch, capsys):
    # 40 stray background processes is plenty for _reap()'s "docker top shows
    # more than the tether" check (docker top runs on the HOST via /proc, so
    # detection doesn't depend on process count) while staying well under the
    # container's --pids-limit 512 default. Fix items 2+3 made the watchdog
    # thread's own periodic worktree sample correctly fail closed
    # (BudgetExceeded) when its own `du`/`find` exec fails twice in a row --
    # spawning close to 512 processes (as this test originally did with 600)
    # can starve THAT exec too and race it against _reap()'s recovery, which
    # is a real but different failure mode from the one this test targets
    # (plain stray-process detection-and-recovery). Keeping the flood well
    # below the pids cap isolates the mechanism this test is actually about.
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash", {
            "command": "for i in $(seq 1 40); do sleep 30 & done; echo spawned",
            "timeout": 30,
        })]),
        _resp(tool_calls=[_call("c2", "bash", {"command": "echo alive-after-reset"})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses)
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")
    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    reset_events = [e for e in events if e["event"] == "sandbox_reset"]
    assert reset_events
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert "alive-after-reset" in bash_results[1]


@pytest.mark.docker
def test_docker_live_pid_flood_past_limit_recovers_or_fails_closed(tmp_path, monkeypatch, capsys):
    # D3 (final-rereview follow-up 1): restore live coverage of spec §6's
    # closing claim -- "fork bombs are contained by --pids-limit and
    # cleared by the reap/reset" -- at a flood size that actually crosses
    # the default --pids-limit 512. test_docker_live_process_flood_triggers_reset
    # above deliberately stays at 40 (well under the cap) to isolate plain
    # stray-process detect-and-recover from this race; see that test's
    # docstring for why. D1+D2 make the terminal outcome deterministic
    # either way once the cap is actually crossed: "completed" with at
    # least one sandbox_reset in the transcript (reap/reset recovered), or
    # sandbox_error/budget_exceeded (the watchdog's own worktree sample
    # failed closed -- spec §6's "second failure -> sandbox_error" -- or a
    # genuine budget breach). Either is an acceptable terminal contract; a
    # hang is not, and neither outcome may leave a container behind.
    import time
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash", {
            "command": "for i in $(seq 1 600); do sleep 30 & done; echo spawned",
            "timeout": 60,
        })]),
        _resp(tool_calls=[_call("c2", "bash", {"command": "echo alive"})]),
        _resp(content="done"),
    ]
    start = time.monotonic()
    _run_docker_main(monkeypatch, tmp_path, repo, responses, timeout=60)
    elapsed = time.monotonic() - start
    payload = json.loads(capsys.readouterr().out)

    assert elapsed < 90, f"run took {elapsed:.1f}s -- must reach a terminal state, not hang"
    _assert_status(payload, ("completed", "sandbox_error", "budget_exceeded"))

    slug = Path(payload["worktree"]).name
    assert slug.startswith("dw-")
    slug = slug[len("dw-"):]
    leftover = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"label=dirtywork.run={slug}", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    ).stdout
    assert leftover.strip() == "", f"leftover container(s) after the run: {leftover!r}"

    if payload["status"] == "completed":
        events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
        reset_events = [e for e in events if e["event"] == "sandbox_reset"]
        assert reset_events  # reap/reset recovered from the flood
        bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
        assert len(bash_results) >= 2
        assert "alive" in bash_results[1]  # the follow-up bash call still ran


@pytest.mark.docker
def test_docker_live_export_reports_nested_git_and_escaping_symlink_and_skips_ignored(
        tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash", {
            "command": "mkdir -p payload && echo fake > payload/.git && ln -s /etc/passwd esc && echo ignored.bin > .gitignore && echo secret > ignored.bin",
        })]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses)
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")
    worktree = Path(payload["worktree"])
    assert (worktree / "esc").is_symlink()
    assert not (worktree / "ignored.bin").exists()
    assert not (worktree / "payload" / ".git").exists()

    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    run_end = next(e for e in events if e["event"] == "run_end")
    assert "payload/.git" in run_end.get("dropped_git_entries", [])
    assert "esc" in run_end.get("escaping_symlinks", [])


@pytest.mark.docker
def test_docker_live_over_budget_write_ends_run_with_budget_exceeded(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash",
              {"command": "dd if=/dev/zero of=big.bin bs=1M count=5 2>/dev/null; echo done"})]),
    ]
    rc = _run_docker_main(monkeypatch, tmp_path, repo, responses, max_worktree_mb=1)
    payload = json.loads(capsys.readouterr().out)
    # Fix item 4: budget_exceeded is the actual cause of the run ending, so
    # it must survive even though finalize()'s export also fails (the same
    # max_worktree_mb cap governs the export's extract_validated call, and
    # the 5 MB file it wrote is over the 1 MB cap) -- export_failed must not
    # overwrite it.
    _assert_status(payload, "budget_exceeded")
    assert rc == 1
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["status"] == "budget_exceeded"
    # export_status is not part of the stdout payload on the normal
    # runner.run() completion path (only _fail_run's exception path adds it
    # there) -- it is always visible in run.json, so check it there.
    assert run_json["export_status"].startswith("export_failed")


@pytest.mark.docker
def test_docker_live_export_refused_into_nonempty_worktree(tmp_path):
    from dirtywork.sandbox import docker_args
    from dirtywork.sandbox.docker_cli import resolve_image, validate_objects_dir
    from dirtywork.sandbox.export import export_run
    from dirtywork.workspace import create_worktree, ensure_worktrees_excluded, worktree_base_commit

    repo = _make_live_repo(tmp_path)
    ensure_worktrees_excluded(repo)
    worktree = create_worktree(repo, "livexp", None, no_checkout=True)
    base_commit = worktree_base_commit(worktree)
    (worktree / "stray.txt").write_text("should not be here")  # makes the worktree non-empty

    objects_dir = validate_objects_dir(repo)
    image_ref = resolve_image(docker_args.DEFAULT_IMAGE)
    cfg = docker_args.DockerConfig()
    label = docker_args.repo_label(repo)
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()

    artifacts = export_run(
        cfg, slug="livexp", base_commit=base_commit, worktree=worktree, run_dir=run_dir,
        objects_dir=objects_dir, image_ref=image_ref, uid=os.getuid(), gid=os.getgid(),
        repo_label=label,
    )

    assert artifacts.export_status == "export_failed: worktree not empty"


@pytest.mark.docker
def test_docker_live_resume_seeds_worktree_keeps_branch_and_exports(tmp_path, monkeypatch, capsys):
    import json
    from pathlib import Path

    repo = _make_live_repo(tmp_path)
    first_responses = [
        _resp(tool_calls=[_call("c1", "write_file", {"path": "new.txt", "content": "from run 1\n"})]),
        _resp(tool_calls=[_call("c2", "bash", {"command": "rm README.md"})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, first_responses)
    first = json.loads(capsys.readouterr().out)
    _assert_status(first, "completed")
    worktree = Path(first["worktree"])
    assert (worktree / "new.txt").read_text() == "from run 1\n"
    assert not (worktree / "README.md").exists()

    second_responses = [
        _resp(tool_calls=[_call("r1", "bash", {"command":
              "cat /work/new.txt; test -e /work/README.md && echo readme=present || echo readme=absent; "
              "git symbolic-ref HEAD; git status --short"})]),
        _resp(tool_calls=[_call("r2", "write_file", {"path": "second.txt", "content": "from run 2\n"})]),
        _resp(tool_calls=[_call("r3", "finish", {"summary": "resumed"})]),
    ]
    rc = _run_main(monkeypatch, tmp_path, second_responses, ["resume", Path(first["run_dir"]).name])
    second = json.loads(capsys.readouterr().out)
    assert rc == 0, second
    _assert_status(second, "completed")
    assert second["resumed_from"] == Path(first["run_dir"]).name
    assert second["worktree"] == first["worktree"] and second["branch"] == first["branch"]

    events = [json.loads(l) for l in Path(second["transcript"]).read_text().splitlines()]
    probe = next(e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash")
    assert "from run 1" in probe                       # seeded from the host worktree
    assert "readme=absent" in probe                    # deletions carried over
    assert f"refs/heads/{first['branch']}" in probe    # original branch name inside the container
    assert "?? new.txt" in probe or "A  new.txt" in probe or "new.txt" in probe
    assert " D README.md" in probe or "D  README.md" in probe

    # export after the resumed run flattened the final tree back onto the same worktree
    assert (worktree / "new.txt").read_text() == "from run 1\n"
    assert (worktree / "second.txt").read_text() == "from run 2\n"
    assert not (worktree / "README.md").exists()
    prior = json.loads((Path(first["run_dir"]) / "run.json").read_text())
    assert prior["resumed_by"] == json.loads((Path(second["run_dir"]) / "run.json").read_text())["slug"]
