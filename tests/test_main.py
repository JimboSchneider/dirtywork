from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from dirtywork.__main__ import build_system_prompt, main
from dirtywork.sandbox.docker_cli import DockerError

from .provider_doubles import (DictProvider, PreflightProvider, patch_provider,
                               text_body, tool_call_body)


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
    patch_provider(monkeypatch, m, PreflightProvider)

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
    patch_provider(monkeypatch, m, PreflightProvider)
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

        def __init__(self, cfg, *, run_dir, transcript=None, image_ref=None):
            self.cfg = cfg
            self.run_dir = run_dir
            self.uid = 501
            self.gid = 20
            self.image_ref = image_ref
            self.watchdog = FakeWatchdog()

        def start(self, worktree, repo, slug, base_commit, **kwargs):
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

    class WritingFakeClient(DictProvider):
        def reply(self, model, messages, tools):
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

    patch_provider(monkeypatch, m, lambda base_url=None: WritingFakeClient(base_url))

    rc = m.main(["run", "--repo", str(repo), "some task"])  # --sandbox defaults to docker

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 2
    assert "run_dir" in payload
    assert payload["status"] == "completed"

    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["status"] == "completed"
    assert run_json["export_status"] == "ok"
    # image_digest is provenance (the registry digest from RepoDigests, or
    # None for a locally-built/never-pulled image) -- distinct from the
    # image_ref (Id) actually used to run the container. The fake
    # docker_cli.run above returns rc=1 for every call, so
    # image_repo_digest() can't inspect RepoDigests and falls back to None.
    assert run_json["image_digest"] is None

    transcript_files = list((tmp_path / "runs").rglob("transcript.jsonl"))
    events = [json.loads(l) for l in transcript_files[0].read_text().splitlines()]
    run_start = next(e for e in events if e["event"] == "run_start")
    assert run_start["sandbox"]["backend"] == "docker"
    assert run_start["sandbox"]["image"] == m.DEFAULT_IMAGE
    assert run_start["sandbox"]["image_digest"] is None
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
    patch_provider(monkeypatch, m, PreflightProvider)
    monkeypatch.setattr(m, "docker_version", lambda *a, **k: "29.7.2")
    monkeypatch.setattr(m, "resolve_image", lambda *a, **k: "dirtywork/worker@sha256:" + "a" * 64)
    monkeypatch.setattr(m, "validate_objects_dir", lambda repo: repo / ".git" / "objects")
    return m, repo


def test_main_docker_build_sandbox_passes_preflight_image_ref(tmp_path, monkeypatch, capsys):
    # Fix item 2: _docker_preflight already resolved the image to its local
    # Id (for EXECUTION) before anything is created -- _build_sandbox must
    # hand that exact same value to DockerSandbox's constructor, so start()
    # doesn't spend a second `docker image inspect`/`pull` round trip
    # resolving it again. image_digest (run.json, PROVENANCE only) is a
    # separate value from image_repo_digest() -- None here since the fake
    # docker_cli.run below returns rc=1 for every call, so RepoDigests can't
    # be inspected.
    from dirtywork.procs import Captured
    from dirtywork.sandbox import RunArtifacts
    from dirtywork.sandbox.docker import DockerSandbox as RealDockerSandbox
    m, repo = _docker_mode_scaffold(tmp_path, monkeypatch)
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(1, b"", False, False))

    constructed_with = []

    class FakeWatchdog:
        def start(self):
            pass

    class FakeDockerSandbox:
        check_name_collision = staticmethod(RealDockerSandbox.check_name_collision)

        def __init__(self, cfg, *, run_dir, transcript=None, image_ref=None):
            constructed_with.append(image_ref)
            self.uid, self.gid = 501, 20
            self.watchdog = FakeWatchdog()

        def start(self, worktree, repo, slug, base_commit, **kwargs):
            pass

        def stop(self):
            pass

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

        def finalize(self):
            return RunArtifacts(export_status="ok")

    monkeypatch.setattr(m, "DockerSandbox", FakeDockerSandbox)

    class ImmediateDoneClient(DictProvider):


        def reply(self, model, messages, tools):
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    patch_provider(monkeypatch, m, ImmediateDoneClient)

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    preflight_image_ref = "dirtywork/worker@sha256:" + "a" * 64  # whatever resolve_image returned
    assert constructed_with == [preflight_image_ref]  # the exact value _build_sandbox passed in
    assert run_json["image_digest"] is None  # provenance, resolved separately from image_ref


