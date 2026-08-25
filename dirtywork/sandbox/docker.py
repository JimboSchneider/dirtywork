# dirtywork/sandbox/docker.py
from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..budget import BudgetExceeded
from ..guardrails import check_bash_command
from ..procs import Captured, run_capped
from ..tools import (
    MAX_BASH_CHARS,
    MAX_LIST_ENTRIES,
    MAX_READ_BYTES,
    MAX_WRITE_BYTES,
    TMP_FIND_REGEX,
    _apply_edits_once,
    _append_missing,
    _append_oversized,
    _cap,
    _check_write_size,
    _insert_once,
    _not_utf8,
    _number_lines,
    _replace_once,
    _result_too_big,
    describe_change,
    describe_write,
    grep_timeout_result,
    timeout_result,
    tmp_name,
)
from . import SandboxError
from . import docker_args
from . import docker_cli
from . import lifecycle
from ..workspace import WorkspaceError
from .watchdog import Watchdog

from . import export
from ..workspace import host_read_tree
from ..resume import stash_dir_for
from . import strays
from .strays import sandbox_reset_text, stray_kill_text, parse_tether_pid

# Fixed exec timeouts for tools with no user-facing timeout knob — these
# operations should complete near-instantly; a hang means the sandbox
# itself is broken, so DockerError is allowed to propagate as sandbox_error
# rather than being caught and turned into text (unlike bash/grep, whose
# Sandbox signatures accept a caller timeout and whose contract already
# promises a graceful "timed out" text result).
READ_EXEC_TIMEOUT = 30
WRITE_EXEC_TIMEOUT = 30
LIST_EXEC_TIMEOUT = 30

# Matches a full swept temp-file path, and only that -- `Captured.output`
# merges stderr into the same stream (procs.py runs with
# stderr=subprocess.STDOUT), so a `find: '/work/x': Permission denied` line
# or other daemon chatter sits in the same output as the real `-print`ed
# paths. Anchored with fullmatch() below rather than trusted as a substring.
_SWEEP_PATTERN = re.compile(TMP_FIND_REGEX + r"\Z")

# settle window between the kill and the verifying `docker top` (spec #61 §3.4)
_SETTLE_SLEEP = 0.05


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


def _oversized(encoded: bytes):
    """The one 'content too big' refusal for every in-container write, or None.
    write_file checks it BEFORE any exec (so an oversized write costs nothing);
    _write_raw checks it again for edit/insert, whose new text is built from a
    read that was only capped at MAX_READ_BYTES."""
    if len(encoded) > MAX_WRITE_BYTES:
        return (
            f"ERROR: content is {len(encoded)} bytes, over the "
            f"{MAX_WRITE_BYTES}-byte write limit"
        )
    return None


# Spec §2.6. The promote tail is shared by both write scripts so a change to
# how a staged file becomes the real file happens in ONE place:
# `chmod --reference` copies the target's mode (today's `cat >` wrote through
# the inode and preserved it for free -- the temp+`mv` shape is what creates
# the need); `chmod 644` is the new-file fallback; `mv -fT` never moves INTO a
# directory; `rm -f` on any failure is harmless when the temp never existed.
# GNU coreutils (`--reference`, `-T`) ship in the bookworm worker image.
_PROMOTE = ('{ chmod --reference="$1" "$2" 2>/dev/null || chmod 644 "$2"; } && '
            'mv -fT -- "$2" "$1" || { rm -f -- "$2"; exit 1; }')

# `$1` is the target relpath, `$2` the host-generated temp relpath; worker DATA
# arrives on stdin and is never inside the script text. `&&`-chained so a
# failed `cat` can never promote. The writability guard keeps host parity:
# today an unwritable file refuses EACCES, and without it temp+`mv` would
# silently overwrite a 0444 file, since rename needs only directory write.
# Each guard echoes its own diagnostic so _write_raw's stderr wrap never
# renders empty.
WRITE_SCRIPT = (
    'mkdir -p "$(dirname -- "$1")" && '
    '{ [ ! -d "$1" ] || { echo "cannot write $1: Is a directory" >&2; exit 1; }; } && '
    '{ [ -w "$1" ] || [ ! -e "$1" ] || { echo "cannot write $1: Permission denied" >&2; exit 1; }; } && '
    'cat > "$2" && ' + _PROMOTE
)


def _sibling_tmp(rel: str) -> str:
    """The staging path for an in-container write to `rel`: the same directory
    (so `mv` is an atomic same-filesystem rename) with tools.tmp_name()'s
    generated basename."""
    return posixpath.join(posixpath.dirname(rel), tmp_name(posixpath.basename(rel)))


# Spec §1.2: exec 1 of three. `[ ! -h ]` FIRST refuses any symlink (dangling
# included) as exit 3, restoring parity with the host's O_NOFOLLOW probe
# (which refuses symlinks too) and closing a cap bypass: plain `stat -c %s`
# is an lstat, so a symlink to an oversized file dodged caps 2 and 3. Then
# `[ -e ]` then `[ -f ]` -- so a FIFO, device or directory is refused with
# exit 3 BEFORE any reader exec exists that a FIFO could block -- then the
# EXACT byte size on stdout via `stat -Lc`, which follows the link as
# belt-and-suspenders for a race after the `-h` check. The size matters
# because `_read_raw` alone discards it (`head -c N+1` only proves
# "exceeds"), and docker must be able to name the exact result size for a
# file it will never read.
APPEND_GUARD_SCRIPT = ('[ ! -h "$1" ] || exit 3; [ -e "$1" ] || exit 2; '
                       '[ -f "$1" ] || exit 3; stat -Lc %s -- "$1"')

