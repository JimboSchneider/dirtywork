from __future__ import annotations

import json
from pathlib import Path

import pytest

from dirtywork.__main__ import build_system_prompt, main
from dirtywork.sandbox.docker_cli import DockerError


def test_main_docker_preflight_failure_exits_2_with_hint(tmp_path, monkeypatch, capsys):
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])

    def boom(*a, **k):
        raise DockerError("Cannot connect to the Docker daemon")

    monkeypatch.setattr(m, "docker_version", boom)

    rc = m.main(["run", "--repo", str(repo), "some task"])  # --sandbox defaults to docker

    assert rc == 2
    err = capsys.readouterr().err
    assert "Docker" in err
    assert "Start Docker Desktop" in err
    assert "--sandbox none" in err


def test_main_docker_preflight_image_failure_exits_2_with_image_hint(tmp_path, monkeypatch, capsys):
    # Fix item 6: an image resolution failure (unpullable image, pinned-digest
    # mismatch) is NOT the daemon's fault -- the exit-2 hint must not tell the
    # operator to start Docker Desktop (it's already running; docker_version
    # succeeded). It should point at building/pulling the image or --image.
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])
    monkeypatch.setattr(m, "docker_version", lambda *a, **k: "29.7.2")  # daemon IS reachable

    def boom(*a, **k):
        raise DockerError("docker pull dirtywork/worker:0.4 failed: manifest unknown")

    monkeypatch.setattr(m, "resolve_image", boom)

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "manifest unknown" in err
    assert "Start Docker Desktop" not in err
    assert "--image" in err
    assert "--sandbox none" in err


def test_main_docker_mode_happy_path_with_fake_sandbox(tmp_path, monkeypatch, capsys):
    # Real coverage of the finalize path (fix item 7): Runner.run is NOT
    # monkeypatched away here -- a fake LLM client drives it for real (one
    # tool call, then a plain completion), so finalize() is actually invoked
    # by Runner.finish() rather than skipped by short-circuiting the loop.
    import subprocess
    import dirtywork.__main__ as m
    from dirtywork.sandbox import RunArtifacts
    from dirtywork.sandbox.docker import DockerSandbox as RealDockerSandbox
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m, "docker_version", lambda *a, **k: "29.7.2")
    monkeypatch.setattr(m, "resolve_image", lambda *a, **k: "dirtywork/worker@sha256:" + "a" * 64)
    monkeypatch.setattr(m, "validate_objects_dir", lambda repo: repo / ".git" / "objects")
    # the pre-worktree collision check inspects the container/volume names;
    # rc 1 == "no such object" == no collision
    from dirtywork.procs import Captured
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(1, b"", False, False))

    class FakeWatchdog:
        def start(self):
            pass

    class FakeDockerSandbox:
        # Delegate to the real static method instead of re-implementing the
        # collision-check logic in a test double.
        check_name_collision = staticmethod(RealDockerSandbox.check_name_collision)

        def __init__(self, cfg, *, run_dir, transcript=None):
            self.cfg = cfg
            self.run_dir = run_dir
            self.uid = 501
            self.gid = 20
            self.watchdog = FakeWatchdog()

        def start(self, worktree, repo, slug, base_commit):
            pass

        def stop(self):
            pass

        def read_file(self, path: str, offset: int = 0, limit: int = 400) -> str:
            return ""

        def write_file(self, path: str, content: str) -> str:
            return ""

        def edit_file(self, path: str, old_string: str, new_string: str) -> str:
            return ""

        def list_dir(self, path: str = ".") -> str:
            return ""

        def grep(self, pattern: str, path: str = ".", glob: str | None = None,
                 timeout: int = 30) -> str:
            return ""

        def bash(self, command: str, timeout: int = 120) -> str:
            return ""

        def finalize(self):
            return RunArtifacts(export_status="ok", diff_stat="1 file changed")

    monkeypatch.setattr(m, "DockerSandbox", FakeDockerSandbox)

    class WritingFakeClient:
        def __init__(self, base_url=None):
            self.calls = 0

        def list_models(self):
            return [m.DEFAULT_MODEL]

        def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
            self.calls += 1
            if self.calls == 1:
                return {"choices": [{"message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "c1", "type": "function",
                                     "function": {"name": "write_file",
                                                  "arguments": json.dumps(
                                                      {"path": "hi.txt", "content": "hi\n"})}}],
                }}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(m, "LMStudioClient", WritingFakeClient)

    rc = m.main(["run", "--repo", str(repo), "some task"])  # --sandbox defaults to docker

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 2
    assert "run_dir" in payload
    assert payload["status"] == "completed"

    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["status"] == "completed"
    assert run_json["export_status"] == "ok"

    transcript_files = list((tmp_path / "runs").rglob("transcript.jsonl"))
    events = [json.loads(l) for l in transcript_files[0].read_text().splitlines()]
    run_start = next(e for e in events if e["event"] == "run_start")
    assert run_start["sandbox"]["backend"] == "docker"
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end["export_status"] == "ok"
    assert run_end["diff_stat"] == "1 file changed"