def _install_immediate_done_docker_fakes(m, monkeypatch, *, constructed_with=None):
    """Shared plumbing for the two image_pinned tests below: a
    FakeDockerSandbox that records the image_ref it was constructed with (if
    a list is given) and completes start()/finalize() as no-ops, plus an
    LM Studio client that replies "done" on the first turn -- just enough
    for main() to reach rc 0 and a written run.json without a real Docker
    daemon or a real LLM."""
    from dirtywork.sandbox import RunArtifacts
    from dirtywork.sandbox.docker import DockerSandbox as RealDockerSandbox

    class FakeWatchdog:
        def start(self):
            pass

    class FakeDockerSandbox:
        check_name_collision = staticmethod(RealDockerSandbox.check_name_collision)

        def __init__(self, cfg, *, run_dir, transcript=None, image_ref=None):
            if constructed_with is not None:
                constructed_with.append(image_ref)
            self.uid, self.gid = 501, 20
            self.watchdog = FakeWatchdog()

        def start(self, worktree, repo, slug, base_commit, **kwargs):
            pass

        def stop(self):
            pass

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

        def finalize(self):
            return RunArtifacts(export_status="ok")

    monkeypatch.setattr(m, "DockerSandbox", FakeDockerSandbox)

    class ImmediateDoneClient(DictProvider):


        def reply(self, model, messages, tools):
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    patch_provider(monkeypatch, m, ImmediateDoneClient)


def test_main_docker_custom_image_skips_pinning(tmp_path, monkeypatch, capsys):
    # Item 3: a user-supplied --image is never pinned, even when
    # PINNED_DIGEST is set for the default image -- the operator chose this
    # image deliberately. resolve_image must be called with pinned_digest
    # None for it (not skipped, so any image still resolves/warns/errors
    # exactly as it would for a bare "no pin configured" run -- it just
    # never compares against PINNED_DIGEST).
    from dirtywork.procs import Captured
    m, repo = _docker_mode_scaffold(tmp_path, monkeypatch)
    monkeypatch.setattr(m.docker_args, "PINNED_DIGEST", "sha256:" + "f" * 64)

    calls = []

    def fake_resolve_image(image, *, pinned_digest=None, run=None):
        calls.append((image, pinned_digest))
        return "sha256:" + "b" * 64

    monkeypatch.setattr(m, "resolve_image", fake_resolve_image)
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(1, b"", False, False))
    _install_immediate_done_docker_fakes(m, monkeypatch)

    rc = m.main(["run", "--repo", str(repo), "--image", "custom-image:latest", "some task"])

    assert rc == 0
    assert calls == [("custom-image:latest", None)]  # pin not passed for a custom image
    payload = json.loads(capsys.readouterr().out)
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["image_pinned"] is False


