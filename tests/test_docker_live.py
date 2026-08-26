# tests/test_docker_live.py
from __future__ import annotations

import functools
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from dirtywork.sandbox.docker_args import DEFAULT_IMAGE
from tests.docker_live_helpers import _call, _events, _make_live_repo, _of, _resp
from tests.provider_doubles import DictProvider, patch_provider

# issue #63: override the image used by the live docker tests below (both
# the --home-size test and the .NET SDK build test) -- e.g. to point at a
# locally built docker/Dockerfile before it is tagged/published. Unset in
# CI's docker-live job, which builds the image and tags it as the CLI's own
# default, so the same tests exercise that build without an env var.
LIVE_IMAGE = os.environ.get("DIRTYWORK_LIVE_IMAGE")


def _image_kwargs() -> dict:
    """The --image override to pass through _run_docker_main, or {} to let
    the CLI's own default apply."""
    return {"image": LIVE_IMAGE} if LIVE_IMAGE else {}


@functools.lru_cache(maxsize=None)
def _dotnet_list_sdks(image: str) -> str:
    """stdout of `dotnet --list-sdks` inside `image`, cached per image so
    the two .NET parametrizations only invoke docker once each. A non-zero
    exit fails the test loudly instead of returning '' -- silently returning
    '' let a missing/broken image (not just one that genuinely predates the
    1.0 Dockerfile) read as "no SDK 10.x" and skip green."""
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "/usr/bin/dotnet", image, "--list-sdks"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"docker run --entrypoint /usr/bin/dotnet {image} --list-sdks failed "
            f"(rc {result.returncode}): {result.stderr.strip()[:500]}")
    return result.stdout


def test_dotnet_list_sdks_fails_loudly_on_nonzero_exit(monkeypatch):
    # Unit-level (no docker daemon needed): a broken/missing image must fail
    # the test rather than read as '' and let the SDK-10 gate in
    # test_docker_live_dotnet_builds_and_runs_offline skip green as though it
    # merely predates the 0.11 Dockerfile.
    _dotnet_list_sdks.cache_clear()
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 1, stdout="", stderr="Unable to find image 'bogus:latest' locally"))
    try:
        with pytest.raises(pytest.fail.Exception) as ei:
            _dotnet_list_sdks("bogus:latest")
        msg = str(ei.value)
        assert ("docker run --entrypoint /usr/bin/dotnet bogus:latest --list-sdks failed "
                "(rc 1)") in msg
        assert "Unable to find image 'bogus:latest' locally" in msg
    finally:
        _dotnet_list_sdks.cache_clear()  # don't leak the faked result into the live tests


class ScriptedClient(DictProvider):
    """Stands in for the provider so these tests need a real Docker
    daemon but NOT a real LM Studio server."""

    def __init__(self, responses):
        super().__init__()
        self.responses = list(responses)
        self._last = None

    def reply(self, model, messages, tools):
        if not self.responses:
            # The change guard (#66) refuses a fresh run's first completion
            # when nothing in the worktree changed and accepts the second;
            # a script whose last reply is a completion -- a plain answer
            # or a `finish` call -- is asked once more and answers the same
            # way. Running dry anywhere else is still a test failure.
            if self._last is not None and _is_completion(self._last):
                return self._last
            raise IndexError("scripted responses exhausted before the run ended")
        self._last = self.responses.pop(0)
        return self._last


def _is_completion(resp) -> bool:
    calls = resp.get("tool_calls") or []
    if not calls:
        return True
    return any((c.get("name") or (c.get("function") or {}).get("name")) == "finish" for c in calls)


class _SlowClient(ScriptedClient):
    """Subclass of ScriptedClient that sleeps 5.2 seconds before replying.
    Used to test that the watchdog's 5 second worktree sample comes due
    at the start of a short call and still be in flight when it returns."""

    def reply(self, model, messages, tools):
        import time
        time.sleep(5.2)
        return super().reply(model, messages, tools)


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