def _docker_mode_scaffold(tmp_path, monkeypatch):
    """Shared setup for the docker-mode CLI tests below: a one-commit repo,
    LM Studio and docker preflight faked, run dir under tmp_path. Returns
    (module, repo)."""
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])
    monkeypatch.setattr(m, "docker_version", lambda *a, **k: "29.7.2")
    monkeypatch.setattr(m, "resolve_image", lambda *a, **k: "dirtywork/worker@sha256:" + "a" * 64)
    monkeypatch.setattr(m, "validate_objects_dir", lambda repo: repo / ".git" / "objects")
    return m, repo


def test_main_docker_name_collision_exits_2_and_creates_nothing(tmp_path, monkeypatch, capsys):
    import subprocess
    from dirtywork.procs import Captured
    m, repo = _docker_mode_scaffold(tmp_path, monkeypatch)
    # `docker container inspect dw-<slug>` succeeds → the name is taken
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(0, b"[{}]", False, False))

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "already exists" in err
    assert "docker rm -f" in err and "docker volume rm" in err
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())
    wl = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                        capture_output=True, text=True).stdout
    assert wl.count("worktree ") == 1  # only the main checkout


def test_main_docker_start_failure_is_sandbox_error_exit_1(tmp_path, monkeypatch, capsys):
    import subprocess
    from dirtywork.procs import Captured
    from dirtywork.sandbox import SandboxError
    from dirtywork.sandbox.docker import DockerSandbox as RealDockerSandbox
    m, repo = _docker_mode_scaffold(tmp_path, monkeypatch)
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(1, b"", False, False))

    class BoomSandbox:
        check_name_collision = staticmethod(RealDockerSandbox.check_name_collision)

        def __init__(self, cfg, *, run_dir, transcript=None):
            self.uid, self.gid = 501, 20
            self.stopped = False

        def start(self, worktree, repo, slug, base_commit):
            raise SandboxError("in-container git init failed: rc 128")

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(m, "DockerSandbox", BoomSandbox)

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "sandbox_error"
    assert "git init failed" in payload["final_message"]
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["status"] == "sandbox_error"

    # Binding adjustment #1 / fix item 4: a docker setup failure must not
    # orphan the worktree + branch create_worktree already made.
    slug = Path(payload["run_dir"]).name
    assert not (repo / ".worktrees" / f"dw-{slug}").exists()
    branch_check = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
         f"refs/heads/dirtywork/{slug}"],
        capture_output=True,
    )
    assert branch_check.returncode != 0


