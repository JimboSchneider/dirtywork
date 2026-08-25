# dirtywork/sandbox/lifecycle.py
from __future__ import annotations

import subprocess
import time

from . import SandboxError
from . import docker_args
from . import docker_cli


# Template for the init script used by the export container (env layout)
ENV_INIT_SCRIPT = (
    "set -e; "
    "/usr/bin/git init -q --template= ; "
    "echo /repo.git/objects > /gitdir/objects/info/alternates; "
    "/usr/bin/git symbolic-ref HEAD refs/heads/{branch}; "
    "/usr/bin/git update-ref refs/heads/{branch} {base_commit}; "
    "{populate}"
)


# Template for the init script used by the worker container (gitfile layout)
GITFILE_INIT_SCRIPT = (
    "set -e; "
    "rm -rf -- /work/.git; "
    "/usr/bin/git init -q --template= --separate-git-dir=/gitdir; "
    "echo /repo.git/objects > /gitdir/objects/info/alternates; "
    "/usr/bin/git symbolic-ref HEAD refs/heads/{branch}; "
    "/usr/bin/git update-ref refs/heads/{branch} {base_commit}; "
    "{populate}"
)


def wait_ready(run, name: str, *, deadline_s: float | None = None, poll_s: float = 0.05) -> None:
    """Poll `docker exec <name> /bin/true` until it exits 0 or deadline_s elapses; raise SandboxError with the last error otherwise."""
    if deadline_s is None:
        deadline_s = docker_cli.T_LIFECYCLE
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


def init_worker_git(run, name: str, *, branch: str, base_commit: str, restart: bool, layout: str) -> None:
    """Run the in-container git init script with GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1.

    Two layouts are supported:
    - "env": for the export container (uses ENV_INIT_SCRIPT); that container has GIT_DIR=/gitdir
      and GIT_WORK_TREE=/work set at create time because /work is mounted read-only there.
    - "gitfile": for worker container (uses GITFILE_INIT_SCRIPT). The git repository is stored at
      /gitdir and /work/.git is a gitfile pointing to it (no GIT_DIR in environment).

    `branch` is the full branch name (dirtywork/<slug>) — a resumed run keeps the original
    run's branch while its container/volume carry the new slug.

    Raises SandboxError('in-container init failed: <output[:500]>') on non-zero exit.
    """
    if layout not in ("env", "gitfile"):
        raise ValueError(f"layout must be 'env' or 'gitfile', got {layout!r}")

    populate = "/usr/bin/git read-tree HEAD" if restart else "/usr/bin/git read-tree -m -u HEAD"

    script = {
        "env": ENV_INIT_SCRIPT,
        "gitfile": GITFILE_INIT_SCRIPT
    }[layout].format(branch=branch, base_commit=base_commit, populate=populate)

    # For gitfile layout, the container has no GIT_DIR/GIT_WORK_TREE in its env
    # (git init --separate-git-dir must not see them). For env layout, the container
    # has both set at create time (export container is read-only so it needs them).
    exec_env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}

    argv = docker_args.exec_argv(
        name, ["/bin/sh", "-c", script],
        env=exec_env,
    )
    captured = run(argv, timeout=docker_cli.T_LIFECYCLE)
    if captured.returncode != 0:
        raise SandboxError(
            f"in-container init failed: {captured.output.decode('utf-8', 'replace')[:500]}"
        )


def close_tether(proc, *, timeout_s: float | None = None) -> None:
    """Close the tether's stdin (ignore OSError), wait up to timeout_s; on subprocess.TimeoutExpired kill() then wait(). Never swallows other exceptions."""
    if timeout_s is None:
        timeout_s = docker_cli.T_LIFECYCLE
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
