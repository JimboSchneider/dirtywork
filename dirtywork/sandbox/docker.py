# dirtywork/sandbox/docker.py
from __future__ import annotations

import os
import posixpath
import subprocess
import time
from pathlib import Path

from ..guardrails import check_bash_command
from ..procs import Captured, run_capped
from ..tools import MAX_BASH_CHARS, MAX_LIST_ENTRIES, MAX_READ_BYTES, MAX_WRITE_BYTES, _cap, _number_lines
from . import SandboxError
from . import docker_args
from . import docker_cli
from ..workspace import WorkspaceError

# Fixed exec timeouts for tools with no user-facing timeout knob — these
# operations should complete near-instantly; a hang means the sandbox
# itself is broken, so DockerError is allowed to propagate as sandbox_error
# rather than being caught and turned into text (unlike bash/grep, whose
# Sandbox signatures accept a caller timeout and whose contract already
# promises a graceful "timed out" text result).
READ_EXEC_TIMEOUT = 30
WRITE_EXEC_TIMEOUT = 30
LIST_EXEC_TIMEOUT = 30


def _rel(path: str, *, writing: bool = False):
    """Host-side path normalization — an accident guard, not the security
    boundary (the container's read-only rootfs and its own filesystem are
    the boundary). Returns (normalized, None) or (None, error_string).
    Rejects absolute paths, '..' escapes, and — when writing — a first path
    component of '.git' (mirrors resolve_in_worktree's writing=True guard in
    host mode)."""
    normalized = posixpath.normpath(path)
    if posixpath.isabs(normalized):
        return None, (
            f"ERROR: path '{path}' resolves outside the worktree "
            f"(absolute paths are not allowed)"
        )
    parts = [] if normalized == "." else normalized.split("/")
    if any(part == ".." for part in parts):
        return None, (
            f"ERROR: path '{path}' resolves outside the worktree "
            f"('..' escapes are not allowed)"
        )
    if writing and parts and parts[0] == ".git":
        return None, f"ERROR: writing inside .git/ is not allowed (got '{path}')"
    return normalized, None


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
        self._check(create_vol, f"docker volume create {vol} failed")
        self.volume = vol

        prep_argv = docker_args.prep_run_argv(self.cfg, slug, self.image_ref, self.uid, self.gid)
        prep = self._run(prep_argv, timeout=docker_cli.T_LIFECYCLE)
        self._check(prep, "prep container failed to chown the volume")

        create_argv = docker_args.worker_create_argv(
            self.cfg, slug, self.image_ref, self.uid, self.gid, objects_dir, repo_label=label
        )
        created = self._run(create_argv, timeout=docker_cli.T_LIFECYCLE)
        self._check(created, f"docker create {name} failed")
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
        self._check(captured, "in-container init failed")

    def _check(self, res, what: str) -> None:
        """Raise SandboxError with the captured output when a docker step failed."""
        if res.returncode != 0:
            raise SandboxError(f"{what}: {res.output.decode('utf-8', 'replace')[:500]}")

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
            except subprocess.TimeoutExpired:
                self._tether.kill()
                self._tether.wait()
        if self.volume is not None and not self.cfg.keep_volume:
            try:
                self._run(["volume", "rm", self.volume], timeout=docker_cli.T_QUERY)
            except docker_cli.DockerError:
                pass

    def _read_raw(self, path: str, *, strict: bool = False):
        rel, err = _rel(path)
        if err:
            return None, err
        argv = docker_args.exec_argv(
            self.container, ["/usr/bin/head", "-c", str(MAX_READ_BYTES + 1), "--", rel]
        )
        captured = self._run(argv, timeout=READ_EXEC_TIMEOUT)
        if captured.returncode != 0:
            return None, (
                f"ERROR: cannot read '{path}': "
                f"{captured.output.decode('utf-8', 'replace')[:500]}"
            )
        if len(captured.output) > MAX_READ_BYTES:
            return None, (
                f"ERROR: '{path}' exceeds {MAX_READ_BYTES} bytes; refusing to read"
            )
        if strict:
            try:
                text = captured.output.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                return None, (
                    f"ERROR: '{path}' is not valid UTF-8; refusing to edit"
                )
            return text, None
        return captured.output.decode("utf-8", errors="replace"), None

    def read_file(self, path: str, offset: int = 0, limit: int = 400) -> str:
        text, err = self._read_raw(path)
        if err:
            return err
        return _number_lines(text, offset, limit)

    def write_file(self, path: str, content: str) -> str:
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            return (
                f"ERROR: content is {len(encoded)} bytes, over the "
                f"{MAX_WRITE_BYTES}-byte write limit"
            )
        rel, err = _rel(path, writing=True)
        if err:
            return err
        argv = docker_args.exec_argv(
            self.container,
            ["/bin/sh", "-c", 'mkdir -p "$(dirname -- "$1")" && cat > "$1"', "_", rel],
            stdin=True,
        )
        captured = self._run(argv, timeout=WRITE_EXEC_TIMEOUT, stdin=encoded)
        if captured.returncode != 0:
            return (
                f"ERROR: cannot write '{path}': "
                f"{captured.output.decode('utf-8', 'replace')[:500]}"
            )
        return f"Wrote {len(encoded)} bytes to {path}"

    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        text, err = self._read_raw(path, strict=True)
        if err:
            return err
        count = text.count(old_string)
        if count != 1:
            return (
                f"ERROR: old_string occurs {count} times in {path}; it must occur "
                f"exactly once. Include more surrounding context to make it unique."
            )
        result = self.write_file(path, text.replace(old_string, new_string, 1))
        if result.startswith("ERROR:"):
            return result
        return f"Edited {path}"