def test_main_docker_sandbox_error_mid_run_exits_1(tmp_path, monkeypatch, capsys):
    # Fix item 8 / release-gate coverage: stands in for the spec's Task 16
    # case 3, "Docker daemon unavailable mid-run" -- a controller ruling
    # noted that scenario cannot be reproduced in a unit test by actually
    # killing the daemon, so this drives the same observable contract with a
    # fake DockerSandbox whose bash() raises SandboxError("daemon
    # unreachable") on the first tool call, after start() already succeeded.
    # Runner.run()'s own `except SandboxError` (not a re-raise main() has to
    # catch) converts this to status sandbox_error and still calls
    # finalize() (best effort) via Runner.finish() before returning. The
    # worktree must NOT be removed: unlike a preflight-shaped failure
    # (_fail_setup, exercised by test_main_docker_start_failure_is_
    # sandbox_error_exit_1 above), the run had already begun, so main()
    # never reaches the rollback path.
    from dirtywork.procs import Captured
    from dirtywork.sandbox import RunArtifacts, SandboxError
    from dirtywork.sandbox.docker import DockerSandbox as RealDockerSandbox
    m, repo = _docker_mode_scaffold(tmp_path, monkeypatch)
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(1, b"", False, False))

    finalize_calls = []

    def finalize(self):
        finalize_calls.append(1)
        return RunArtifacts(export_status="ok")

    def boom_bash(self, command, timeout=120):
        raise SandboxError("daemon unreachable")

    FakeDockerSandbox = _fake_docker_sandbox_class(RealDockerSandbox, finalize=finalize)
    FakeDockerSandbox.bash = boom_bash
    monkeypatch.setattr(m, "DockerSandbox", FakeDockerSandbox)

    class OneBashCallClient:
        def __init__(self, base_url=None):
            pass

        def list_models(self):
            return [m.DEFAULT_MODEL]

        def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
            return {"choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function",
                                 "function": {"name": "bash",
                                              "arguments": json.dumps({"command": "echo hi"})}}],
            }}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(m, "LMStudioClient", OneBashCallClient)

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "sandbox_error"
    assert "daemon unreachable" in payload["final_message"]
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["status"] == "sandbox_error"
    assert finalize_calls == [1]  # finalize attempted best-effort, per the docker error path

    slug = Path(payload["run_dir"]).name
    assert (repo / ".worktrees" / f"dw-{slug}").exists()  # NOT removed -- the run had begun


def _fake_docker_sandbox_class(RealDockerSandbox, *, finalize):
    """Shared skeleton for the fake DockerSandbox classes below: a real
    check_name_collision, a no-op start/stop, and just enough of the tool
    surface for ToolExecutor to dispatch a write_file call. `finalize` is
    the body of the fake's finalize() method."""

    class FakeDockerSandbox:
        check_name_collision = staticmethod(RealDockerSandbox.check_name_collision)

        def __init__(self, cfg, *, run_dir, transcript=None):
            self.cfg = cfg
            self.uid, self.gid = 501, 20
            self.volume = "dw-fake-work"
            self.stopped = False

            class FakeWatchdog:
                def start(self):
                    pass

            self.watchdog = FakeWatchdog()

        def start(self, worktree, repo, slug, base_commit):
            pass

        def stop(self):
            self.stopped = True

        def read_file(self, path, offset=0, limit=400):
            return ""

        def write_file(self, path, content):
            return ""

        def edit_file(self, path, old_string, new_string):
            return ""

        def list_dir(self, path="."):
            return ""

        def grep(self, pattern, path=".", glob=None, timeout=30):
            return ""

        def bash(self, command, timeout=120):
            return ""

    FakeDockerSandbox.finalize = finalize
    return FakeDockerSandbox