def _run_main(monkeypatch, tmp_path, responses, argv, client_cls=ScriptedClient):
    import dirtywork.__main__ as m
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    client = client_cls(responses)
    patch_provider(monkeypatch, m, lambda base_url=None: client)
    return m.main(argv)


def _run_docker_main(monkeypatch, tmp_path, repo, responses, client_cls=ScriptedClient, **extra_args):
    argv = ["run", "--repo", str(repo), "--sandbox", "docker"]
    for k, v in extra_args.items():
        flag = "--" + k.replace("_", "-")
        if v is True:
            argv.append(flag)
        elif v is not False:
            argv += [flag, str(v)]
    argv.append("do the task")
    return _run_main(monkeypatch, tmp_path, responses, argv, client_cls=client_cls)


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

    _run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())
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
    _run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())
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
    _run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")
    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert "GONE" in bash_results[1]


@pytest.mark.docker
def test_docker_live_process_flood_is_killed_in_place(tmp_path, monkeypatch, capsys):
    # 40 stray background processes is plenty for _reap()'s "docker top shows
    # more than the tether" check (docker top runs on the HOST via /proc, so
    # detection doesn't depend on process count) while staying well under the
    # container's --pids-limit 512 default. Since 1.0 (#61) the strays are
    # killed IN PLACE -- one fork-free exec, a settle re-check -- and the
    # container, its /tmp and /gitdir survive: the transcript records a
    # `stray_kill` naming them (capped at 20, the full count in
    # `strays_total`) and no `sandbox_reset`. The reset is the ladder's last
    # rung, reached only when the kill cannot be performed or verified (the
    # pids-saturation case is test_docker_live_pid_flood_past_limit_recovers_or_fails_closed).
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash", {
            "command": "for i in $(seq 1 40); do sleep 30 & done; echo spawned",
            "timeout": 30,
        })]),
        _resp(tool_calls=[_call("c2", "bash", {"command": "echo alive-after-kill"})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")
    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    assert not [e for e in events if e["event"] == "sandbox_reset"]
    kills = [e for e in events if e["event"] == "stray_kill"]
    assert len(kills) == 1
    assert len(kills[0]["strays"]) == 20 and kills[0]["strays_total"] >= 40
    assert all("sleep 30" in s for s in kills[0]["strays"])
    bash_results = [e for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert "alive-after-kill" in bash_results[1]["result"]


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
    _run_docker_main(monkeypatch, tmp_path, repo, responses, timeout=60, **_image_kwargs())
    elapsed = time.monotonic() - start
    payload = json.loads(capsys.readouterr().out)

    assert elapsed < 90, f"run took {elapsed:.1f}s -- must reach a terminal state, not hang"
    _assert_status(payload, ("completed", "sandbox_error", "budget_exceeded"))

    # Extended assertions about strays in events
    _extend_pid_flood_assertions(payload)

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
        assert reset_events or [e for e in events if e["event"] == "stray_kill"]  # either rung recovers from the flood (#61)
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
    _run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())
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
    rc = _run_docker_main(monkeypatch, tmp_path, repo, responses, max_worktree_mb=1, **_image_kwargs())
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
    repo = _make_live_repo(tmp_path)
    first_responses = [
        _resp(tool_calls=[_call("c1", "write_file", {"path": "new.txt", "content": "from run 1\n"})]),
        _resp(tool_calls=[_call("c2", "bash", {"command": "rm README.md"})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, first_responses, **_image_kwargs())
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
    rc = _run_main(monkeypatch, tmp_path, second_responses,
                   ["resume", Path(first["run_dir"]).name, "--feedback", "keep going"])
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


@pytest.mark.docker
def test_docker_live_home_size_flag_caps_home_tmpfs(tmp_path, monkeypatch, capsys):
    # issue #63: --home-size 300m (a non-default value, so a passing
    # assertion actually proves the flag reached the docker create argv --
    # the 256m default would pass a same-shaped assertion by accident).
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash",
              {"command": "df -B1 --output=size /home/worker | tail -1"})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses, home_size="300m", **_image_kwargs())
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")

    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert len(bash_results) == 1
    # bash results are "exit code: N\n<stdout>" (dirtywork/tools.py); df's
    # own output is the last non-empty line.
    lines = [l for l in bash_results[0].strip().splitlines() if l.strip()]
    assert lines[0] == "exit code: 0", bash_results[0]
    reported_bytes = int(lines[-1].strip())

    # tmpfs reports the cap exactly on Linux, but don't assert exact equality
    # here -- that would make this test an OOM/write-failure trap disguised
    # as a size check. 1% tolerance only.
    expected_bytes = 300 * 1024 * 1024  # 314572800
    assert abs(reported_bytes - expected_bytes) <= expected_bytes * 0.01, (
        f"reported {reported_bytes} bytes, expected ~{expected_bytes}"
    )

    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["home_size"] == "300m"
    assert run_json["sandbox"] == "docker"

    run_start = next(e for e in events if e["event"] == "run_start")
    assert run_start["sandbox"]["home_size"] == "300m"


@pytest.mark.docker
def test_docker_live_stray_is_killed_in_place_and_stash_survives(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash",
              {"command": 'echo x >> README.md && git stash && (nohup sleep 300 >/dev/null 2>&1 &) && echo started'})]),
        _resp(tool_calls=[_call("c2", "bash",
              {"command": 'git stash pop && git diff --stat'})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")
    events = _events(payload)
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert "README.md" in bash_results[1]
    stray_kills = _of(events, "stray_kill")
    assert len(stray_kills) == 1
    assert any("sleep 300" in s for s in stray_kills[0].get("strays", []))
    assert not [e for e in events if e["event"] == "sandbox_reset"]
    tool_results = [e for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert any("The sandbox killed" in str(r.get("follow_up", "")) for r in tool_results)


@pytest.mark.docker
def test_docker_live_cat_named_stray_dies_with_the_others(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash",
              {"command": 'mkfifo /tmp/f; (setsid cat 0<>/tmp/f >/dev/null 2>&1 &); (sleep 300 >/dev/null 2>&1 &); echo ok'})]),
        _resp(tool_calls=[_call("c2", "bash",
              {"command": 'ls /proc | grep -c "^\\([0-9]\\)$"'})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")
    events = _events(payload)
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    proc_count = int(bash_results[1].strip().splitlines()[-1])
    assert proc_count <= 5
    stray_kills = _of(events, "stray_kill")
    assert len(stray_kills) == 1
    assert not [e for e in events if e["event"] == "sandbox_reset"]


@pytest.mark.docker
def test_docker_live_killed_git_locks_are_swept(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash",
              {"command": 'touch /gitdir/index.lock /gitdir/gc.pid; (sleep 300 >/dev/null 2>&1 &); echo ok'})]),
        _resp(tool_calls=[_call("c2", "bash",
              {"command": 'git status --short; echo rc=$?'})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")
    events = _events(payload)
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert "rc=0" in bash_results[1]
    stray_kills = _of(events, "stray_kill")
    assert len(stray_kills) == 1
    assert "/gitdir/index.lock" in stray_kills[0].get("locks_removed", [])
    assert "/gitdir/gc.pid" in stray_kills[0].get("locks_removed", [])
    bash_events = [e for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert "Stale git lock files" in bash_events[0]["follow_up"]


@pytest.mark.docker
def test_docker_live_git_init_in_tmp_stays_local(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash",
              {"command": 'd=$(mktemp -d) && cd $d && git init -q && git status --short && git worktree list && git rev-parse --git-dir'})]),
        _resp(tool_calls=[_call("c2", "bash",
              {"command": 'cd /tmp && git -C /work status --short; echo rc=$?'})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")
    events = _events(payload)
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    result1 = bash_results[0]
    assert any("/tmp/" in line for line in result1.splitlines())
    worktree_lines = [l for l in result1.splitlines() if "git worktree list" in l or (l.strip().startswith("/tmp/") and ".git/worktrees" not in l)]
    assert any(".git" == line.strip() for line in result1.splitlines())
    assert "rc=0" in bash_results[1]
    assert not [e for e in events if e["event"] == "sandbox_reset"]
    assert not [e for e in events if e["event"] == "stray_kill"]


@pytest.mark.docker
def test_docker_live_nested_repos_export_as_plain_files(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash",
              {"command": 'printf \'__pycache__/\\n\' > .gitignore && mkdir -p sub && cd sub && git init -q && echo new > NEW.txt && echo mod >> ../README.md && mkdir -p deep/inner && cd deep/inner && git init -q && echo d > D.txt && mkdir -p /work/sub/__pycache__ && echo x > /work/sub/__pycache__/a.pyc'})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")
    events = _events(payload)
    run_end = next(e for e in events if e["event"] == "run_end")
    dropped = run_end.get("dropped_git_entries", [])
    assert sorted(dropped) == ["sub/.git", "sub/deep/inner/.git"]
    worktree = Path(payload["worktree"])
    assert (worktree / "sub" / "NEW.txt").read_text() == "new\n"
    assert (worktree / "sub" / "deep" / "inner" / "D.txt").read_text() == "d\n"
    assert (worktree / "README.md").read_text().endswith("mod\n")
    assert not (worktree / "sub" / "__pycache__" / "a.pyc").exists()
    ls_files = subprocess.run(
        ["git", "-C", str(worktree), "ls-files", "-s"],
        capture_output=True, text=True
    ).stdout
    assert not any(line.startswith("160000") for line in ls_files.splitlines())


@pytest.mark.docker
def test_docker_live_root_gitfile_tampering_20a(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash",
              {"command": 'rm .git; git status >/dev/null 2>&1; echo rc=$?'})]),
        # Force a sandbox reset by killing the tether from inside the container.
        # The fork bomb ':(){ :|:& };:' is now killed IN PLACE (the fork-free
        # kill survives pid saturation), so it never forces a reset. Kill the
        # tether instead; the container dies, next docker top fails, and the
        # harness resets as "container unreachable after bash".
        _resp(tool_calls=[_call("c2", "bash",
              {"command": 'for p in /proc/[0-9]*; do read -r c 2>/dev/null < "$p/comm" || continue; [ "$c" = cat ] && kill -9 "${p#/proc/}"; done; echo killed'})]),
        _resp(tool_calls=[_call("c3", "bash",
              {"command": 'cat .git'})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")
    events = _events(payload)
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert "rc=128" in bash_results[0]
    reset_events = _of(events, "sandbox_reset")
    assert reset_events and reset_events[0]["reason"] == "container unreachable after bash"
    assert "gitdir: /gitdir" in bash_results[2]


@pytest.mark.docker
def test_docker_live_root_gitfile_tampering_20b(tmp_path, monkeypatch, capsys):
    repo = _make_live_repo(tmp_path)
    responses = [
        _resp(tool_calls=[_call("c1", "bash",
              {"command": 'rm .git && git init -q && echo t > T.txt'})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")
    events = _events(payload)
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end.get("dropped_git_entries", []) == [".git"]
    worktree = Path(payload["worktree"])
    assert (worktree / "T.txt").read_text() == "t\n"


@pytest.mark.docker
@pytest.mark.parametrize("tfm", ["net8.0", "net10.0"])
def test_docker_live_dotnet_builds_and_runs_offline(tmp_path, monkeypatch, capsys, tfm):
    # issue #63 / #59: docker/Dockerfile installs .NET SDK 8.0 and 10.0 and
    # sets DOTNET_EnableWriteXorExecute=0 -- prove both targeting packs work
    # end-to-end offline (--network none) AND that the built app runs under
    # the bash tool's `ulimit -f 524288`: without that variable the .NET 8
    # runtime dies with SIGXFSZ (exit 153) at startup, which is exactly what
    # the published :0.10 image does. Gate on SDK 10 being present: an image
    # without it predates that Dockerfile and is known to fail the net8.0
    # case, so there is nothing to learn from running it there.
    image = LIVE_IMAGE or DEFAULT_IMAGE
    sdks = _dotnet_list_sdks(image)
    if not any(line.startswith("10.") for line in sdks.splitlines()):
        pytest.skip(f"image {image} predates the 0.11 Dockerfile (no .NET SDK 10.x, so no "
                    f"DOTNET_EnableWriteXorExecute=0 either) -- build docker/Dockerfile")

    repo = _make_live_repo(tmp_path)
    build_cmd = (
        f"cd /tmp && dotnet new console -f {tfm} -o app && "
        f"DOTNET_CLI_USE_MSBUILD_SERVER=0 MSBUILDDISABLENODEREUSE=1 "
        f"dotnet build app -nologo -p:UseSharedCompilation=false && echo BUILD_OK_{tfm} && "
        f"dotnet app/bin/Debug/{tfm}/app.dll && echo RUN_OK_{tfm}"
    )
    responses = [
        _resp(tool_calls=[_call("c1", "bash", {"command": build_cmd, "timeout": 600})]),
        _resp(content="done"),
    ]
    _run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")

    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert len(bash_results) == 1
    assert f"BUILD_OK_{tfm}" in bash_results[0], bash_results[0]
    assert f"RUN_OK_{tfm}" in bash_results[0], bash_results[0]


def _extend_pid_flood_assertions(payload: dict) -> None:
    """Extended assertions for pid flood tests.
    When the transcript has a sandbox_reset event it carries a non-empty
    strays list of strings; when it has a stray_kill instead, that event's
    strays is non-empty."""
    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]

    reset_events = [e for e in events if e["event"] == "sandbox_reset"]
    stray_kills = [e for e in events if e["event"] == "stray_kill"]

    # At least one of reset or stray_kill should occur
    assert reset_events or stray_kills, "Expected either sandbox_reset or stray_kill event"

    # A sandbox_reset carries `strays` only when strays caused it (spec #61 §6.1):
    # reason "stray process after bash", or "oom" found right after an in-place
    # kill. "budget sample failed" / "container unreachable after bash" carry none.
    for reset_event in reset_events:
        if reset_event.get("reason") in ("stray process after bash", "oom"):
            assert reset_event.get("strays"), f"a stray-caused sandbox_reset must carry strays: {reset_event}"

    # When there's a stray_kill, strays should be non-empty
    if stray_kills:
        for kill_event in stray_kills:
            assert "strays" in kill_event and len(kill_event["strays"]) > 0, f"stray_kill event should have non-empty strays: {kill_event}"


@pytest.mark.docker
def test_docker_live_race_loop_no_resets(tmp_path, monkeypatch, capsys):
    """Test that a 5.2 second sleep BETWEEN turns doesn't trigger sandbox_reset.
    The 5.2 s idle must be BETWEEN turns, not inside the command — that is what
    makes the watchdog's 5 s worktree sample come due at the start of a short
    call and still be in flight when it returns."""
    import os
    if not os.environ.get("DIRTYWORK_LIVE_SLOW"):
        pytest.skip("Skipping slow test; set DIRTYWORK_LIVE_SLOW=1 to run")

    repo = _make_live_repo(tmp_path)
    # 40 bash calls with sleep between each
    responses = [
        _resp(tool_calls=[_call(f"c{i}", "bash", {"command": f"sed -n 1,3p README.md"})])
        for i in range(40)
    ]
    responses.append(_resp(content="done"))

    # forty identical read-only calls would trip the stall detector; it is not what this test measures
    # 40 bash turns + finish exceed the default turn cap
    _run_docker_main(monkeypatch, tmp_path, repo, responses, client_cls=_SlowClient, stall_turns=0, max_turns=60, **_image_kwargs())
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")

    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    # Expect zero sandbox_reset events
    assert not [e for e in events if e["event"] == "sandbox_reset"], f"Expected zero sandbox_reset events, but found some"
    # Expect zero stray_kill events
    assert not [e for e in events if e["event"] == "stray_kill"], f"Expected zero stray_kill events, but found some"


@pytest.mark.docker
def test_docker_live_dotnet_build_leaves_no_stray(tmp_path, monkeypatch, capsys):
    """Test that dotnet build doesn't leave stray processes."""
    image = LIVE_IMAGE or DEFAULT_IMAGE

    # Check if the image has .NET SDK
    try:
        sdks = _dotnet_list_sdks(image)
    except Exception as e:
        pytest.skip(f"Image {image} does not have .NET SDK: {e}")

    if "8.0." not in sdks and "10.0." not in sdks:
        pytest.skip(f"Image {image} does not have .NET SDK 8.0 or 10.0")

    # Read the image's env to check for daemons_off
    env_result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "/usr/bin/env", image],
        capture_output=True, text=True, timeout=60
    )
    if env_result.returncode != 0:
        pytest.skip(f"Could not read environment from image {image}: {env_result.stderr}")

    env_out = env_result.stdout
    daemons_off = "UseSharedCompilation=false" in env_out

    repo = _make_live_repo(tmp_path)
    build_cmd = (
        "dotnet new console --framework net8.0 -o app && dotnet build app"
    )
    responses = [
        _resp(tool_calls=[_call("c1", "bash", {"command": build_cmd, "timeout": 300})]),
        _resp(tool_calls=[_call("c2", "bash", {"command": "echo ok"})]),
        _resp(content="done"),
    ]

    _run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")

    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
    bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    if not bash_results[0].startswith("exit code: 0"):
        # the :0.10 base ships .NET 8 without DOTNET_EnableWriteXorExecute=0: the SDK
        # itself dies under the bash tool's ulimit -f (#70), so no daemon ever starts
        pytest.skip("dotnet build cannot run on this image (#70: .NET 8 under ulimit -f "
                    "without DOTNET_EnableWriteXorExecute=0)")

    # Expect no sandbox_reset either way
    assert not [e for e in events if e["event"] == "sandbox_reset"], f"Expected no sandbox_reset events, but found some"

    if daemons_off:
        # When daemons_off: no stray_kill event
        assert not [e for e in events if e["event"] == "stray_kill"], f"Expected no stray_kill events when daemons_off, but found some"
    else:
        # Otherwise: exactly one stray_kill whose strays contain an entry containing "VBCSCompiler"
        stray_kills = [e for e in events if e["event"] == "stray_kill"]
        assert len(stray_kills) == 1, f"Expected exactly one stray_kill event, got {len(stray_kills)}"
        assert any("VBCSCompiler" in s for s in stray_kills[0].get("strays", [])), f"Expected stray_kill to contain VBCSCompiler in strays: {stray_kills[0]}"


@pytest.mark.docker
def test_docker_live_timed_out_grep_leaves_no_stray(tmp_path, monkeypatch, capsys):
    """Test that a timed out grep doesn't leave stray processes."""
    repo = _make_live_repo(tmp_path)

    # rg blocks forever on an explicitly named FIFO (verified in the image), so the
    # grep tool's 1 s timeout is guaranteed to expire and the abandoned rg is a real
    # stray for _kill_abandoned_exec -- with nothing large left for the export.
    responses = [
        _resp(tool_calls=[_call("c1", "bash", {"command": "mkfifo slow.fifo && echo made"})]),
        _resp(tool_calls=[_call("c2", "grep", {"pattern": "x", "path": "slow.fifo", "timeout": 1})]),
        _resp(tool_calls=[_call("c3", "bash", {"command": "rm -f slow.fifo; echo ok"})]),
        _resp(content="done"),
    ]

    _run_docker_main(monkeypatch, tmp_path, repo, responses, **_image_kwargs())
    payload = json.loads(capsys.readouterr().out)
    _assert_status(payload, "completed")

    events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]

    # Find the grep tool_result
    grep_results = [e for e in events if e["event"] == "tool_result" and e["tool"] == "grep"]
    assert len(grep_results) >= 1, "Expected at least one grep tool_result"

    # The grep should have timed out
    grep_result_text = grep_results[0]["result"]
    assert "timed out" in grep_result_text.lower(), f"Expected grep to time out, got: {grep_result_text}"

    # No stray_kill and no sandbox_reset events
    assert not [e for e in events if e["event"] == "stray_kill"], f"Expected no stray_kill events, but found some"
    assert not [e for e in events if e["event"] == "sandbox_reset"], f"Expected no sandbox_reset events, but found some"

@pytest.mark.docker
def test_docker_live_fingerprint_matches_host_and_leaves_the_store_alone(tmp_path):
    """Spec §7 test 24. The fingerprint is content-addressed, so the same tree
    hashes identically on the host and inside the worker container, nested
    repositories included. The nested repositories are created on both sides:
    the docker seed (`tar --exclude=./.git`) drops every `.git` directory, not
    only the root one, so a nested repository made on the host arrives in the
    container as plain files -- a seed property, not the guard's -- and the
    container gets its own `git init` for the same content."""
    import subprocess
    from dirtywork.changes import fingerprint
    from dirtywork.sandbox import docker_args
    from dirtywork.sandbox.docker import DockerSandbox
    from dirtywork.sandbox.docker_cli import resolve_image
    from dirtywork.sandbox.host import HostSandbox
    from dirtywork.workspace import create_worktree, ensure_worktrees_excluded, worktree_base_commit

    def git(*args, cwd):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True)

    repo = _make_live_repo(tmp_path)              # README.md committed on main
    ensure_worktrees_excluded(repo)
    worktree = create_worktree(repo, "livefp", None)   # checked out: README.md present
    base_commit = worktree_base_commit(worktree)
    # nested repositories inside the worktree: a committed one and an unborn one
    inner = worktree / "vendor" / "inner"; inner.mkdir(parents=True)
    git("init", "-q", cwd=inner); (inner / "a.txt").write_text("a\n"); git("add", "-A", cwd=inner); git("commit", "-qm", "init", cwd=inner)
    unborn = worktree / "vendor" / "unborn"; unborn.mkdir()
    git("init", "-q", cwd=unborn); (unborn / "x.txt").write_text("x\n")

    fp_host, reason = fingerprint(HostSandbox(worktree))
    assert reason is None and fp_host is not None
    assert len(fp_host.splitlines()) == 4        # root tree, HEAD, two nested trees

    cfg = docker_args.DockerConfig(**_image_kwargs())
    run_dir = tmp_path / "rundir"; run_dir.mkdir()
    sb = DockerSandbox(cfg, run_dir=run_dir, image_ref=resolve_image(cfg.image))
    try:
        sb.start(worktree, repo, "livefp", base_commit, branch=None, seed_from_worktree=True)
        assert sb.bash("find . -name .git -mindepth 2", 60) == "exit code: 0\n"   # the seed flattened them
        fp_flat, reason = fingerprint(sb)
        assert reason is None and len(fp_flat.splitlines()) == 2 and fp_flat != fp_host
        sb.bash("git -C vendor/inner init -q && git -C vendor/inner add -A && "
                "git -C vendor/inner -c user.email=t@t -c user.name=t commit -qm init && "
                "git -C vendor/unborn init -q", 60)
        fp_docker, reason = fingerprint(sb)
        assert reason is None
        assert fp_docker == fp_host              # content-addressed, sorted: identical on host and in the container
        count_cmd = "find /gitdir/objects -type f | wc -l"
        before = sb.bash(count_cmd, 60).split("\n")[1].strip()
        sb.bash("head -c 102400 /dev/urandom > big.bin", 60)     # a new untracked 100 KB file
        fp2, reason = fingerprint(sb)
        assert reason is None and fp2 != fp_docker
        after = sb.bash(count_cmd, 60).split("\n")[1].strip()
        assert before == after                    # the scratch object directory: the real store did not grow
        sb.bash("cp README.md /tmp/r && cp /tmp/r README.md", 60)   # byte-identical rewrite
        fp3, reason = fingerprint(sb)
        assert reason is None and fp3 == fp2
    finally:
        sb.stop()