def test_main_docker_run_json_records_image_pinned_true_for_pulled_default_image(
        tmp_path, monkeypatch, capsys):
    # Item 3: image_pinned is true only when the pin was actually enforced
    # against a pulled default image -- i.e. --image was not given (so the
    # default image is used) AND the image has a RepoDigests entry matching
    # PINNED_DIGEST (a locally built image with no RepoDigests would warn,
    # not enforce -- see test_docker_cli.py's local-build test).
    from dirtywork.procs import Captured
    m, repo = _docker_mode_scaffold(tmp_path, monkeypatch)
    pinned = "sha256:" + "a" * 64
    monkeypatch.setattr(m.docker_args, "PINNED_DIGEST", pinned)
    name = m.DEFAULT_IMAGE.rsplit(":", 1)[0]
    repo_digest = f"{name}@{pinned}"

    def fake_docker_cli_run(argv, *, timeout, stdin=None):
        if argv[:2] == ["image", "inspect"]:
            return Captured(0, json.dumps([repo_digest]).encode(), False, False)
        return Captured(1, b"", False, False)  # container/volume collision checks: not found

    monkeypatch.setattr(m.docker_cli, "run", fake_docker_cli_run)
    _install_immediate_done_docker_fakes(m, monkeypatch)

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["image_digest"] == repo_digest
    assert run_json["image_pinned"] is True


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

        def __init__(self, cfg, *, run_dir, transcript=None, image_ref=None):
            self.uid, self.gid = 501, 20
            self.stopped = False

        def start(self, worktree, repo, slug, base_commit, **kwargs):
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

    class OneBashCallClient(DictProvider):
        def reply(self, model, messages, tools):
            return {"choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function",
                                 "function": {"name": "bash",
                                              "arguments": json.dumps({"command": "echo hi"})}}],
            }}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    patch_provider(monkeypatch, m, OneBashCallClient)

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

        def __init__(self, cfg, *, run_dir, transcript=None, image_ref=None):
            self.cfg = cfg
            self.uid, self.gid = 501, 20
            self.volume = "dw-fake-work"
            self.image_ref = image_ref
            self.stopped = False

            class FakeWatchdog:
                def start(self):
                    pass

            self.watchdog = FakeWatchdog()

        def start(self, worktree, repo, slug, base_commit, **kwargs):
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

    class FlakyClient(DictProvider):
        def reply(self, model, messages, tools):
            if self.calls == 1:
                return {"choices": [{"message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "c1", "type": "function",
                                     "function": {"name": "write_file",
                                                  "arguments": json.dumps(
                                                      {"path": "hi.txt", "content": "hi\n"})}}],
                }}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            raise LLMError("connection dropped")

    patch_provider(monkeypatch, m, FlakyClient)

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

    class ImmediateDoneClient(DictProvider):


        def reply(self, model, messages, tools):
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    patch_provider(monkeypatch, m, ImmediateDoneClient)

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "export_failed"
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["status"] == "export_failed"
    assert run_json["export_status"] == "export_failed: boom"


def test_main_docker_watchdog_violation_status_from_finalize_result(tmp_path, monkeypatch, capsys):
    # Fix item 1: finalize() (called inside Runner.finish() after a normal
    # "completed" run) reports a watchdog_violation -- a disk-floor or
    # fail-closed kill that fired while the model was idle, after its last
    # tool call, so there was no bash call left to surface it via
    # _after_bash's BudgetExceeded raise. _final_status() must override the
    # terminal status to "budget_exceeded", not let it report "completed".
    from dirtywork.procs import Captured
    from dirtywork.sandbox import RunArtifacts
    from dirtywork.sandbox.docker import DockerSandbox as RealDockerSandbox
    m, repo = _docker_mode_scaffold(tmp_path, monkeypatch)
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(1, b"", False, False))

    def finalize(self):
        return RunArtifacts(export_status="ok",
                             watchdog_violation="host free space below 2048 MB")

    FakeDockerSandbox = _fake_docker_sandbox_class(RealDockerSandbox, finalize=finalize)
    monkeypatch.setattr(m, "DockerSandbox", FakeDockerSandbox)

    class ImmediateDoneClient(DictProvider):


        def reply(self, model, messages, tools):
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    patch_provider(monkeypatch, m, ImmediateDoneClient)

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "budget_exceeded"
    assert payload["watchdog_violation"] == "host free space below 2048 MB"
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["status"] == "budget_exceeded"
    assert run_json["watchdog_violation"] == "host free space below 2048 MB"

    transcript_files = list((tmp_path / "runs").rglob("transcript.jsonl"))
    events = [json.loads(l) for l in transcript_files[0].read_text().splitlines()]
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end["watchdog_violation"] == "host free space below 2048 MB"