def test_main_docker_llm_error_after_start_finalizes_before_stop(tmp_path, monkeypatch, capsys):
    # Fix item 5: an LLMError raised by client.chat() is not caught inside
    # Runner.run()'s own try/except, so it escapes to main()'s exception
    # handler. By then the sandbox has started and the agent already ran one
    # tool call -- finalize() must still be attempted (best effort) before
    # `finally: sandbox.stop()` would otherwise discard the volume.
    from dirtywork.llm import LLMError
    from dirtywork.procs import Captured
    from dirtywork.sandbox import RunArtifacts
    from dirtywork.sandbox.docker import DockerSandbox as RealDockerSandbox
    m, repo = _docker_mode_scaffold(tmp_path, monkeypatch)
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(1, b"", False, False))

    finalize_calls = []

    def finalize(self):
        finalize_calls.append(1)
        return RunArtifacts(export_status="ok")

    FakeDockerSandbox = _fake_docker_sandbox_class(RealDockerSandbox, finalize=finalize)
    monkeypatch.setattr(m, "DockerSandbox", FakeDockerSandbox)

    class FlakyClient:
        def __init__(self, base_url=None):
            self.calls = 0

        def list_models(self):
            return [m.DEFAULT_MODEL]

        def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
            self.calls += 1
            if self.calls == 1:
                return {"choices": [{"message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "c1", "type": "function",
                                     "function": {"name": "write_file",
                                                  "arguments": json.dumps(
                                                      {"path": "hi.txt", "content": "hi\n"})}}],
                }}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            raise LLMError("connection dropped")

    monkeypatch.setattr(m, "LMStudioClient", FlakyClient)

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "model_error"
    assert payload["export_status"] == "ok"
    assert finalize_calls == [1]


def test_main_docker_export_failed_status_from_finalize_result(tmp_path, monkeypatch, capsys):
    # Fix item 6, trigger 1: Runner.run() completes normally and finalize()
    # (called inside Runner.finish()) returns an export_status that starts
    # with "export_failed" -- _final_status() must override the terminal
    # status to "export_failed" even though Runner itself reported
    # "completed".
    from dirtywork.procs import Captured
    from dirtywork.sandbox import RunArtifacts
    from dirtywork.sandbox.docker import DockerSandbox as RealDockerSandbox
    m, repo = _docker_mode_scaffold(tmp_path, monkeypatch)
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(1, b"", False, False))

    def finalize(self):
        return RunArtifacts(export_status="export_failed: boom")

    FakeDockerSandbox = _fake_docker_sandbox_class(RealDockerSandbox, finalize=finalize)
    monkeypatch.setattr(m, "DockerSandbox", FakeDockerSandbox)

    class ImmediateDoneClient:
        def __init__(self, base_url=None):
            pass

        def list_models(self):
            return [m.DEFAULT_MODEL]

        def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(m, "LMStudioClient", ImmediateDoneClient)

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "export_failed"
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["status"] == "export_failed"
    assert run_json["export_status"] == "export_failed: boom"


def test_main_docker_export_failed_status_from_finalize_exception(tmp_path, monkeypatch, capsys):
    # Fix item 6, trigger 2: finalize() itself raises. Runner.finish() (SP1)
    # catches it and puts finalize_error into result.extra instead of
    # export_status -- _final_status() must treat a present finalize_error
    # the same as an export_failed export_status.
    from dirtywork.procs import Captured
    from dirtywork.sandbox.docker import DockerSandbox as RealDockerSandbox
    m, repo = _docker_mode_scaffold(tmp_path, monkeypatch)
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(1, b"", False, False))

    def finalize(self):
        raise RuntimeError("export exploded")

    FakeDockerSandbox = _fake_docker_sandbox_class(RealDockerSandbox, finalize=finalize)
    monkeypatch.setattr(m, "DockerSandbox", FakeDockerSandbox)

    class ImmediateDoneClient:
        def __init__(self, base_url=None):
            pass

        def list_models(self):
            return [m.DEFAULT_MODEL]

        def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(m, "LMStudioClient", ImmediateDoneClient)

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "export_failed"
    assert payload["finalize_error"]
    assert "export exploded" in payload["finalize_error"]
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["status"] == "export_failed"
    assert run_json["finalize_error"]


