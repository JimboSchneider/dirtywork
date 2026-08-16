# dirtywork/sandbox/docker.py
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from . import SandboxError
from . import docker_args
from . import docker_cli
from ..workspace import WorkspaceError


class DockerSandbox:
    """Every tool call and the run lifecycle for docker mode. Constructed
    with injectable `run`/`popen` so unit tests never touch a real daemon —
    only tests marked `docker` (Tasks 13, 15, 16) pass real callables."""

    def __init__(self, cfg: docker_args.DockerConfig, *, run_dir: Path, transcript=None,
                 run=docker_cli.run, popen=subprocess.Popen):
        self.cfg = cfg
        self.run_dir = run_dir
        self.transcript = transcript
        self._run = run
        self._popen = popen
        self.container = None
        self.volume = None
        self.image_ref = None
        self.uid = None
        self.gid = None
        self._tether = None
        self._slug = None
        self._repo = None
        self._worktree = None
        self._base_commit = None
        self._stopped = False

    def start(self, worktree: Path, repo: Path, slug: str, base_commit: str) -> None:
        self._worktree = worktree
        self._repo = repo
        self._slug = slug
        self._base_commit = base_commit
        name = docker_args.container_name(slug)
        vol = docker_args.volume_name(slug)

        # Name collision refusal (spec §3): never remove anything this
        # invocation did not create — a collision is either a stale leftover
        # or something else's resource, and both deserve a human.
        c_inspect = self._run(["container", "inspect", name], timeout=docker_cli.T_QUERY)
        if c_inspect.returncode == 0:
            raise SandboxError(
                f"container {name} already exists; run `dirtywork runs clean {slug}`"
            )
        v_inspect = self._run(["volume", "inspect", vol], timeout=docker_cli.T_QUERY)
        if v_inspect.returncode == 0:
            raise SandboxError(
                f"volume {vol} already exists; run `dirtywork runs clean {slug}`"
            )

        self.uid = os.getuid() if os.name == "posix" else 1000
        self.gid = os.getgid() if os.name == "posix" else 1000

        try:
            objects_dir = docker_cli.validate_objects_dir(repo)
        except WorkspaceError as e:
            raise SandboxError(str(e)) from e
        self.image_ref = docker_cli.resolve_image(
            self.cfg.image, run=self._run, pinned_digest=docker_args.PINNED_DIGEST)
        label = docker_args.repo_label(repo)

        create_vol = self._run(
            ["volume", "create", "--label", f"dirtywork.run={slug}",
             "--label", f"dirtywork.repo={label}", vol],
            timeout=docker_cli.T_QUERY,
        )
        if create_vol.returncode != 0:
            raise SandboxError(
                f"docker volume create {vol} failed: "
                f"{create_vol.output.decode('utf-8', 'replace')[:500]}"
            )
        self.volume = vol

        prep_argv = docker_args.prep_run_argv(self.cfg, slug, self.image_ref, self.uid, self.gid)
        prep = self._run(prep_argv, timeout=docker_cli.T_LIFECYCLE)
        if prep.returncode != 0:
            raise SandboxError(
                f"prep container failed to chown the volume: "
                f"{prep.output.decode('utf-8', 'replace')[:500]}"
            )

        create_argv = docker_args.worker_create_argv(
            self.cfg, slug, self.image_ref, self.uid, self.gid, objects_dir, repo_label=label
        )
        created = self._run(create_argv, timeout=docker_cli.T_LIFECYCLE)
        if created.returncode != 0:
            raise SandboxError(
                f"docker create {name} failed: {created.output.decode('utf-8', 'replace')[:500]}"
            )
        self.container = name

        self._start_tether()
        self._wait_ready()
        self._init(restart=False)

    def _start_tether(self) -> None:
        self._tether = self._popen(
            ["docker", "start", "-ai", self.container],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + docker_cli.T_LIFECYCLE
        last_error = None
        while time.monotonic() < deadline:
            try:
                captured = self._run(["exec", self.container, "/bin/true"],
                                      timeout=docker_cli.T_LIFECYCLE)
            except docker_cli.DockerError as e:
                last_error = e
                time.sleep(0.05)
                continue
            if captured.returncode == 0:
                return
            last_error = SandboxError(
                f"docker exec {self.container} /bin/true returned {captured.returncode}"
            )
            time.sleep(0.05)
        raise SandboxError(
            f"container {self.container} did not become ready within "
            f"{docker_cli.T_LIFECYCLE}s" + (f": {last_error}" if last_error else "")
        )

    def _init(self, *, restart: bool) -> None:
        populate = "/usr/bin/git read-tree HEAD" if restart else "/usr/bin/git read-tree -m -u HEAD"
        script = (
            "set -e; "
            "/usr/bin/git init -q; "
            "echo /repo.git/objects > /gitdir/objects/info/alternates; "
            f"/usr/bin/git symbolic-ref HEAD refs/heads/dirtywork/{self._slug}; "
            f"/usr/bin/git update-ref refs/heads/dirtywork/{self._slug} {self._base_commit}; "
            f"{populate}"
        )
        argv = docker_args.exec_argv(
            self.container, ["/bin/sh", "-c", script],
            env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
        )
        captured = self._run(argv, timeout=docker_cli.T_LIFECYCLE)
        if captured.returncode != 0:
            raise SandboxError(
                f"in-container init failed: {captured.output.decode('utf-8', 'replace')[:500]}"
            )

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self.container is not None:
            try:
                self._run(["rm", "-f", self.container], timeout=docker_cli.T_LIFECYCLE)
            except docker_cli.DockerError:
                pass
        if self._tether is not None:
            try:
                if self._tether.stdin is not None:
                    self._tether.stdin.close()
            except OSError:
                pass
            try:
                self._tether.wait(timeout=docker_cli.T_LIFECYCLE)
            except Exception:
                pass
        if self.volume is not None and not self.cfg.keep_volume:
            try:
                self._run(["volume", "rm", self.volume], timeout=docker_cli.T_QUERY)
            except docker_cli.DockerError:
                pass
