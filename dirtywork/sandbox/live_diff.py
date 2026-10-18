"""Bounded, read-only previews of a live worker's cumulative changes.

The worker's Git configuration and index are not used to generate the diff.
A private index starts at the run's base commit, so intermediate commits and
staging decisions do not hide changes to the current files.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from ..procs import Captured, run_capped
from . import docker_args, docker_cli

MAX_PATCH_BYTES = 1024 * 1024
MAX_UNTRACKED_FILES = 256
CAPTURE_TIMEOUT = 15
_INNER_TIMEOUT = "10s"
_TOO_LARGE = 3


class LiveDiffError(Exception):
    """A preview could not be captured; the run itself can continue."""


class LiveDiffUnavailable(LiveDiffError):
    """The live worktree or its base could not be read within the time limit."""


class LiveDiffTooLarge(LiveDiffError):
    """The preview exceeds its fixed byte or new-file limit."""


# This exact program runs locally and inside the worker. Never interpolate a
# worker path, commit, filename, or model-provided command into this program.
# -I prevents Python startup imports from the worktree or PYTHON* environment.
_CAPTURE_SCRIPT = r'''
import errno
import os
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time

CAP = 1048576
MAX_NEW = 256
DEADLINE = time.monotonic() + 8
GIT = "/usr/bin/git"
ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
}
FLAGS = [GIT, "--no-optional-locks", "--no-pager", "--no-replace-objects",
         "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]
DIFF = ["--no-ext-diff", "--no-textconv", "--no-renames", "--no-color",
        "--ignore-submodules=all", "--src-prefix=a/", "--dst-prefix=b/"]

class Unavailable(Exception):
    pass

class TooLarge(Exception):
    pass


def command(args, *, env=None, cwd=None, cap=CAP, codes=(0,)):
    if time.monotonic() >= DEADLINE:
        raise Unavailable("live diff timed out")
    proc = subprocess.Popen(FLAGS + args, cwd=cwd, env=ENV if env is None else env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    out = bytearray()
    err = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, out)
            selector.register(proc.stderr, selectors.EVENT_READ, err)
            while selector.get_map():
                left = DEADLINE - time.monotonic()
                if left <= 0:
                    raise Unavailable("live diff timed out")
                for key, unused in selector.select(left):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    target = key.data
                    limit = cap if target is out else 4096
                    if len(target) + len(chunk) > limit:
                        if target is out:
                            raise TooLarge("live diff exceeds preview limit")
                        raise Unavailable("live diff command failed")
                    target.extend(chunk)
        left = DEADLINE - time.monotonic()
        if left <= 0:
            raise Unavailable("live diff timed out")
        if proc.wait(timeout=left) not in codes:
            raise Unavailable("live diff command failed")
        return bytes(out)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def git_path(kind):
    raw = command(["rev-parse", "--path-format=absolute", "--git-path", kind])
    if not raw.endswith(b"\n") or b"\0" in raw:
        raise Unavailable("cannot locate live Git metadata")
    return os.path.abspath(os.fsdecode(raw[:-1]))


def records(raw):
    if raw and not raw.endswith(b"\0"):
        raise Unavailable("incomplete live file listing")
    return raw.split(b"\0")[:-1]


def regular_file(root_fd, path):
    # Walk every component without following links. O_NONBLOCK prevents a
    # substituted FIFO/device from stalling before its file type is checked.
    if path.endswith(b"/"):
        return None  # Git reports an untracked nested repository as a directory.
    parts = path.split(b"/")
    if not parts or any(p in (b"", b".", b"..") for p in parts):
        raise Unavailable("unsafe live file path")
    parent = os.dup(root_fd)
    fd = None
    try:
        for component in parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=parent)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None
        if info.st_size > CAP:
            raise TooLarge("new live file exceeds preview limit")
        data = bytearray()
        while True:
            if time.monotonic() >= DEADLINE:
                raise Unavailable("live diff timed out")
            chunk = os.read(fd, min(65536, CAP + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > CAP:
                raise TooLarge("new live file exceeds preview limit")
        return bytes(data), info.st_mode
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
            return None
        raise
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def capture(base):
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", base):
        raise Unavailable("invalid live diff base")
    worktree = os.getcwd()
    objects = git_path("objects")
    worker_index = git_path("index")
    with tempfile.TemporaryDirectory(prefix="dirtywork-live-diff-", dir="/tmp") as tmp:
        gitdir = os.path.join(tmp, "git")
        os.mkdir(gitdir)
        os.mkdir(os.path.join(gitdir, "objects"))
        os.mkdir(os.path.join(gitdir, "refs"))
        with open(os.path.join(gitdir, "HEAD"), "w") as stream:
            stream.write("ref: refs/heads/live-diff\n")
        with open(os.path.join(gitdir, "config"), "w") as stream:
            stream.write("[core]\nrepositoryFormatVersion = " +
                         ("1" if len(base) == 64 else "0") + "\nbare = false\n")
            if len(base) == 64:
                stream.write("[extensions]\nobjectFormat = sha256\n")
        env = dict(ENV, GIT_DIR=gitdir, GIT_WORK_TREE=worktree,
                   GIT_OBJECT_DIRECTORY=objects,
                   GIT_INDEX_FILE=os.path.join(gitdir, "index"))
        # Only the disposable index is written. No refresh/add touches the
        # worker index, and its executable filters/fsmonitor/hooks are absent.
        command(["read-tree", base], env=env)
        patch = bytearray(command(["diff"] + DIFF + [base, "--"], env=env))
        new_paths = set(records(command(["ls-files", "--others", "--exclude-standard", "-z"], env=env)))
        # An added, already staged/committed path can now match .gitignore.
        # Read its name from the worker index using our clean configuration.
        if os.path.isfile(worker_index):
            index_env = dict(env, GIT_INDEX_FILE=worker_index)
            new_paths.update(records(command(["diff", "--cached", "--name-only", "--diff-filter=A", "-z"] +
                                             DIFF + [base, "--"], env=index_env)))
        if len(new_paths) > MAX_NEW:
            raise TooLarge("too many new live files")
        additions = os.path.join(tmp, "new")
        os.mkdir(additions)
        root_fd = os.open(worktree, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for path in sorted(new_paths):
                content = regular_file(root_fd, path)
                if content is None:
                    continue
                data, mode = content
                dest = os.path.join(os.fsencode(additions), path)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as stream:
                    stream.write(data)
                os.chmod(dest, 0o755 if mode & 0o111 else 0o644)
                patch.extend(command(["diff", "--no-index"] + DIFF + ["--", "/dev/null", os.fsdecode(path)],
                                     env=env, cwd=additions, cap=CAP-len(patch), codes=(0, 1)))
        finally:
            os.close(root_fd)
        return patch

try:
    result = capture(sys.argv[1])
except TooLarge as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(3)
except (Unavailable, OSError, ValueError, subprocess.SubprocessError):
    print("live diff unavailable", file=sys.stderr)
    sys.exit(2)
else:
    sys.stdout.buffer.write(result)
'''


def _validate_base(base_commit: str) -> None:
    if not isinstance(base_commit, str) or not re.fullmatch(
        r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", base_commit
    ):
        raise LiveDiffUnavailable("invalid live diff base commit")


def _patch(captured: Captured) -> bytes:
    if captured.truncated or len(captured.output) > MAX_PATCH_BYTES or captured.returncode == _TOO_LARGE:
        raise LiveDiffTooLarge("live diff exceeds preview limits")
    if captured.timed_out or captured.returncode != 0:
        raise LiveDiffUnavailable("live diff unavailable")
    return captured.output


def capture_docker_patch(container: str, base_commit: str, *, run=docker_cli.run) -> bytes:
    """Capture a worker preview between tools, with a remote process deadline.

    The inner timeout kills the exec process group even if the Docker client
    disconnects; the outer deadline bounds a stalled daemon/client too.
    """
    _validate_base(base_commit)
    argv = docker_args.exec_argv(
        container,
        ["/usr/bin/timeout", "--signal=KILL", _INNER_TIMEOUT,
         "/usr/bin/python3", "-I", "-c", _CAPTURE_SCRIPT, base_commit],
        env={"PATH": docker_args.PATH_ENV},
    )
    try:
        captured = run(argv, timeout=CAPTURE_TIMEOUT, cap=MAX_PATCH_BYTES + 1)
    except docker_cli.DockerError as exc:
        raise LiveDiffUnavailable("live diff unavailable") from exc
    return _patch(captured)


def capture_host_patch(worktree: Path, base_commit: str) -> bytes:
    """Capture the same bounded preview from a host-backed worker worktree."""
    _validate_base(base_commit)
    return _patch(run_capped(
        [sys.executable, "-I", "-c", _CAPTURE_SCRIPT, base_commit],
        cwd=worktree, timeout=CAPTURE_TIMEOUT, cap=MAX_PATCH_BYTES + 1,
    ))