def test_main_docker_export_failed_with_budget_exceeded_status(tmp_path, monkeypatch, capsys):
    # Fix item 4: export_failed should only replace 'completed', not other
    # statuses. Drive a real Runner (like the happy-path test above): the
    # fake sandbox's bash() raises BudgetExceeded, which runner.run() itself
    # catches and turns into a normal "budget_exceeded" RunResult -- then
    # Runner.finish() calls finalize(), which here reports an export_failed
    # export_status. _final_status() must keep "budget_exceeded" (the actual
    # cause), not let the export failure overwrite it.
    from dirtywork.budget import BudgetExceeded
    from dirtywork.procs import Captured
    from dirtywork.sandbox import RunArtifacts
    from dirtywork.sandbox.docker import DockerSandbox as RealDockerSandbox
    m, repo = _docker_mode_scaffold(tmp_path, monkeypatch)
    # rc 1 == "no such object" == no collision, same as the sibling tests
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(1, b"", False, False))

    def finalize(self):
        return RunArtifacts(export_status="export_failed: x")

    FakeDockerSandbox = _fake_docker_sandbox_class(RealDockerSandbox, finalize=finalize)

    def boom_bash(self, command, timeout=120):
        raise BudgetExceeded("worktree exceeds 2048 MB or 200000 files")

    FakeDockerSandbox.bash = boom_bash
    monkeypatch.setattr(m, "DockerSandbox", FakeDockerSandbox)

    class BashCallingClient:
        def __init__(self, base_url=None):
            pass

        def list_models(self):
            return [m.DEFAULT_MODEL]

        def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
            return {"choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function",
                                 "function": {"name": "bash",
                                              "arguments": json.dumps({"command": "echo hi"})}}],
            }}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(m, "LMStudioClient", BashCallingClient)

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    # Status should stay budget_exceeded, not be replaced by export_failed
    assert payload["status"] == "budget_exceeded"
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["status"] == "budget_exceeded"
    # But the export failure is still visible via export_status in run.json
    assert run_json["export_status"].startswith("export_failed")


def test_build_system_prompt_includes_rules_and_context(tmp_path: Path):
    p = build_system_prompt(tmp_path, "REPO RULES HERE")
    assert str(tmp_path) in p
    assert "edit_file" in p
    assert "REPO RULES HERE" in p
    assert "uncommitted" in p


def test_build_system_prompt_no_context(tmp_path: Path):
    p = build_system_prompt(tmp_path, None)
    assert "Repository conventions" not in p


def test_main_bad_repo_exits_2(tmp_path: Path, capsys):
    rc = main(["run", "--repo", str(tmp_path / "nope"), "do things"])
    assert rc == 2
    assert "error" in capsys.readouterr().err.lower()


def test_main_lmstudio_down_exits_2(tmp_path: Path, capsys, monkeypatch):
    import subprocess
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    rc = main(["run", "--repo", str(repo), "--base-url",
               "http://127.0.0.1:1/v1", "do things"])
    assert rc == 2


def test_transcript_closed_even_on_unexpected_error(tmp_path, monkeypatch, capsys):
    # Machine contract: every post-preflight run prints exactly one JSON object,
    # even on an exception the runner doesn't itself convert to a status (e.g. a
    # bare RuntimeError escaping runner.run). No traceback, no missing run_end.
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    closed = {}
    class SpyTranscript(m.Transcript):
        def close(self):
            closed["yes"] = True
            super().close()
    monkeypatch.setattr(m, "Transcript", SpyTranscript)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])
    def boom(self, system_prompt, task):
        raise RuntimeError("boom")
    monkeypatch.setattr(m.Runner, "run", boom)

    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "model_error"
    assert "unexpected" in payload["final_message"]
    assert closed.get("yes")

    transcript_files = list((tmp_path / "runs").rglob("transcript.jsonl"))
    assert len(transcript_files) == 1
    events = [json.loads(line) for line in transcript_files[0].read_text().splitlines()]
    assert events[-1]["event"] == "run_end"


