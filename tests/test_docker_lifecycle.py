# tests/test_docker_lifecycle.py
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from dirtywork.__main__ import DEFAULT_MODEL


def _resp(content=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def _call(call_id, name, args: dict):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


class _ScriptedHandler(http.server.BaseHTTPRequestHandler):
    """Serves /v1/models and /v1/chat/completions with pre-scripted JSON
    responses, popped in order for each /chat/completions POST."""

    def log_message(self, *a):
        pass  # keep test output quiet

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            body = json.dumps({"data": [{"id": self.server.model}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)  # request body is ignored — responses are pre-scripted
        if self.server.responses:
            resp = self.server.responses.pop(0)
        else:
            resp = {"choices": [{"message": {"role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        body = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_fake_llm_server(model: str, responses: list):
    server = http.server.HTTPServer(("127.0.0.1", 0), _ScriptedHandler)
    server.model = model
    server.responses = list(responses)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README.md").write_text("# demo\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    return repo


def _spawn_env(tmp_home: Path) -> dict:
    """A subprocess-local $HOME so ~/.dirtywork/runs (and thus the slug the
    subprocess picks) is isolated per test — no CLI flag exists for the
    runs directory, but dirtywork.rundir.RUNS_DIR is Path.home()-derived,
    which $HOME fully controls."""
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "TERM", "LANG")}
    env["HOME"] = str(tmp_home)
    return env


def _dirtywork_argv(repo: Path, base_url: str, task: str = "do the task", extra=None) -> list:
    argv = [sys.executable, "-m", "dirtywork", "run", "--repo", str(repo),
            "--sandbox", "docker", "--base-url", base_url, "--max-turns", "5"]
    if extra:
        argv += extra
    argv.append(task)
    return argv


def _wait_for_slug(tmp_home: Path, timeout: float) -> str:
    runs_root = tmp_home / ".dirtywork" / "runs"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if runs_root.is_dir():
            entries = [p for p in runs_root.iterdir() if p.is_dir()]
            if entries:
                return entries[0].name
        time.sleep(0.2)
    raise TimeoutError("dirtywork subprocess never created a run directory")


def _docker_ps_a(label_filter: str) -> str:
    return subprocess.run(
        ["docker", "ps", "-a", "--filter", label_filter, "--format", "{{.Names}}"],
        capture_output=True, text=True,
    ).stdout


def _docker_volume_ls(label_filter: str) -> str:
    return subprocess.run(
        ["docker", "volume", "ls", "--filter", label_filter, "--format", "{{.Name}}"],
        capture_output=True, text=True,
    ).stdout


def _wait_for_container(slug: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if f"dw-{slug}" in _docker_ps_a(f"label=dirtywork.run={slug}"):
            return
        time.sleep(0.2)
    raise TimeoutError(f"container dw-{slug} never appeared")


def _no_leaked_docker_children(slug: str) -> bool:
    result = subprocess.run(["pgrep", "-f", f"docker start -ai dw-{slug}"], capture_output=True)
    return result.returncode != 0  # pgrep: 0 = found a match, 1 = none found


def _cleanup_labelled(slug: str) -> None:
    subprocess.run(["docker", "rm", "-f", f"dw-{slug}"], capture_output=True)
    subprocess.run(["docker", "rm", "-f", f"dw-{slug}-export"], capture_output=True)
    subprocess.run(["docker", "volume", "rm", f"dw-{slug}-work"], capture_output=True)


@pytest.mark.docker
def test_docker_lifecycle_sigkill_leaves_container_gone_volume_recoverable(tmp_path):
    repo = _make_repo(tmp_path)
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    responses = [_resp(tool_calls=[_call("c1", "bash", {"command": "sleep 30", "timeout": 60})])]
    server, thread = _start_fake_llm_server(DEFAULT_MODEL, responses)
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    slug = None
    try:
        proc = subprocess.Popen(_dirtywork_argv(repo, base_url), env=_spawn_env(tmp_home),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            slug = _wait_for_slug(tmp_home, timeout=60)
            _wait_for_container(slug, timeout=60)

            proc.kill()  # SIGKILL the whole dirtywork process
            proc.wait(timeout=15)
            time.sleep(2)  # let the daemon notice the tether's stdin pipe closed

            # The tether Popen died with its parent, so `docker start -ai`'s
            # attach exits and the container stops (verified in the spec's
            # decision record) — it is gone or Exited, never left running.
            status = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Running}}", f"dw-{slug}"],
                capture_output=True, text=True,
            )
            assert status.returncode != 0 or status.stdout.strip() == "false"

            # The volume, with no attached process of its own, survives.
            assert f"dw-{slug}-work" in _docker_volume_ls(f"label=dirtywork.run={slug}")
            assert _no_leaked_docker_children(slug)

            # Recovery: export_run against the surviving volume — this plan
            # predates SP3's `dirtywork runs export <slug>` CLI command, which
            # will wrap exactly this call once it exists.
            from dirtywork.sandbox import docker_args
            from dirtywork.sandbox.docker_cli import resolve_image, validate_objects_dir
            from dirtywork.sandbox.export import export_run
            from dirtywork.workspace import worktree_base_commit

            worktree = repo / ".worktrees" / f"dw-{slug}"
            objects_dir = validate_objects_dir(repo)
            image_ref = resolve_image(docker_args.DEFAULT_IMAGE)
            cfg = docker_args.DockerConfig()
            label = docker_args.repo_label(repo)
            base_commit = worktree_base_commit(worktree)
            run_dir = tmp_home / ".dirtywork" / "runs" / slug
            artifacts = export_run(
                cfg, slug=slug, base_commit=base_commit, worktree=worktree, run_dir=run_dir,
                objects_dir=objects_dir, image_ref=image_ref, uid=os.getuid(), gid=os.getgid(),
                repo_label=label,
            )
            assert artifacts.export_status == "ok"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=15)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        if slug:
            _cleanup_labelled(slug)
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"], capture_output=True)


@pytest.mark.docker
def test_docker_lifecycle_daemon_hang_fails_closed_within_timeout(tmp_path):
    repo = _make_repo(tmp_path)
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stub = fake_bin / "docker"
    stub.write_text("#!/bin/sh\nsleep 1000\n")
    stub.chmod(0o755)

    server, thread = _start_fake_llm_server(DEFAULT_MODEL, [_resp(content="unreachable")])
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        env = _spawn_env(tmp_home)
        env["PATH"] = f"{fake_bin}:{env['PATH']}"  # the hanging stub shadows the real docker

        start = time.monotonic()
        result = subprocess.run(_dirtywork_argv(repo, base_url), env=env,
                                 capture_output=True, text=True, timeout=60)
        elapsed = time.monotonic() - start

        # docker_version()'s own T_QUERY=10s timeout must fire well before
        # this test's own 60s ceiling — "fails closed instead of hanging".
        assert elapsed < 30
        assert result.returncode == 2
        assert "Docker" in result.stderr
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.docker
def test_docker_lifecycle_container_killed_during_exec_run_continues_after_reset(tmp_path):
    repo = _make_repo(tmp_path)
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    responses = [
        _resp(tool_calls=[_call("c1", "bash", {"command": "sleep 20", "timeout": 60})]),
        _resp(tool_calls=[_call("c2", "bash", {"command": "echo recovered"})]),
        _resp(content="done"),
    ]
    server, thread = _start_fake_llm_server(DEFAULT_MODEL, responses)
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    slug = None
    try:
        proc = subprocess.Popen(_dirtywork_argv(repo, base_url), env=_spawn_env(tmp_home),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        slug = _wait_for_slug(tmp_home, timeout=60)
        _wait_for_container(slug, timeout=60)
        time.sleep(2)  # let the "sleep 20" bash exec actually start inside the container
        subprocess.run(["docker", "kill", f"dw-{slug}"], capture_output=True)

        out, _err = proc.communicate(timeout=90)
        payload = json.loads(out.decode())

        assert payload["status"] == "completed"
        events = [json.loads(l) for l in Path(payload["transcript"]).read_text().splitlines()]
        reset_events = [e for e in events if e["event"] == "sandbox_reset"]
        assert reset_events  # reap detected the killed container and reset it
        bash_results = [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
        assert "recovered" in bash_results[-1]

        # Debug: print volume status
        volumes = _docker_volume_ls(f"label=dirtywork.run={slug}")
        print(f"Volumes for {slug}: '{volumes}'")
        
        # The volume is deleted after successful export (unless keep_volume=True).
        # This test focuses on the container kill and reset, not volume retention.
        assert _no_leaked_docker_children(slug)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        if slug:
            _cleanup_labelled(slug)
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"], capture_output=True)
