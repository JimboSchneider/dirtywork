# dirtywork/sandbox/docker.py
from __future__ import annotations

import os
import posixpath
import subprocess
import time
from pathlib import Path

from ..budget import BudgetExceeded
from ..guardrails import check_bash_command
from ..procs import Captured, run_capped
from ..tools import MAX_BASH_CHARS, MAX_LIST_ENTRIES, MAX_READ_BYTES, MAX_WRITE_BYTES, _cap, _number_lines
from . import SandboxError
from . import docker_args
from . import docker_cli
from . import lifecycle
from ..workspace import WorkspaceError
from .watchdog import Watchdog

from . import export
from ..workspace import host_read_tree

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
        self.watchdog = None
        self._objects_dir = None
        self._export_failed = False

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
            self._objects_dir = objects_dir
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
        self.watchdog = Watchdog(
            kill=self._watchdog_kill,
            sample=self._sample_worktree,
            storage_paths=docker_cli.docker_storage_paths(run=self._run),
            min_free_mb=self.cfg.min_free_mb,
            max_worktree_mb=self.cfg.max_worktree_mb,
            max_worktree_files=self.cfg.max_worktree_files,
        )
        # Constructed, not started: the background thread does real
        # time.sleep/shutil.disk_usage work with no injectable clock in
        # production use. dirtywork/__main__.py starts it explicitly right
        # after a real sandbox.start() succeeds (Task 12).

    def _start_tether(self) -> None:
        self._tether = self._popen(
            ["docker", "start", "-ai", self.container],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _wait_ready(self) -> None:
        lifecycle.wait_ready(self._run, self.container)

    def _init(self, *, restart: bool) -> None:
        lifecycle.init_worker_git(self._run, self.container, slug=self._slug, base_commit=self._base_commit, restart=restart)

    def _check(self, res, what: str) -> None:
        """Raise SandboxError with the captured output when a docker step failed."""
        if res.returncode != 0:
            raise SandboxError(f"{what}: {res.output.decode('utf-8', 'replace')[:500]}")

    def _stop_container(self) -> None:
        if self.watchdog is not None:
            self.watchdog.stop()
            if self.watchdog.is_alive():
                self.watchdog.join(timeout=docker_cli.T_LIFECYCLE)
        if self.container is not None:
            try:
                self._run(["rm", "-f", self.container], timeout=docker_cli.T_LIFECYCLE)
            except docker_cli.DockerError:
                pass
        if self._tether is not None:
            lifecycle.close_tether(self._tether)

    def _stop_volume(self) -> None:
        if self.volume is not None and not self.cfg.keep_volume and not self._export_failed:
            try:
                self._run(["volume", "rm", self.volume], timeout=docker_cli.T_QUERY)
            except docker_cli.DockerError:
                pass

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._stop_container()
        self._stop_volume()

    def finalize(self) -> export.RunArtifacts:
        """Spec §2 steps 10-11: stop the worker container (but NOT the
        volume — export needs it), export into the still-empty host
        worktree, then host_read_tree — the one host git command allowed to
        touch anything the worker produced. `RunArtifacts.untracked` stays ""
        in docker mode: the export's `git add -A` before `write-tree` already
        folds new files into `diff_stat`/`diff.patch`, so there is nothing
        separate to report."""
        self._stop_container()
        label = docker_args.repo_label(self._repo)
        artifacts = export.export_run(
            self.cfg, slug=self._slug, base_commit=self._base_commit,
            worktree=self._worktree, run_dir=self.run_dir, objects_dir=self._objects_dir,
            image_ref=self.image_ref, uid=self.uid, gid=self.gid, repo_label=label,
            run=self._run, popen=self._popen,
        )
        if artifacts.export_status.startswith("export_failed"):
            # Leave the host worktree as it was (.git file only): read-tree
            # after a failed export would make host `git status` claim mass
            # deletions that never happened.
            self._export_failed = True
            return artifacts
        host_read_tree(self._worktree)
        return artifacts

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

    def _probe(self, attr: str, argv: list) -> bool:
        """Probe once per sandbox instance for an optional in-image tool; cached on self."""
        cached = getattr(self, attr, None)
        if cached is None:
            try:
                captured = self._run(docker_args.exec_argv(self.container, argv), timeout=LIST_EXEC_TIMEOUT)
                cached = captured.returncode == 0
            except docker_cli.DockerError:
                cached = False
            setattr(self, attr, cached)
        return cached

    def _list_exec(self, path: str, argv: list):
        """Run a listing command; return (stdout_text, None) or (None, error_string)."""
        captured = self._run(docker_args.exec_argv(self.container, argv), timeout=LIST_EXEC_TIMEOUT)
        if captured.returncode != 0:
            return None, f"ERROR: cannot list '{path}': {captured.output.decode('utf-8', 'replace')[:500]}"
        return captured.output.decode("utf-8", errors="replace"), None

    def list_dir(self, path: str = ".") -> str:
        rel, err = _rel(path)
        if err:
            return err
        rows = []  # (name, is_dir, size)
        if self._probe("_has_gnu_find", ["/usr/bin/find", "--version"]):
            out, err = self._list_exec(path, ["/usr/bin/find", rel, "-mindepth", "1", "-maxdepth", "1",
                                              "-printf", "%y\t%s\t%f\n"])
            if err:
                return err
            for line in out.splitlines():
                if line:
                    kind, size, name = line.split("\t", 2)
                    rows.append((name, kind == "d", int(size)))
        else:
            # Spec fallback for images without GNU find: `ls -1Ap` inside the target
            # directory (trailing `/` marks directories), then ONE batched `wc -c`
            # for the file sizes — never one exec per entry.
            out, err = self._list_exec(path, ["/bin/sh", "-c", 'cd -- "$1" && ls -1Ap', "sh", rel])
            if err:
                return err
            names = [line for line in out.splitlines() if line]
            files = [n for n in names if not n.endswith("/")]
            sizes = {}
            if files:
                wc_out, wc_err = self._list_exec(path, ["/bin/sh", "-c", 'cd -- "$1" && shift && wc -c -- "$@"', "sh", rel, *files])
                if wc_err is None:
                    for line in wc_out.splitlines():
                        parts = line.strip().split(None, 1)
                        if len(parts) == 2 and parts[1] != "total":
                            try:
                                sizes[parts[1]] = int(parts[0])
                            except ValueError:
                                pass
            for n in names:
                if n.endswith("/"):
                    rows.append((n[:-1], True, 0))
                else:
                    rows.append((n, False, sizes.get(n, 0)))
        rows.sort(key=lambda r: r[0])  # raw-name sort BEFORE formatting (host parity)
        formatted = [f"{name}/" if is_dir else f"{name}  ({size} bytes)" for name, is_dir, size in rows]
        note = ""
        if len(formatted) > MAX_LIST_ENTRIES:
            formatted = formatted[:MAX_LIST_ENTRIES]
            note = f"\n[list capped at {MAX_LIST_ENTRIES} entries]"
        return ("\n".join(formatted) or "(empty directory)") + note

    def grep(self, pattern: str, path: str = ".", glob: str | None = None,
             timeout: int = 30) -> str:
        rel, err = _rel(path)
        if err:
            return err
        # Probe for rg once per sandbox instance
        if self._probe("_has_rg", ["/usr/bin/rg", "--version"]):
            cmd = ["/usr/bin/rg", "-n", "--no-heading", "-M", "300", "-e", pattern]
            if glob:
                cmd += ["-g", glob]
        else:
            cmd = ["/usr/bin/grep", "-rn", "-e", pattern]
            if glob:
                cmd += [f"--include={glob}"]
        cmd.append(rel)
        argv = docker_args.exec_argv(self.container, cmd)
        try:
            captured = self._run(argv, timeout=timeout + 10)
        except docker_cli.DockerError:
            return f"ERROR: grep timed out after {timeout}s — narrow the pattern or path."
        if captured.returncode not in (0, 1):
            return f"ERROR: grep failed: {captured.output.decode('utf-8', 'replace')[:500]}"
        text = captured.output.decode("utf-8", errors="replace")
        if not text.strip():
            return "No matches found."
        # Strip leading ./ from paths
        lines = [(l[2:] if l.startswith("./") else l) for l in text.splitlines()]
        return _cap("\n".join(lines), note=" — narrow the pattern or path for full results")

    def reset(self, reason: str) -> None:
        """Spec §3 "Reset" (used on a stray process, OOM, or a watchdog
        kill): docker kill SIGKILLs PID 1, so the whole container namespace
        dies and its tmpfs is wiped — but the volume and its contents
        persist (verified). Fresh tether, ready-wait, then init's restart
        variant (index only — never touches /work, so the working tree
        survives a reset even though the worker's git metadata in /gitdir
        does not)."""
        try:
            self._run(["kill", self.container], timeout=docker_cli.T_LIFECYCLE)
        except docker_cli.DockerError:
            pass
        if self._tether is not None:
            lifecycle.close_tether(self._tether)
        self._start_tether()
        self._wait_ready()
        self._init(restart=True)
        if self.transcript is not None:
            try:
                self.transcript.write("sandbox_reset", reason=reason)
            except Exception:
                pass

    def _reap(self) -> None:
        """After every bash call (spec §6): docker top should show at most
        the lifetime tether (bare "cat", or "/sbin/docker-init -- cat" while
        tini is still attached). Any other row means a backgrounded process
        outlived the call — reset restores the documented contract. A
        nonzero `docker top` itself (the container is stopped, killed, or
        otherwise unreachable — e.g. `docker kill` fired while a `docker
        exec` was in flight) is ALSO a reset trigger: whatever state the
        container is in, a fresh one via reset() is the safe recovery."""
        try:
            top = self._run(["top", self.container], timeout=docker_cli.T_QUERY)
            unreachable = top.returncode != 0
        except docker_cli.DockerError:
            unreachable = True
        if unreachable:
            self.reset("container unreachable after bash")
            return
        lines = top.output.decode("utf-8", errors="replace").splitlines()
        if lines:
            header_cols = lines[0].split()
            n = max(len(header_cols), 1)
            for line in lines[1:]:
                if not line.strip():
                    continue
                fields = line.split(None, n - 1)
                cmd = fields[-1] if fields else ""
                # --entrypoint /bin/cat means the tether row reads "/bin/cat"
                # (and tini's row "/sbin/docker-init -- /bin/cat"); a bare
                # "cat" is what the spec's experiment showed — accept both.
                if cmd in ("cat", "/bin/cat") or cmd.endswith("docker-init -- cat") \
                        or cmd.endswith("docker-init -- /bin/cat"):
                    continue
                self.reset("stray process after bash")
                return
        try:
            oom = self._run(
                ["inspect", "--format", "{{.State.OOMKilled}}", self.container],
                timeout=docker_cli.T_QUERY,
            )
        except docker_cli.DockerError:
            # If inspect fails, don't reset - it would be recursive
            return
        if oom.returncode == 0 and oom.output.decode("utf-8", errors="replace").strip() == "true":
            self.reset("oom")

    def _watchdog_kill(self, reason: str) -> None:
        try:
            self._run(["kill", self.container], timeout=docker_cli.T_LIFECYCLE)
        except docker_cli.DockerError:
            pass

    def _sample_worktree(self) -> tuple:
        """(kbytes, entries) for /work, sampled inside the container. On
        exec failure, resets once and retries; a second failure raises
        SandboxError (spec §6: "If the exec itself fails ... → reset, then
        re-measure; a second failure → sandbox_error")."""
        for attempt in range(2):
            argv = docker_args.exec_argv(
                self.container, ["/bin/sh", "-c", "du -sk /work; find /work | wc -l"]
            )
            try:
                captured = self._run(argv, timeout=docker_cli.T_QUERY)
            except docker_cli.DockerError:
                captured = None
            if captured is not None and captured.returncode == 0:
                lines = captured.output.decode("utf-8", errors="replace").splitlines()
                try:
                    kbytes = int(lines[0].split()[0])
                    entries = int(lines[-1].strip())
                except (IndexError, ValueError):
                    pass
                else:
                    return kbytes, entries
            if attempt == 0:
                self.reset("budget sample failed")
        raise SandboxError("worktree budget sample failed twice in a row")

    def _after_bash(self) -> None:
        self._reap()
        if self.watchdog is not None:
            self.watchdog.check_worktree_budget_once()
            if self.watchdog.violation is not None:
                violation = self.watchdog.violation
                self.watchdog.violation = None
                raise BudgetExceeded(violation)

    def bash(self, command: str, timeout: int = 120) -> str:
        reason = check_bash_command(command)
        if reason:
            return reason  # starts with "BLOCKED:"
        timeout = max(1, min(int(timeout), 600))
        argv = docker_args.exec_argv(
            self.container,
            ["/bin/bash", "-c", 'ulimit -f 524288; exec bash -c "$1"', "_", command],
        )
        if self.watchdog is not None:
            self.watchdog.note_bash_start()
        try:
            captured = self._run(argv, timeout=timeout + 10)
        except docker_cli.DockerError:
            if self.watchdog is not None:
                self.watchdog.note_bash_end()
            result = _cap(f"ERROR: command timed out after {timeout}s.", cap=MAX_BASH_CHARS)
            self._after_bash()
            return result
        if self.watchdog is not None:
            self.watchdog.note_bash_end()
        out = captured.output.decode("utf-8", errors="replace").strip()
        note = " — bash output capped" if captured.truncated else ""
        final_text = f"exit code: {captured.returncode}\n{out}"
        if captured.truncated:
            final_text += "\n[output capped]"
        result = _cap(final_text, cap=MAX_BASH_CHARS, note=note)
        self._after_bash()
        return result