def test_transcript_construction_failure_still_prints_json(tmp_path, monkeypatch, capsys):
    # The JSON exception boundary must cover more than runner.run() -- a failure
    # constructing Transcript itself (e.g. disk unavailable) must still produce
    # the documented stdout JSON instead of a traceback.
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)

    class BrokenTranscript:
        def __init__(self, path):
            raise OSError("disk unavailable")

    monkeypatch.setattr(m, "Transcript", BrokenTranscript)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])

    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "model_error"
    assert "disk unavailable" in payload["final_message"]


def test_load_repo_context_uses_worktree_not_caller_checkout(tmp_path, monkeypatch):
    import subprocess
    import dirtywork.__main__ as m
    from dirtywork.runner import RunResult
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    (repo / "CLAUDE.md").write_text("CONVENTIONS-FROM-COMMIT")
    subprocess.run(["git", "-C", str(repo), "add", "CLAUDE.md"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-m", "add conventions"],
                   capture_output=True)
    # Dirty the working tree AFTER the commit — the worktree branches from
    # HEAD (the commit), so it must never see this uncommitted content.
    (repo / "CLAUDE.md").write_text("CONVENTIONS-DIRTY")

    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])

    captured = {}

    def fake_run(self, system_prompt, task):
        captured["system_prompt"] = system_prompt
        return RunResult("completed", 1, "ok", {})

    monkeypatch.setattr(m.Runner, "run", fake_run)

    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"])
    assert rc == 0
    assert "CONVENTIONS-FROM-COMMIT" in captured["system_prompt"]
    assert "CONVENTIONS-DIRTY" not in captured["system_prompt"]


def test_llm_error_during_run_prints_model_error_json(tmp_path, monkeypatch, capsys):
    import subprocess
    import dirtywork.__main__ as m
    from dirtywork.llm import LLMError
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])

    def boom(self, system_prompt, task):
        raise LLMError("boom")
    monkeypatch.setattr(m.Runner, "run", boom)

    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"])
    assert rc == 1
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["status"] == "model_error"
    assert "worktree" in payload


def test_run_start_has_all_provenance_fields(tmp_path, monkeypatch):
    # Runner.run() itself writes the run_start transcript event — replacing
    # Runner.run wholesale (as other tests in this file do to short-circuit
    # the agent loop) would skip that write entirely. Drive a minimal fake
    # LLM client through the REAL Runner.run() instead, so run_start is
    # actually emitted.
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")

    class ImmediateDoneClient:
        def __init__(self, base_url=None):
            pass

        def list_models(self):
            return [m.DEFAULT_MODEL]

        def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(m, "LMStudioClient", ImmediateDoneClient)

    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"])
    assert rc == 0

    transcript_files = list((tmp_path / "runs").rglob("transcript.jsonl"))
    events = [json.loads(l) for l in transcript_files[0].read_text().splitlines()]
    run_start = next(e for e in events if e["event"] == "run_start")
    for key in ("base_commit", "branch", "branch_from", "base_url",
                "dirtywork_version", "temperature", "sandbox", "provider"):
        assert key in run_start, key
    assert run_start["sandbox"] == "none"
    assert run_start["provider"] == "openai"