# Spec §2.6: exec 3 of three. `[ -f "$1" ]` is re-checked here so a delete
# between the guard exec and this one still refuses as "does not exist"
# (exit 2) rather than silently creating the file; `-f` also re-excludes
# directories and FIFOs. The writability guard is WRITE_SCRIPT's
# counterpart: without it a 0444 target either leaks a temp-path in a
# cp/cat stderr wrap or, run as root, silently succeeds -- neither is the
# EACCES parity spec §2.6 asks for. `cp` without `-p` is fine -- the shared
# promote's `chmod --reference` runs afterward.
APPEND_WRITE_SCRIPT = (
    '[ -f "$1" ] || exit 2; '
    '[ -w "$1" ] || { echo "cannot append to $1: Permission denied" >&2; exit 1; }; '
    'cp -- "$1" "$2" && cat >> "$2" && ' + _PROMOTE
)


class DockerSandbox:
    """Every tool call and the run lifecycle for docker mode. Constructed
    with injectable `run`/`popen` so unit tests never touch a real daemon —
    only tests marked `docker` (Tasks 13, 15, 16) pass real callables."""

    def __init__(self, cfg: docker_args.DockerConfig, *, run_dir: Path, transcript=None,
                 image_ref: str | None = None, run=docker_cli.run, popen=subprocess.Popen):
        self.cfg = cfg
        self.run_dir = run_dir
        self.transcript = transcript
        self._run = run
        self._popen = popen
        self.container = None
        self.volume = None
        # When the caller already resolved a digest (main()'s docker
        # preflight does, so it can record it in run.json before anything
        # is created), start() must use it verbatim rather than resolving
        # again — resolving twice means two `docker image inspect`/`pull`
        # round trips for one run, and a chance the two resolutions
        # disagree if the tag moved in between. None here (the default, and
        # what every other caller — tests included — gets) preserves the
        # old behavior: start() resolves it itself.
        self.image_ref = image_ref
        self.uid = None
        self.gid = None
        self._tether = None
        self._slug = None
        self._repo = None
        self._worktree = None
        self._base_commit = None
        self._branch = None
        self._seeded = False
        self._stopped = False
        self.watchdog = None
        self._objects_dir = None
        self._export_failed = False
        self._reset_this_call = False  # Track if reset happened in current bash call
        self._shutting_down = False
        # Lock order: _reap_lock -> _reset_lock -> _notices_lock
        self._reap_lock = threading.Lock()
        # Serializes reset()'s whole body (kill -> wait -> new tether -> init)
        # and the watchdog thread's own kill path against each other: reset()
        # can be invoked from the watchdog thread (_sample_worktree's
        # exec-failure path) and from the main thread (_reap() after a bash
        # call) concurrently. _reset_this_call is set/cleared under this lock.
        self._reset_lock = threading.Lock()
        # State for tether pid and notice queue
        self._tether_pid = None
        self._tether_warned = False
        self._notices = []
        self._notices_lock = threading.Lock()

    @staticmethod
    def check_name_collision(run, slug: str) -> None:
        """Refuse to reuse a container/volume name that already exists (spec
        §3 'Name collision'): never remove anything this invocation did not
        create — a collision is either a stale leftover or something else's
        resource, and both deserve a human. Raises SandboxError. Called both
        from __main__'s pre-worktree preflight and from start() itself
        (defense in depth against a same-slug race between the two).

        The error names the concrete manual recipe rather than a
        `dirtywork runs …` subcommand — that cleanup CLI is SP3, not shipped
        in this release (Important #7)."""
        name = docker_args.container_name(slug)
        vol = docker_args.volume_name(slug)
        recipe = (
            f"dirtywork will not remove a resource it did not create this run — "
            f"if it is a stale leftover, remove it manually: "
            f"`docker rm -f {name}`; `docker volume rm {vol}`"
        )
        c_inspect = run(["container", "inspect", name], timeout=docker_cli.T_QUERY)
        if c_inspect.returncode == 0:
            raise SandboxError(f"container {name} already exists; {recipe}")
        v_inspect = run(["volume", "inspect", vol], timeout=docker_cli.T_QUERY)
        if v_inspect.returncode == 0:
            raise SandboxError(f"volume {vol} already exists; {recipe}")

    def start(self, worktree: Path, repo: Path, slug: str, base_commit: str, *, branch: str | None = None, seed_from_worktree: bool = False) -> None:
        self._worktree = worktree
        self._repo = repo
        self._slug = slug
        self._base_commit = base_commit
        self._branch = branch or f"dirtywork/{slug}"
        self._seeded = seed_from_worktree
        name = docker_args.container_name(slug)
        vol = docker_args.volume_name(slug)

        self.check_name_collision(self._run, slug)

        self.uid = os.getuid() if os.name == "posix" else 1000
        self.gid = os.getgid() if os.name == "posix" else 1000

        try:
            objects_dir = docker_cli.validate_objects_dir(repo)
            self._objects_dir = objects_dir
        except WorkspaceError as e:
            raise SandboxError(str(e)) from e
        if self.image_ref is None:
            # Preflight rule: the pin applies only when self.cfg.image == docker_args.DEFAULT_IMAGE; a custom image is never pinned.
            self.image_ref = docker_cli.resolve_image(
                self.cfg.image, run=self._run, pinned_digest=docker_args.pin_for(self.cfg.image))
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
        self._discover_tether()
        self._init(restart=seed_from_worktree)
        if seed_from_worktree:
            self._seed_from_worktree()
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
        lifecycle.init_worker_git(self._run, self.container, branch=self._branch, base_commit=self._base_commit, restart=restart, layout="gitfile")

    def _check(self, res, what: str) -> None:
        """Raise SandboxError with the captured output when a docker step failed."""
        if res.returncode != 0:
            raise SandboxError(f"{what}: {res.output.decode('utf-8', 'replace')[:500]}")

    def _discover_tether(self) -> None:
        """Discover the tether pid by running a shell script inside the container."""
        argv = docker_args.exec_argv(
            self.container, ["/bin/sh", "-c", strays.TETHER_DISCOVERY_SCRIPT]
        )
        try:
            captured = self._run(argv, timeout=docker_cli.T_QUERY)
        except docker_cli.DockerError:
            pid = None
        else:
            if captured.returncode != 0:
                pid = None
            else:
                pid = parse_tether_pid(captured.output)
        self._tether_pid = pid
        if pid is None and not self._tether_warned:
            print("tether pid unknown; a stray process will reset the container", file=sys.stderr)
            self._tether_warned = True

    def _queue_notice(self, kind: str, text: str) -> None:
        """Queue a notice (kind, text) under _notices_lock."""
        with self._notices_lock:
            self._notices.append((kind, text))

    def drain_notices(self) -> list[tuple[str, str]]:
        """Return queued notices and clear them, under _notices_lock."""
        with self._notices_lock:
            notices = list(self._notices)
            self._notices.clear()
            return notices

    def _stop_container(self) -> None:
        self._shutting_down = True
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
        separate to report.

        `_stop_container()` (called first, below) already stops and joins
        the watchdog thread — a disk-floor or fail-closed kill can fire
        after the model's last tool call, with nothing left to consume
        `self.watchdog.violation` (`_after_bash` only runs on a bash call).
        Read and clear it here, right after the thread is guaranteed dead,
        so a violation that landed while idle still surfaces instead of
        being silently reported `completed`. The export still runs — the
        volume is intact and the work is worth salvaging regardless.

        Spec §2.5 (execution amendment, fix round 1): the stale-temp sweep
        runs HERE, one exec against the still-alive WORKER container, right
        before `_stop_container()` below -- not in export_run. The EXPORT
        container's `/work` volume mount is readonly by design
        (docker_args.export_create_argv), so a `find … -delete` there would
        get EROFS on every match and silently do nothing. Reporting is never
        silent on a partial failure: the swept-N note fires whenever at least
        one real temp-file line was seen, regardless of exit code, and a
        non-zero rc gets its own note so an EROFS or other mid-sweep error is
        never mistaken for "nothing to sweep". The count itself is filtered
        through `_SWEEP_PATTERN` -- `Captured.output` merges stderr, so a
        `find: permission denied` or daemon error line must never inflate
        the swept count.

        The sweep is best-effort, not part of the run's success/failure
        boundary: a `DockerError` here (timeout, daemon hiccup) is caught
        and reported, never allowed to escape and skip export -- the salvage
        path (export -> host_read_tree) is what actually protects the run's
        output, and it must still run."""
        sweep_argv = docker_args.exec_argv(
            self.container, ["/usr/bin/find", "/work", "-type", "f", "-regextype",
                             "posix-extended", "-regex", TMP_FIND_REGEX, "-print", "-delete"])
        try:
            sweep_captured = self._run(sweep_argv, timeout=docker_cli.T_EXPORT_STEP)
        except docker_cli.DockerError as e:
            print(f"sweep failed: {e}", file=sys.stderr)
        else:
            swept = [line for line
                    in sweep_captured.output.decode("utf-8", errors="replace").splitlines()
                    if line.strip() and _SWEEP_PATTERN.fullmatch(line.strip())]
            if swept:
                plural = "" if len(swept) == 1 else "s"
                print(f"swept {len(swept)} stale temp file{plural}", file=sys.stderr)
            if sweep_captured.returncode != 0:
                print(f"sweep incomplete (rc {sweep_captured.returncode})", file=sys.stderr)
        self._stop_container()
        watchdog_violation = self.watchdog.violation if self.watchdog is not None else None
        # D1: only meaningful when watchdog_violation itself is set -- see
        # RunArtifacts.watchdog_violation_kind.
        watchdog_violation_kind = (
            self.watchdog.violation_kind if self.watchdog is not None and watchdog_violation else None
        )
        if self.watchdog is not None:
            self.watchdog.violation = None
            self.watchdog.violation_kind = "budget"
        label = docker_args.repo_label(self._repo)
        aside = self._stash_prior_worktree() if self._seeded else None
        try:
            artifacts = export.export_run(
                self.cfg, slug=self._slug, base_commit=self._base_commit,
                worktree=self._worktree, run_dir=self.run_dir, objects_dir=self._objects_dir,
                image_ref=self.image_ref, uid=self.uid, gid=self.gid, repo_label=label,
                run=self._run, popen=self._popen,
            )
        except BaseException:
            # export_run converts its own failures to export_failed; anything
            # that still escapes (a bug, Ctrl-C) must not leave the prior work
            # stranded in the stash.
            if aside is not None:
                self._restore_prior_worktree(aside)
            raise
        artifacts.watchdog_violation = watchdog_violation
        artifacts.watchdog_violation_kind = watchdog_violation_kind
        if artifacts.export_status.startswith("export_failed"):
            # Leave the host worktree as it was: on a fresh run that is the
            # .git file only (read-tree after a failed export would make host
            # `git status` claim mass deletions that never happened); on a
            # resume it is the prior run's exported work, restored from the
            # stash — the export must never destroy work that was safe on disk.
            self._export_failed = True
            if aside is not None:
                self._restore_prior_worktree(aside)
            return artifacts
        if aside is not None:
            shutil.rmtree(aside, ignore_errors=True)
        host_read_tree(self._worktree)
        return artifacts

    def _stash_prior_worktree(self) -> Path:
        """Resume: export_run requires a host worktree holding only the .git
        file, but a resumed run's worktree still holds the prior export.
        Move that content aside (never delete it before the export is known
        to have succeeded) into a sibling directory the export cannot see;
        finalize() removes the stash after a successful export or restores
        it after a failed one. The stash is unique to this run's slug and is
        never pre-cleared: a stash left by an interrupted earlier resume is
        someone's only copy of their work, and check_resumable refuses to
        start a resume while one exists."""
        aside = stash_dir_for(self._worktree, self._slug)
        aside.mkdir()  # exists → FileExistsError: never reuse or clear a stash
        for entry in self._worktree.iterdir():
            if entry.name == ".git" and entry.is_file() and not entry.is_symlink():
                continue
            os.rename(str(entry), str(aside / entry.name))
        return aside

    def _restore_prior_worktree(self, aside: Path) -> None:
        """Undo _stash_prior_worktree after a failed export: the worktree is
        back to exactly the state the resume started from."""
        export._cleanup_to_dot_git_only(self._worktree)
        for entry in aside.iterdir():
            os.rename(str(entry), str(self._worktree / entry.name))
        aside.rmdir()

    def _seed_from_worktree(self) -> None:
        """Resume: mirror the host worktree into /work (deletions included —
        the index was populated with `read-tree HEAD` and no files, so
        whatever the tar carries is exactly what the worker sees). The
        operator's own worktree is the trusted direction; .git (the gitdir
        pointer file) is excluded; COPYFILE_DISABLE stops macOS tar from
        adding ._ AppleDouble entries."""
        env = dict(os.environ)
        env["COPYFILE_DISABLE"] = "1"
        tar_out = self._popen(
            ["tar", "-C", str(self._worktree), "--exclude=./.git", "-cf", "-", "."],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
        )
        exec_argv = ["docker"] + docker_args.exec_argv(
            self.container, ["tar", "-C", "/work", "-xf", "-"], stdin=True)
        tar_in = self._popen(exec_argv, stdin=tar_out.stdout,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        timed_out = []

        def _kill_both():
            timed_out.append(True)
            tar_out.kill()
            tar_in.kill()

        # The seed moves the same data as the export streams, in the other
        # direction, so it gets the export-step budget, not the lifecycle one.
        timer = threading.Timer(docker_cli.T_EXPORT_STEP, _kill_both)
        timer.start()
        try:
            if tar_out.stdout is not None:
                tar_out.stdout.close()  # the child owns the pipe now; lets SIGPIPE reach tar
            tar_in.wait()
            tar_out.wait()
        finally:
            timer.cancel()
        if tar_out.returncode != 0 or tar_in.returncode != 0:
            why = (f"timed out after {docker_cli.T_EXPORT_STEP}s" if timed_out
                   else f"tar rc={tar_out.returncode}, docker exec tar rc={tar_in.returncode}")
            raise SandboxError(f"resume seed failed: {why}")

    def _read_raw(self, path: str, *, strict: bool = False, tool: str | None = None):
        """Spec §5.1: `tool` names the tool whose call this read serves, so a
        non-UTF-8 file refuses with the HOST's wording (`<path> is not valid
        UTF-8 text; <tool> only works on text files`) instead of the legacy
        `refusing to edit`, which was wrong for every insert/apply/append. It
        is only consulted on a `strict` read."""
        rel, err = _rel(path)
        if err:
            return None, err
        argv = docker_args.exec_argv(
            self.container, ["/usr/bin/head", "-c", str(MAX_READ_BYTES + 1), "--", rel]
        )
        # docker_cli.run's default capture cap (procs.MAX_CAPTURE_BYTES, 1 MiB)
        # is meant for ordinary exec output, not a file body -- request a cap
        # above what `head` can ever emit (MAX_READ_BYTES + 1) so a 1-5 MiB
        # file is never silently cut short at 1 MiB. With that explicit cap
        # the exceeds check below is live again (it was dead code under the
        # 1 MiB default, since captured.output could never exceed it); a
        # `truncated` Captured is refused defensively even though the exec
        # itself should never produce one now.
        captured = self._run(argv, timeout=READ_EXEC_TIMEOUT, cap=MAX_READ_BYTES + 1)
        if captured.returncode != 0:
            return None, (
                f"ERROR: cannot read '{path}': "
                f"{captured.output.decode('utf-8', 'replace')[:500]}"
            )
        if captured.truncated or len(captured.output) > MAX_READ_BYTES:
            return None, (
                f"ERROR: '{path}' exceeds {MAX_READ_BYTES} bytes; refusing to read"
            )
        if strict:
            try:
                text = captured.output.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                if tool is not None:
                    return None, _not_utf8(path, tool)
                # No shipped caller reaches this since 0.10 -- every strict
                # read names its tool. Kept so a direct caller of this private
                # method gets a coherent refusal rather than a formatted None.
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

    def _write_raw(self, path: str, encoded: bytes) -> str:
        """The in-container write itself: '' on success, an 'ERROR: …' string
        otherwise. Split out of write_file so edit_file (and, from Task 2,
        insert_before/insert_after) can write WITHOUT paying for write_file's
        own read-back — one read exec per edit, as before."""
        too_big = _oversized(encoded)
        if too_big:
            return too_big
        rel, err = _rel(path, writing=True)
        if err:
            return err
        argv = docker_args.exec_argv(
            self.container,
            ["/bin/sh", "-c", WRITE_SCRIPT, "_", rel, _sibling_tmp(rel)],
            stdin=True,
        )
        captured = self._run(argv, timeout=WRITE_EXEC_TIMEOUT, stdin=encoded)
        if captured.returncode != 0:
            return (
                f"ERROR: cannot write '{path}': "
                f"{captured.output.decode('utf-8', 'replace')[:500]}"
            )
        return ""

    def write_file(self, path: str, content: str) -> str:
        encoded = content.encode("utf-8")
        # Best-effort 'before' picture for the echoed diff (spec §3.1); an
        # unreadable/missing file yields None and reads as a new file.
        # _oversized/_rel checks are owned by _write_raw (DRY) — one extra
        # `head` exec on an oversized/invalid-path write is acceptable.
        old_text, _unused = self._read_raw(path, strict=True, tool="write_file")
        err = self._write_raw(path, encoded)
        if err:
            return err
        return describe_write(path, old_text, content, len(encoded))

    def _append_guard(self, path: str, rel: str, text_len: int):
        """Spec §1.2 exec 1: (size, None) or (None, error). Decides existence,
        regular-file-ness and both size caps before anything is read."""
        argv = docker_args.exec_argv(
            self.container, ["/bin/sh", "-c", APPEND_GUARD_SCRIPT, "_", rel])
        captured = self._run(argv, timeout=READ_EXEC_TIMEOUT)
        if captured.returncode == 2:
            return None, _append_missing(path)
        if captured.returncode == 3:
            return None, f"ERROR: cannot append to '{path}': not a regular file"
        text = captured.output.decode("utf-8", "replace")
        if captured.returncode != 0:
            return None, f"ERROR: cannot append to '{path}': {text[:500]}"
        try:
            size = int(text.strip())
        except ValueError:
            return None, f"ERROR: cannot append to '{path}': {text[:500]}"
        if size > MAX_READ_BYTES or size + text_len > MAX_WRITE_BYTES:
            # Caps 2 and 3 share one sentence, exactly as on the host, so a
            # file too large to read reads as un-appendable rather than
            # surfacing _read_raw's "refusing to read" wording.
            return None, _result_too_big(size + text_len)
        return size, None

    def _append_write(self, path: str, rel: str, encoded: bytes) -> str:
        """Spec §2.6 exec 3: '' on success, an 'ERROR: …' string otherwise."""
        argv = docker_args.exec_argv(
            self.container,
            ["/bin/sh", "-c", APPEND_WRITE_SCRIPT, "_", rel, _sibling_tmp(rel)],
            stdin=True,
        )
        captured = self._run(argv, timeout=WRITE_EXEC_TIMEOUT, stdin=encoded)
        if captured.returncode == 2:
            return _append_missing(path)
        if captured.returncode != 0:
            return (f"ERROR: cannot append to '{path}': "
                    f"{captured.output.decode('utf-8', 'replace')[:500]}")
        return ""

    def append_file(self, path: str, text: str) -> str:
        """Spec §1.2: three execs, in the same cap order tools.append_file
        uses, so both modes emit identical strings from identical conditions.
        The `text` argument is capped by _append_oversized BEFORE any exec;
        this path never routes the payload through _oversized, which says
        `ERROR: content is <n> bytes, over the <limit>-byte write limit` --
        write_file's noun, and the wrong fix for an append. (The longer
        host-only form with the `; write the file in smaller pieces` tail
        lives in tools.write_file and has no counterpart in this module.)"""
        encoded = text.encode("utf-8")
        too_big = _append_oversized(encoded)
        if too_big:
            return too_big
        rel, err = _rel(path, writing=True)
        if err:
            return err
        size, err = self._append_guard(path, rel, len(encoded))
        if err:
            return err
        # The guard already refused a file over MAX_READ_BYTES with the
        # result-cap string; the exceeds-trap just below covers the race
        # where the file grows between that exec and this one.
        # `tool="append_file"` is what makes a non-UTF-8 file refuse with
        # the host's append wording rather than the legacy `refusing to
        # edit` (spec §5.1).
        old_text, err = self._read_raw(path, strict=True, tool="append_file")
        if err:
            if err.startswith(f"ERROR: '{path}' exceeds "):
                # TOCTOU: the guard's snapshot approved a size that
                # _read_raw's own cap disproved a moment later. Trapped
                # here so read_file's "refusing to read" wording -- the
                # wrong noun for an append -- can never surface; reported
                # against the guard's last-known size, the best number
                # available (mirrors tools.append_file's probe-then-read
                # race handling, spec §2.6).
                return _result_too_big(size + len(encoded))
            return err
        # Re-checked against what was actually read, mirroring
        # tools.append_file: the guard's size is a moment old, and the file
        # may have grown in place since ("the probe's size is a moment
        # old", spec §2.6).
        if len(old_text.encode("utf-8")) + len(encoded) > MAX_WRITE_BYTES:
            return _result_too_big(len(old_text.encode("utf-8")) + len(encoded))
        err = self._append_write(path, rel, encoded)
        if err:
            return err
        return describe_change(path, old_text, old_text + text, verb="Appended to")

    def _transform_file(self, path: str, transform, *, tool: str) -> str:
        """Read → transform → write inside the container: the same shape as
        tools._transform_file, over the same transforms, so edit_file,
        insert_before and insert_after are three transforms over ONE path per
        backend (spec §3.2) and the two backends can never disagree about an
        anchor rule or an error string. `tool` is forwarded to _read_raw so a
        non-UTF-8 file refuses with the host's wording, naming the tool the
        model called (spec §5.1)."""
        text, err = self._read_raw(path, strict=True, tool=tool)
        if err:
            return err
        new_text, result = transform(text)
        if new_text is None:
            return result
        # Spec §1.5: the shared cap fires BEFORE _write_raw's own _oversized
        # check, so all four in-place tools refuse an oversized result with the
        # same string here as on the host. write_file still reaches _oversized
        # with its own wording, which is why that check stays where it is.
        too_big = _check_write_size(new_text)
        if too_big:
            return too_big
        err = self._write_raw(path, new_text.encode("utf-8"))
        if err:
            return err
        return result

    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        return self._transform_file(path, _replace_once(path, old_string, new_string),
                                    tool="edit_file")

    def apply_edits(self, path: str, edits: list) -> str:
        return self._transform_file(path, _apply_edits_once(path, edits),
                                    tool="apply_edits")

    def insert_before(self, path: str, anchor: str, text: str) -> str:
        return self._transform_file(path, _insert_once(path, anchor, text, "before"),
                                    tool="insert_before")

    def insert_after(self, path: str, anchor: str, text: str) -> str:
        return self._transform_file(path, _insert_once(path, anchor, text, "after"),
                                    tool="insert_after")

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
        except docker_cli.DockerError as e:
            # Spec §4.2: the same discrimination as bash below. A grep timeout
            # keeps its own (unchanged) wording and does NOT count toward
            # `timeouts` or the `timeout` nudge -- those are about commands the
            # WORKER ran, and grep is the harness searching on its behalf.
            if e.timed_out:
                # Kill any stray process that continued after the timeout
                self._kill_abandoned_exec()
                return grep_timeout_result(timeout)
            return f"ERROR: grep failed: {e}"
        if captured.returncode not in (0, 1):
            return f"ERROR: grep failed: {captured.output.decode('utf-8', 'replace')[:500]}"
        text = captured.output.decode("utf-8", errors="replace")
        if not text.strip():
            return "No matches found."
        # Strip leading ./ from paths
        lines = [(l[2:] if l.startswith("./") else l) for l in text.splitlines()]
        return _cap("\n".join(lines), note=" — narrow the pattern or path for full results")

    def reset(self, reason: str, *, strays=None, strays_total=None) -> None:
        """Spec §3 "Reset" (used on a stray process, OOM, or a watchdog
        kill): docker kill SIGKILLs PID 1, so the whole container namespace
        dies and its tmpfs is wiped — but the volume and its contents
        persist (verified). Fresh tether, ready-wait, then init's restart
        variant (index only — never touches /work, so the working tree
        survives a reset even though the worker's git metadata in /gitdir
        does not). The whole body runs under `self._reset_lock` so a
        concurrent reset() from the watchdog thread and the main thread
        cannot interleave their docker calls or race `_start_tether`/`_init`
        (Important #4)."""
        with self._reset_lock:
            # Mark that a reset will happen in this call (for _after_bash to skip budget sample)
            self._reset_this_call = True
            # If shutting down, don't perform any docker calls or write events
            if self._shutting_down:
                return
            try:
                self._run(["kill", self.container], timeout=docker_cli.T_LIFECYCLE)
            except docker_cli.DockerError:
                pass
            # Wait for the container to actually stop before starting again
            try:
                self._run(["wait", self.container], timeout=docker_cli.T_LIFECYCLE)
            except docker_cli.DockerError:
                # Container already stopped returns immediately with non-zero
                pass
            if self._tether is not None:
                lifecycle.close_tether(self._tether)
            self._start_tether()
            self._wait_ready()
            # A reset starts a new container life, so the warning can happen again
            self._tether_warned = False
            self._discover_tether()
            self._init(restart=True)
            if self.transcript is not None:
                try:
                    # Build kwargs for transcript.write
                    kwargs = {"reason": reason}
                    if strays is not None:
                        kwargs["strays"] = strays
                    if strays_total is not None:
                        kwargs["strays_total"] = strays_total
                    self.transcript.write("sandbox_reset", **kwargs)
                except Exception:
                    pass
            # Queue a notice for the sandbox reset
            self._queue_notice("sandbox_reset", sandbox_reset_text(reason))

    def _kill_strays(self) -> bool:
        """Kill stray processes using the STRAY_KILL_SCRIPT.

        Returns True iff rc == 0 (DockerError -> False).
        """
        argv = docker_args.exec_argv(
            self.container, ["/bin/sh", "-c", strays.STRAY_KILL_SCRIPT, "_", str(self._tether_pid)]
        )
        try:
            result = self._run(argv, timeout=docker_cli.T_QUERY)
            return result.returncode == 0
        except docker_cli.DockerError:
            return False

    def _kill_abandoned_exec(self) -> None:
        """Kill any stray exec process that continued after a timed-out DockerError.

        Any tool exec that continues after a timed-out DockerError must call this
        before the next bash/grep call completes. The in-container process is
        still running and the next bash call's reap would otherwise blame the
        worker's command for it."""
        if self._tether_pid is None:
            return
        with self._reap_lock:
            self._kill_strays()

    def _sweep_locks(self) -> tuple:
        """The post-kill lock sweep (spec #61 §3.4/§3.5): after a successful
        in-place kill no process exists, so every `*.lock` / `gc.pid` under
        /gitdir is stale by definition. Returns (paths, total) from
        strays.cap_locks; a DockerError never escalates -- the state the kill
        just saved must not be lost to a slow `find`."""
        argv = docker_args.exec_argv(self.container, strays.LOCK_SWEEP_ARGV)
        try:
            captured = self._run(argv, timeout=docker_cli.T_QUERY)
        except docker_cli.DockerError as e:
            print(f"lock sweep incomplete ({e})", file=sys.stderr)
            return [], None
        paths = strays.parse_locks(captured.output)
        if captured.returncode != 0:
            print(f"lock sweep incomplete (rc {captured.returncode})", file=sys.stderr)
        if captured.truncated:
            print("lock sweep incomplete (output truncated)", file=sys.stderr)
        return strays.cap_locks(paths, captured.truncated)

    def _reap(self) -> bool:
        """After every bash call (spec §6; the #61 §3.4 ladder): `docker top`
        should show at most the lifetime tether. Any other row is a stray --
        killed in place (STRAY_KILL_SCRIPT, then up to three settle re-checks),
        after which the OOM flag is inspected, stale git locks are swept and a
        `stray_kill` event plus notice record what happened; only when the kill
        cannot be performed (no tether pid) or verified does the container
        reset, as it did before 1.0. A nonzero `docker top` (the container is
        stopped, killed, or otherwise unreachable) is a reset trigger as before.

        Returns True if a reset was performed OR the container is already dead
        by a watchdog kill (do not sample), False otherwise."""
        # Don't perform multiple resets in one call
        if self._reset_this_call:
            return False
        # Use _reap_lock to serialize with _sample_worktree's non-blocking sample;
        # ALL work after the guard (top, kill, settle re-checks, OOM inspect,
        # sweep, event/notice, and any reset) runs inside the lock to prevent
        # watchdog sampling from capturing partial state.
        with self._reap_lock:
            try:
                top = self._run(["top", self.container], timeout=docker_cli.T_QUERY)
                unreachable = top.returncode != 0
            except docker_cli.DockerError:
                unreachable = True
            if unreachable:
                # If watchdog already killed the container, don't sample
                if self.watchdog is not None and self.watchdog.violation is not None:
                    return True
                self.reset("container unreachable after bash")
                return True
            rows = strays.stray_rows(top.output)
            capped, total = strays.cap_strays(rows) if rows else ([], None)
            if rows:
                if self._tether_pid is None or not self._kill_strays():
                    # If watchdog already killed the container, don't sample
                    if self.watchdog is not None and self.watchdog.violation is not None:
                        return True
                    self.reset("stray process after bash", strays=capped, strays_total=total)
                    return True
                for _ in range(3):
                    time.sleep(_SETTLE_SLEEP)
                    try:
                        top = self._run(["top", self.container], timeout=docker_cli.T_QUERY)
                        unreachable = top.returncode != 0
                    except docker_cli.DockerError:
                        unreachable = True
                    if unreachable:
                        # If watchdog already killed the container, don't sample
                        if self.watchdog is not None and self.watchdog.violation is not None:
                            return True
                        self.reset("container unreachable after bash")
                        return True
                    if not strays.stray_rows(top.output):
                        break
                else:
                    # If watchdog already killed the container, don't sample
                    if self.watchdog is not None and self.watchdog.violation is not None:
                        return True
                    self.reset("stray process after bash", strays=capped, strays_total=total)
                    return True
            try:
                oom = self._run(
                    ["inspect", "--format", "{{.State.OOMKilled}}", self.container],
                    timeout=docker_cli.T_QUERY,
                )
            except docker_cli.DockerError:
                # If inspect fails, don't reset - it would be recursive
                return False
            if oom.returncode == 0 and oom.output.decode("utf-8", errors="replace").strip() == "true":
                # If watchdog already killed the container, don't sample
                if self.watchdog is not None and self.watchdog.violation is not None:
                    return True
                if rows:
                    self.reset("oom", strays=capped, strays_total=total)
                else:
                    self.reset("oom")
                return True
            if rows:
                locks, locks_total = self._sweep_locks()
                fields = {"strays": capped}
                if total is not None:
                    fields["strays_total"] = total
                if locks:
                    fields["locks_removed"] = locks
                if locks_total is not None:
                    fields["locks_removed_total"] = locks_total
                if self.transcript is not None:
                    try:
                        self.transcript.write("stray_kill", **fields)
                    except Exception:
                        pass
                self._queue_notice("stray_kill", stray_kill_text(capped, total, locks))
            return False

    def _watchdog_kill(self, reason: str) -> None:
        # Goes through the same lock as reset() (Important #4): without it, a
        # watchdog-thread kill fired while the main thread is mid-reset()
        # could land between reset()'s own kill/wait/start calls.
        with self._reset_lock:
            # Mark that a reset will happen in this call (for _after_bash to skip budget sample)
            self._reset_this_call = True
            try:
                self._run(["kill", self.container], timeout=docker_cli.T_LIFECYCLE)
            except docker_cli.DockerError:
                pass

    def _measure_worktree_once(self):
        """One `du`/`find` exec against /work. Returns (kbytes, entries) on
        a clean, parseable result, or None on any failure (exec error,
        nonzero exit, unparseable output) -- callers decide what a failed
        measurement means."""
        argv = docker_args.exec_argv(
            self.container, ["/bin/sh", "-c", "du -sk /work; find /work | wc -l"]
        )
        try:
            captured = self._run(argv, timeout=docker_cli.T_QUERY)
        except docker_cli.DockerError:
            return None
        if captured is None or captured.returncode != 0:
            return None
        lines = captured.output.decode("utf-8", errors="replace").splitlines()
        try:
            kbytes = int(lines[0].split()[0])
            entries = int(lines[-1].strip())
        except (IndexError, ValueError):
            return None
        return kbytes, entries

    def _sample_worktree(self, *, wait=True) -> tuple | None:
        """(kbytes, entries) for /work, sampled inside the container. On
        exec failure, resets once and retries; a second failure raises
        SandboxError (spec §6: "If the exec itself fails ... → reset, then
        re-measure; a second failure → sandbox_error").

        D2: this is also the watchdog THREAD's own `sample` callback
        (ticking every 5s while a bash call is in flight), independent of
        _after_bash's own call -- so two calls can race a reset performed
        by one of them. The retry is deterministic to avoid measuring a
        possibly mid-reset container: a failed attempt resets (once, under
        reset()'s own _reset_lock) and re-measures ONLY if no reset had
        already happened this bash call; if one already had (this call
        itself is then already "post-reset"), a single failed attempt is
        sufficient -- raise immediately rather than retrying blind against
        a container another thread may still be resetting.

        The `wait` parameter controls blocking behavior:
        - wait=True (default): today's behaviour exactly. On second failure,
          raises SandboxError.
        - wait=False (watchdog thread): non-blocking. Acquires _reap_lock
          with blocking=wait; if lock is busy, returns None immediately.
          On second failure, returns None (no exception) -- the main
        thread's sample after this call escalates.

        Returns (kbytes, entries) on success. With wait=True, raises
        SandboxError on failure. With wait=False, returns None on failure."""
        # If shutting down, return None immediately (no docker calls)
        if self._shutting_down:
            return None
        # Try to acquire _reap_lock with blocking control
        acquired = self._reap_lock.acquire(blocking=wait)
        try:
            if not acquired:
                # Non-blocking mode and lock is busy - return None immediately
                return None
            result = self._measure_worktree_once()
            if result is not None:
                return result
            if self.watchdog is not None and self.watchdog.violation is not None:
                # The watchdog killed the container and recorded why (spec #61
                # §3.6): the caller consumes the violation; do not reset or raise.
                return None
            if not self._reset_this_call:
                # If shutting down, don't reset (no docker calls)
                if self._shutting_down:
                    return None
                self.reset("budget sample failed")
                result = self._measure_worktree_once()
                if result is not None:
                    return result
                if self.watchdog is not None and self.watchdog.violation is not None:
                    return None
                if wait:
                    raise SandboxError("worktree budget sample failed twice in a row")
                # Non-blocking: return None on second failure (main thread escalates)
                return None
            if wait:
                raise SandboxError("worktree budget sample failed after an earlier reset this call")
            # Non-blocking: return None if already reset (main thread escalates)
            return None
        finally:
            if acquired:
                self._reap_lock.release()

    def _raise_violation(self) -> None:
        """Consume and raise a watchdog violation."""
        violation = self.watchdog.violation
        kind = self.watchdog.violation_kind
        self.watchdog.violation = None
        self.watchdog.violation_kind = "budget"
        if kind == "sandbox_error":
            # D1: a watchdog-thread sample() failure (spec §6's
            # "second failure -> sandbox_error") is a sandbox
            # failure, not a budget breach -- raise the same
            # exception type the main-thread _sample_worktree path
            # already raises for the identical condition.
            raise SandboxError(violation)
        raise BudgetExceeded(violation)

    def _after_bash(self) -> None:
        try:
            if self.watchdog is not None and self.watchdog.violation is not None:
                self._raise_violation()
            self._reap()
            if self.watchdog is not None:
                # Skip only the re-SAMPLING when a reset happened this call (the
                # container was just rebuilt, so there is nothing meaningful to
                # measure yet) -- but ALWAYS consume a violation the watchdog
                # thread may already have recorded (Important #3). Swallowing it
                # here would let a run continue past a budget breach until the
                # next bash call or export self-corrects it.
                if not self._reset_this_call:
                    # Synchronous sample after bash: wait=True for blocking behavior
                    self.watchdog.check_worktree_budget_once(wait=True)
                if self.watchdog.violation is not None:
                    self._raise_violation()
        finally:
            with self._reset_lock:
                self._reset_this_call = False

    def bash(self, command: str, timeout: int = 120) -> str:
        reason = check_bash_command(command, sandboxed=True)
        if reason:
            return reason  # starts with "BLOCKED:"
        timeout = max(1, min(int(timeout), 600))
        argv = docker_args.exec_argv(
            self.container,
            ["/bin/bash", "-c", 'ulimit -f 524288; exec bash -c "$1"', "_", command],
        )
        if self.watchdog is not None:
            self.watchdog.note_bash_start()
        err = None
        captured = None
        try:
            captured = self._run(argv, timeout=timeout + 10)
        except docker_cli.DockerError as exc:
            err = exc
        finally:
            if self.watchdog is not None:
                self.watchdog.note_bash_end()
        # Spec §4.2: only a REAL expired timeout renders as the canonical
        # timeout text. Any other DockerError (a killed container, an exec
        # that could not start) gets the host's own non-timeout wording, so
        # an ordinary failure is never read as "it might still be running"
        # -- and never counts as a timeout downstream.
        if err is not None:
            result = (timeout_result(timeout) if err.timed_out
                      else f"ERROR: bash failed: {err}")
        else:
            out = captured.output.decode("utf-8", errors="replace").strip()
            note = " — bash output capped" if captured.truncated else ""
            final_text = f"exit code: {captured.returncode}\n{out}"
            if captured.truncated:
                final_text += "\n[output capped]"
            result = _cap(final_text, cap=MAX_BASH_CHARS, note=note)
        self._after_bash()
        return result