def test_main_docker_watchdog_violation_sandbox_error_kind_status(tmp_path, monkeypatch, capsys):
    # D1: a watchdog_violation whose kind is "sandbox_error" (a
    # watchdog-thread sample() failure, spec §6's second-failure case) must
    # override a "completed" terminal status to "sandbox_error", not the
    # default "budget_exceeded" -- same only-replaces-'completed' rule as
    # the plain watchdog_violation case above.
    from dirtywork.procs import Captured
    from dirtywork.sandbox import RunArtifacts
    from dirtywork.sandbox.docker import DockerSandbox as RealDockerSandbox
    m, repo = _docker_mode_scaffold(tmp_path, monkeypatch)
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(1, b"", False, False))

    def finalize(self):
        return RunArtifacts(export_status="ok",
                             watchdog_violation="watchdog: worktree budget sample failed twice in a row",
                             watchdog_violation_kind="sandbox_error")

    FakeDockerSandbox = _fake_docker_sandbox_class(RealDockerSandbox, finalize=finalize)
    monkeypatch.setattr(m, "DockerSandbox", FakeDockerSandbox)

    class ImmediateDoneClient(DictProvider):


        def reply(self, model, messages, tools):
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    patch_provider(monkeypatch, m, ImmediateDoneClient)

    rc = m.main(["run", "--repo", str(repo), "some task"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "sandbox_error"
    assert payload["watchdog_violation_kind"] == "sandbox_error"
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["status"] == "sandbox_error"
    assert run_json["watchdog_violation_kind"] == "sandbox_error"


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

    class ImmediateDoneClient(DictProvider):


        def reply(self, model, messages, tools):
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    patch_provider(monkeypatch, m, ImmediateDoneClient)

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

    class BashCallingClient(DictProvider):
        def reply(self, model, messages, tools):
            return {"choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function",
                                 "function": {"name": "bash",
                                              "arguments": json.dumps({"command": "echo hi"})}}],
            }}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    patch_provider(monkeypatch, m, BashCallingClient)

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
    assert "finish(summary=...)" in p


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
    patch_provider(monkeypatch, m, PreflightProvider)
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
    patch_provider(monkeypatch, m, PreflightProvider)

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
    patch_provider(monkeypatch, m, PreflightProvider)

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
    patch_provider(monkeypatch, m, PreflightProvider)

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

    class ImmediateDoneClient(DictProvider):


        def reply(self, model, messages, tools):
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    patch_provider(monkeypatch, m, ImmediateDoneClient)

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

    class WritingFakeClient(DictProvider):
        def reply(self, model, messages, tools):
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

    patch_provider(monkeypatch, m, WritingFakeClient)

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

    class WritingFakeClient(DictProvider):
        def reply(self, model, messages, tools):
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

    patch_provider(monkeypatch, m, WritingFakeClient)

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
    patch_provider(monkeypatch, m, PreflightProvider)
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
    patch_provider(monkeypatch, m, PreflightProvider)
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
    patch_provider(monkeypatch, m, PreflightProvider)
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


def _host_repo(tmp_path):
    import subprocess
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "--allow-empty", "-m", "i"], capture_output=True)
    return repo


def _install_host_harness(monkeypatch, tmp_path, responses=None):
    import dirtywork.__main__ as m
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    patch_provider(monkeypatch, m,
                        lambda base_url=None: _ScriptedClient(base_url, responses))
    return m


class _ScriptedClient(DictProvider):
    """Provider stand-in driven by a list of OpenAI chat bodies; the last
    response repeats so a run can never underflow."""
    instances = []

    def __init__(self, base_url=None, responses=None):
        super().__init__(base_url)
        self.responses = list(responses or [text_body()])
        _ScriptedClient.instances.append(self)

    def list_models(self):
        import dirtywork.__main__ as m
        return [m.DEFAULT_MODEL, "other/model"]

    def reply(self, model, messages, tools):
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def _read_only_run_json(tmp_path):
    return json.loads(next((tmp_path / "runs").rglob("run.json")).read_text())


def test_run_json_records_task_model_context_window_and_turns(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "--context-window", "5000",
                 "--stall-turns", "7", "some task"])
    assert rc == 0
    data = _read_only_run_json(tmp_path)
    assert data["task"] == "some task"
    assert data["model"] == m.DEFAULT_MODEL
    assert data["context_window"] == 5000
    assert data["turns"] == 1
    assert data["resumed_from"] is None
    start = next(e for e in (json.loads(l) for l in Path(
        next((tmp_path / "runs").rglob("transcript.jsonl"))).read_text().splitlines())
        if e["event"] == "run_start")
    assert start["context_window"] == 5000 and start["resumed_from"] is None
    out = json.loads(capsys.readouterr().out)
    assert out["resumed_from"] is None


