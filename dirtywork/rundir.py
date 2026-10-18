from __future__ import annotations

import json
import os
import re
import stat
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

from .osfs import open_nofollow

DIRTYWORK_HOME = Path.home() / ".dirtywork"
RUNS_DIR = DIRTYWORK_HOME / "runs"
BENCH_HOME = DIRTYWORK_HOME / "bench"


class RunDirError(Exception):
    """Raised when ~/.dirtywork or a per-run directory cannot be trusted."""


def _ensure_owned_dir(path: Path) -> None:
    try:
        os.mkdir(path, mode=0o700)
    except FileExistsError:
        pass
    except OSError as e:
        raise RunDirError(f"cannot create {path}: {e}")
    try:
        st = os.lstat(path)
    except OSError as e:
        raise RunDirError(f"cannot stat {path}: {e}")
    if not stat.S_ISDIR(st.st_mode):
        raise RunDirError(f"{path} exists and is not a directory")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise RunDirError(f"{path} is not owned by the current user")
    # A pre-existing ~/.dirtywork (or runs/) with group/other-readable perms
    # would leak run slugs/task names via directory listing -- run dirs and
    # transcripts are already created 0700/0600, so tighten the container too.
    if hasattr(os, "getuid") and stat.S_IMODE(st.st_mode) & 0o077:
        try:
            os.chmod(path, 0o700)
        except OSError as e:
            raise RunDirError(f"cannot tighten permissions on {path}: {e}")


def ensure_runs_dir(runs_dir: Path = RUNS_DIR) -> Path:
    runs_dir = Path(runs_dir)
    _ensure_owned_dir(runs_dir.parent)
    _ensure_owned_dir(runs_dir)
    return runs_dir


def ensure_bench_dir(bench_home: Path = BENCH_HOME) -> Path:
    """Same ownership/permission guarantees as ensure_runs_dir, for
    ~/.dirtywork/bench (dirtywork bench's own results directory)."""
    bench_home = Path(bench_home)
    _ensure_owned_dir(bench_home.parent)
    _ensure_owned_dir(bench_home)
    return bench_home


def create_run_dir(runs_dir: Path, slug: str) -> Path:
    run_dir = Path(runs_dir) / slug
    try:
        os.mkdir(run_dir, mode=0o700)
    except FileExistsError:
        raise RunDirError(f"{run_dir} already exists — slug collision")
    except OSError as e:
        raise RunDirError(f"cannot create {run_dir}: {e}")
    return run_dir


# Spec §4.1. A slug arrives from the command line (`runs show <slug>`,
# `--branch-from @<slug>`) or from a results file; it must never be able to
# name a path outside `runs_dir`.
_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def run_dir_for(slug: str, runs_dir: Path) -> Path:
    """`<runs_dir>/<slug>` for a plain slug ONLY. Raises RunDirError.

    The ONE rule, in one place: `dirtywork runs …` reaches it through
    `runs._run_dir_for` (re-raised as RunsError) and `--branch-from @<slug>`
    through `__main__._resolve_branch_from` (re-raised as PreflightFailure).
    Before 0.10 the second route went through the path-permissive
    `resume.resolve_run_dir`, so `@../x` and `@/etc` were accepted by one
    entry point and refused by the other. This does NOT require the directory
    to exist -- a syntactically valid slug that names no run gets each
    caller's own "unknown run" message."""
    if not _SLUG_RE.fullmatch(slug) or slug in (".", ".."):
        raise RunDirError(f"invalid run slug '{slug}'")
    runs_dir = Path(runs_dir)
    run_dir = runs_dir / slug
    try:
        if run_dir.resolve().parent != runs_dir.resolve():
            raise RunDirError(f"invalid run slug '{slug}'")
    except OSError as e:
        raise RunDirError(f"cannot resolve run '{slug}': {e}")
    return run_dir


class RunJsonConflict(OSError):
    """The run changed after the caller read it."""


def run_json_signature(data: dict) -> str:
    """A content signature independent of JSON indentation and key order."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


@contextmanager
def _run_json_lock(run_dir: Path):
    # Keep the inode in place: unlinking a lock lets a second writer lock a
    # different inode. OS locks are released even if the process is killed.
    lock = run_dir / ".run.json.lock"
    fd = open_nofollow(lock, os.O_RDWR | os.O_CREAT, 0o600, cloexec=True)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError(f"unsafe run metadata lock: {lock}")
        if os.name == "nt":
            import msvcrt
            def acquire():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            def release():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(fd, fcntl.LOCK_UN)
        deadline = time.monotonic() + 5
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise OSError(f"run metadata is busy: {lock}")
                time.sleep(0.01)
        try:
            yield
        finally:
            release()
    finally:
        os.close(fd)


def write_run_json(run_dir: Path, data: dict, *, expected_signature=None) -> None:
    """Atomically replace metadata using an exclusive, private temporary file.

    All writers serialize the compare-and-replace, so an expected signature
    rejects stale read/modify/write operations without losing newer fields.
    """
    run_dir = Path(run_dir)
    with _run_json_lock(run_dir):
        if expected_signature is not None:
            if run_json_signature(read_run_json(run_dir)) != expected_signature:
                raise RunJsonConflict("run metadata changed; retry the operation")
        for _ in range(100):
            tmp_path = run_dir / f".run.json.{secrets.token_hex(16)}.tmp"
            try:
                fd = open_nofollow(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, cloexec=True)
                break
            except FileExistsError:
                continue
        else:
            raise OSError("cannot allocate a private run metadata temporary file")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(str(tmp_path), str(run_dir / "run.json"))
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


def update_run_json(run_dir: Path, transform) -> dict:
    """Apply a pure merge operation to fresh metadata, retrying conflicts."""
    for _ in range(20):
        current = read_run_json(run_dir)
        signature = run_json_signature(current)
        updated = transform(current)
        try:
            write_run_json(run_dir, updated, expected_signature=signature)
            return updated
        except RunJsonConflict:
            continue
    raise RunJsonConflict("run metadata kept changing; retry the operation")


def read_run_json(run_dir: Path) -> dict:
    with open(run_dir / "run.json", "r", encoding="utf-8") as fh:
        return json.load(fh)