def test_run_end_has_diff_stat_after_writing_tracked_file(tmp_path, monkeypatch):
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    (repo / "existing.txt").write_text("original\n")
    subprocess.run(["git", "-C", str(repo), "add", "existing.txt"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-m", "init"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")

    class WritingFakeClient:
        def __init__(self, base_url=None):
            self.calls = 0

        def list_models(self):
            return [m.DEFAULT_MODEL]

        def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
            self.calls += 1
            if self.calls == 1:
                return {"choices": [{"message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "c1", "type": "function",
                                     "function": {"name": "write_file",
                                                  "arguments": json.dumps(
                                                      {"path": "existing.txt", "content": "changed\n"})}}],
                }}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(m, "LMStudioClient", WritingFakeClient)

    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"])
    assert rc == 0

    transcript_files = list((tmp_path / "runs").rglob("transcript.jsonl"))
    events = [json.loads(l) for l in transcript_files[0].read_text().splitlines()]
    run_end = next(e for e in events if e["event"] == "run_end")
    assert "diff_stat" in run_end
    assert "existing.txt" in run_end["diff_stat"]


def test_run_end_has_untracked_after_writing_new_file(tmp_path, monkeypatch):
    # A model deliverable that's a brand-new file it never `git add`ed is
    # invisible to diff_stat (tracked changes only) — this pins that
    # untracked picks it up instead.
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    (repo / "existing.txt").write_text("original\n")
    subprocess.run(["git", "-C", str(repo), "add", "existing.txt"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-m", "init"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")

    class WritingFakeClient:
        def __init__(self, base_url=None):
            self.calls = 0

        def list_models(self):
            return [m.DEFAULT_MODEL]

        def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
            self.calls += 1
            if self.calls == 1:
                return {"choices": [{"message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "c1", "type": "function",
                                     "function": {"name": "write_file",
                                                  "arguments": json.dumps(
                                                      {"path": "brand_new.txt", "content": "hi\n"})}}],
                }}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setattr(m, "LMStudioClient", WritingFakeClient)

    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"])
    assert rc == 0

    transcript_files = list((tmp_path / "runs").rglob("transcript.jsonl"))
    events = [json.loads(l) for l in transcript_files[0].read_text().splitlines()]
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end["untracked"] == "brand_new.txt"
    assert run_end["diff_stat"] == ""


def test_rundir_error_exits_2(tmp_path, monkeypatch):
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(m, "RUNS_DIR", runs_dir)
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])
    monkeypatch.setattr(m, "make_slug", lambda task, now: "fixed-slug")
    runs_dir.mkdir(parents=True)
    (runs_dir / "fixed-slug").mkdir()  # pre-existing run dir collides

    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"])
    assert rc == 2


def test_rundir_error_removes_orphaned_worktree(tmp_path, monkeypatch):
    # create_worktree already succeeded by the time RunDirError fires, so
    # without rollback the worktree dir + branch are silently orphaned.
    import subprocess
    import dirtywork.__main__ as m
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(m, "RUNS_DIR", runs_dir)
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])
    monkeypatch.setattr(m, "make_slug", lambda task, now: "fixed-slug")
    runs_dir.mkdir(parents=True)
    (runs_dir / "fixed-slug").mkdir()  # pre-existing run dir collides

    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"])
    assert rc == 2

    assert not (repo / ".worktrees" / "dw-fixed-slug").exists()
    branch_check = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
         "refs/heads/dirtywork/fixed-slug"],
        capture_output=True,
    )
    assert branch_check.returncode != 0


def test_stdout_json_has_run_dir_and_base_commit(tmp_path, monkeypatch, capsys):
    import subprocess
    import dirtywork.__main__ as m
    from dirtywork.runner import RunResult
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "--allow-empty", "-m", "i"],
                   capture_output=True)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(m.LMStudioClient, "list_models", lambda self: [m.DEFAULT_MODEL])
    monkeypatch.setattr(m.Runner, "run", lambda self, sp, t: RunResult("completed", 1, "ok", {}))

    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "some task"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_dir"].endswith("run_dir_placeholder") is False  # sanity: it's a real path
    assert "runs" in payload["run_dir"]
    assert payload["base_commit"]
    # existing contract fields must still be present and unrenamed
    for key in ("status", "worktree", "branch", "transcript", "turns", "usage", "final_message"):
        assert key in payload
