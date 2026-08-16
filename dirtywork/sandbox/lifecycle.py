# dirtywork/sandbox/lifecycle.py
from __future__ import annotations

import subprocess
import time

from . import SandboxError
from . import docker_args
from . import docker_cli


def wait_ready(run, name: str, *, deadline_s: float = docker_cli.T_LIFECYCLE, poll_s: float = 0.05) -> None:
    """Poll `docker exec <name> /bin/true` until it exits 0 or deadline_s elapses; raise SandboxError with the last error otherwise."""
    deadline = time.monotonic() + deadline_s
    last_error = None
    while time.monotonic() < deadline:
        try:
            captured = run(["exec", name, "/bin/true"], timeout=deadline_s)
        except docker_cli.DockerError as e:
            last_error = e
            time.sleep(poll_s)
            continue
        if captured.returncode == 0:
            return
        last_error = SandboxError(
            f"docker exec {name} /bin/true returned {captured.returncode}"
        )
        time.sleep(poll_s)
    raise SandboxError(
        f"container {name} did not become ready within {deadline_s}s" + (f": {last_error}" if last_error else "")
    )


def init_worker_git(run, name: str, *, slug: str, base_commit: str, restart: bool) -> None:
    """Run the in-container git init script (git init -q; alternates; symbolic-ref; update-ref; read-tree HEAD [-m -u unless restart]) with GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1; raise SandboxError('in-container init failed: <output[:500]>') on non-zero exit."""
    populate = "/usr/bin/git read-tree HEAD" if restart else "/usr/bin/git read-tree -m -u HEAD"
    script = (
        "set -e; "
        "/usr/bin/git init -q; "
        "echo /repo.git/objects > /gitdir/objects/info/alternates; "
        f"/usr/bin/git symbolic-ref HEAD refs/heads/dirtywork/{slug}; "
        f"/usr/bin/git update-ref refs/heads/dirtywork/{slug} {base_commit}; "
        f"{populate}"
    )
    argv = docker_args.exec_argv(
        name, ["/bin/sh", "-c", script],
        env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
    )
    captured = run(argv, timeout=docker_cli.T_LIFECYCLE)
    if captured.returncode != 0:
        raise SandboxError(
            f"in-container init failed: {captured.output.decode('utf-8', 'replace')[:500]}"
        )


def close_tether(proc, *, timeout_s: float = docker_cli.T_LIFECYCLE) -> None:
    """Close the tether's stdin (ignore OSError), wait up to timeout_s; on subprocess.TimeoutExpired kill() then wait(). Never swallows other exceptions."""
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