def test_context_window_env_and_unknown_model_warning(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    monkeypatch.setenv("DIRTYWORK_CONTEXT_WINDOW", "4096")
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "--model", "other/model", "t"])
    assert rc == 0
    assert _read_only_run_json(tmp_path)["context_window"] == 4096
    assert "warning: no known context window" not in capsys.readouterr().err
    monkeypatch.delenv("DIRTYWORK_CONTEXT_WINDOW")
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs2")
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "--model", "other/model", "t"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "warning: no known context window for 'other/model'; assuming 32768 tokens" in err


def test_bad_context_window_env_exits_2(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    monkeypatch.setenv("DIRTYWORK_CONTEXT_WINDOW", "lots")
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "t"])
    assert rc == 2
    assert "DIRTYWORK_CONTEXT_WINDOW" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


def test_bad_stall_turns_flag_exits_2(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    with pytest.raises(SystemExit) as ei:
        m.main(["run", "--repo", str(repo), "--sandbox", "none", "--stall-turns", "-1", "t"])
    assert ei.value.code == 2


def _first_run(monkeypatch, tmp_path, responses):
    m = _install_host_harness(monkeypatch, tmp_path, responses)
    repo = _host_repo(tmp_path)
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "--max-turns", "1", "add a file"])
    return m, repo, rc


def _resume_responses():
    return [{"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "b1", "type": "function", "function": {"name": "bash",
                 "arguments": json.dumps({"command": "git status --short; cat new.txt"})}}]}}],
             "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
            {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "f1", "type": "function", "function": {"name": "finish",
                 "arguments": json.dumps({"summary": "resumed and verified"})}}]}}],
             "usage": {"prompt_tokens": 1, "completion_tokens": 1}}]


def test_resume_host_mode_reuses_worktree_and_links_runs(tmp_path, monkeypatch, capsys):
    write_then_loop = [
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "w1", "type": "function", "function": {"name": "write_file",
             "arguments": json.dumps({"path": "new.txt", "content": "from run 1\n"})}}]}}],
         "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
    ]
    m, repo, rc = _first_run(monkeypatch, tmp_path, write_then_loop)
    assert rc == 1                                    # max_turns after 1 turn
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "max_turns"
    first_run_dir = Path(first["run_dir"])

    patch_provider(monkeypatch, m,
                        lambda base_url=None: _ScriptedClient(base_url, _resume_responses()))
    rc = m.main(["resume", first_run_dir.name])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0, out
    assert out["status"] == "completed"
    assert out["final_message"] == "resumed and verified"
    assert out["worktree"] == first["worktree"] and out["branch"] == first["branch"]
    assert out["resumed_from"] == first_run_dir.name
    assert out["run_dir"] != first["run_dir"]

    second = json.loads((Path(out["run_dir"]) / "run.json").read_text())
    assert second["resumed_from"] == first_run_dir.name
    assert second["task"].startswith("add a file\n\n--- RESUMED RUN ---")
    assert "ended with status 'max_turns' after 1 turns" in second["task"]
    assert second["model"] == m.DEFAULT_MODEL
    prior = json.loads((first_run_dir / "run.json").read_text())
    assert prior["resumed_by"] == second["slug"]
    assert prior["status"] == "max_turns"             # untouched

    events = [json.loads(l) for l in Path(out["transcript"]).read_text().splitlines()]
    start = next(e for e in events if e["event"] == "run_start")
    assert start["resumed_from"] == first_run_dir.name
    bash_result = next(e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash")
    assert "from run 1" in bash_result                 # prior work is in the worktree
    # the prior transcript tail reached the model
    assert "tool_result write_file" in start["task"]
    # only one worktree exists
    assert len(list((repo / ".worktrees").iterdir())) == 1


def test_resume_refuses_running_run_with_live_pid(tmp_path, monkeypatch, capsys):
    m, repo, rc = _first_run(monkeypatch, tmp_path, None)
    first = json.loads(capsys.readouterr().out)
    run_dir = Path(first["run_dir"])
    data = json.loads((run_dir / "run.json").read_text())
    data["status"] = "running"; data["host_pid"] = os.getpid()
    (run_dir / "run.json").write_text(json.dumps(data))
    rc = m.main(["resume", run_dir.name])
    assert rc == 2
    assert "still in progress" in capsys.readouterr().err
    assert len(list((tmp_path / "runs").iterdir())) == 1   # nothing created


def test_resume_refuses_missing_worktree(tmp_path, monkeypatch, capsys):
    m, repo, rc = _first_run(monkeypatch, tmp_path, None)
    first = json.loads(capsys.readouterr().out)
    shutil.rmtree(first["worktree"])
    rc = m.main(["resume", Path(first["run_dir"]).name])
    assert rc == 2
    assert "worktree" in capsys.readouterr().err


def test_resume_rejects_sandbox_flag_and_unknown_run(tmp_path, monkeypatch, capsys):
    m = _install_host_harness(monkeypatch, tmp_path)
    with pytest.raises(SystemExit):
        m.main(["resume", "--sandbox", "none", "abc"])
    rc = m.main(["resume", "no-such-run"])
    assert rc == 2
    assert "run.json" in capsys.readouterr().err


def test_resume_uses_prior_model_unless_overridden(tmp_path, monkeypatch, capsys):
    m, repo, rc = _first_run(monkeypatch, tmp_path, None)
    first = json.loads(capsys.readouterr().out)
    patch_provider(monkeypatch, m,
                        lambda base_url=None: _ScriptedClient(base_url, _resume_responses()))
    rc = m.main(["resume", "--model", "other/model", Path(first["run_dir"]).name])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert json.loads((Path(out["run_dir"]) / "run.json").read_text())["model"] == "other/model"


def test_resume_setup_failure_keeps_worktree(tmp_path, monkeypatch, capsys):
    m, repo, rc = _first_run(monkeypatch, tmp_path, None)
    first = json.loads(capsys.readouterr().out)
    from dirtywork.sandbox import SandboxError

    class ExplodingHost:
        def __init__(self, *a, **k):
            pass

        def start(self, *a, **k):
            raise SandboxError("boom at start")

    monkeypatch.setattr(m, "HostSandbox", ExplodingHost)
    rc = m.main(["resume", Path(first["run_dir"]).name])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["status"] == "sandbox_error"
    assert Path(first["worktree"]).is_dir()            # prior work preserved


def _install_docker_fake(monkeypatch, tmp_path, start_calls: list):
    """Docker-mode harness (same shape as test_main_docker_mode_happy_path_with_fake_sandbox):
    daemon/image/objects preflight faked, collision check sees no objects, and
    a fake DockerSandbox that records start() kwargs."""
    import dirtywork.__main__ as m
    from dirtywork.procs import Captured
    from dirtywork.sandbox import RunArtifacts
    from dirtywork.sandbox.docker import DockerSandbox as RealDockerSandbox
    monkeypatch.setattr(m, "docker_version", lambda *a, **k: "29.7.2")
    monkeypatch.setattr(m, "resolve_image", lambda *a, **k: "dirtywork/worker@sha256:" + "a" * 64)
    monkeypatch.setattr(m, "validate_objects_dir", lambda repo: repo / ".git" / "objects")
    monkeypatch.setattr(m.docker_cli, "run",
                        lambda argv, *, timeout, stdin=None: Captured(1, b"", False, False))

    class FakeWatchdog:
        def start(self):
            pass

    class FakeDockerSandbox:
        check_name_collision = staticmethod(RealDockerSandbox.check_name_collision)

        def __init__(self, cfg, *, run_dir, transcript=None, image_ref=None):
            self.cfg = cfg
            self.run_dir = run_dir
            self.uid = 501
            self.gid = 20
            self.image_ref = image_ref
            self.watchdog = FakeWatchdog()

        def start(self, worktree, repo, slug, base_commit, **kwargs):
            start_calls.append({"slug": slug, "base_commit": base_commit, **kwargs})

        def stop(self):
            pass

        def read_file(self, path, offset=0, limit=400):
            return ""

        def write_file(self, path, content):
            return "ok"

        def edit_file(self, path, old_string, new_string):
            return "ok"

        def list_dir(self, path="."):
            return ""

        def grep(self, pattern, path=".", glob=None, timeout=30):
            return ""

        def bash(self, command, timeout=120):
            return "exit code: 0\n"

        def finalize(self):
            return RunArtifacts(export_status="ok", diff_stat="")

    monkeypatch.setattr(m, "DockerSandbox", FakeDockerSandbox)
    return m


def test_resume_docker_mode_seeds_and_keeps_branch(tmp_path, monkeypatch, capsys):
    start_calls = []
    m = _install_host_harness(monkeypatch, tmp_path)      # RUNS_DIR + scripted LLM ("done")
    _install_docker_fake(monkeypatch, tmp_path, start_calls)
    repo = _host_repo(tmp_path)
    rc = m.main(["run", "--repo", str(repo), "some task"])   # docker is the default sandbox
    first = json.loads(capsys.readouterr().out)
    assert rc == 0, first
    assert start_calls[0]["seed_from_worktree"] is False
    assert start_calls[0]["branch"] == first["branch"]

    rc = m.main(["resume", Path(first["run_dir"]).name])
    second = json.loads(capsys.readouterr().out)
    assert rc == 0, second
    assert len(start_calls) == 2
    assert start_calls[1]["seed_from_worktree"] is True
    assert start_calls[1]["branch"] == first["branch"]
    assert start_calls[1]["slug"] != start_calls[0]["slug"]
    second_json = json.loads((Path(second["run_dir"]) / "run.json").read_text())
    first_json = json.loads((Path(first["run_dir"]) / "run.json").read_text())
    assert second_json["sandbox"] == "docker"
    assert second_json["image"] == first_json["image"]
    assert second_json["container"] != first_json["container"]


def test_main_unknown_provider_rejected_by_argparse(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        main(["run", "--repo", str(tmp_path), "--provider", "bogus", "do things"])
    assert exc_info.value.code == 2


def test_base_url_defaults_per_provider(tmp_path, monkeypatch, capsys):
    import dirtywork.__main__ as m
    from dirtywork.runner import RunResult
    repo = _host_repo(tmp_path)
    monkeypatch.setattr(m, "RUNS_DIR", tmp_path / "runs")
    seen = {}

    def factory(base_url=None):
        seen["base_url"] = base_url
        return PreflightProvider(base_url)

    patch_provider(monkeypatch, m, factory)
    monkeypatch.setattr(m.Runner, "run", lambda self, sp, task: RunResult("completed", 1, "ok", {}))
    rc = m.main(["run", "--repo", str(repo), "--sandbox", "none", "task"])
    assert rc == 0
    assert seen["base_url"] == "http://localhost:1234/v1"


def test_run_json_and_stdout_record_the_provider(tmp_path, monkeypatch, capsys):
    import dirtywork.__main__ as m
    m2 = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    rc = m2.main(["run", "--repo", str(repo), "--sandbox", "none", "task"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["provider"] == "openai"
    run_json = json.loads((Path(payload["run_dir"]) / "run.json").read_text())
    assert run_json["provider"] == "openai"
    transcript = (Path(payload["run_dir"]) / "transcript.jsonl").read_text().splitlines()
    run_start = next(json.loads(l) for l in transcript if json.loads(l)["event"] == "run_start")
    assert run_start["provider"] == "openai"
    assert run_start["base_url"] == "http://localhost:1234/v1"


def test_resume_refuses_a_provider_switch(tmp_path, monkeypatch, capsys):
    import dirtywork.__main__ as m
    m2 = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    assert m2.main(["run", "--repo", str(repo), "--sandbox", "none", "task"]) == 0
    slug = json.loads(capsys.readouterr().out)["run_dir"].rsplit("/", 1)[-1]
    rc = m2.main(["resume", str(tmp_path / "runs" / slug), "--provider", "anthropic"])
    assert rc == 2
    assert "provider 'openai'" in capsys.readouterr().err


def test_resume_inherits_the_prior_provider(tmp_path, monkeypatch, capsys):
    import dirtywork.__main__ as m
    m2 = _install_host_harness(monkeypatch, tmp_path)
    repo = _host_repo(tmp_path)
    assert m2.main(["run", "--repo", str(repo), "--sandbox", "none", "task"]) == 0
    slug = json.loads(capsys.readouterr().out)["run_dir"].rsplit("/", 1)[-1]
    assert m2.main(["resume", str(tmp_path / "runs" / slug)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["provider"] == "openai"
