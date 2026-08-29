from __future__ import annotations

import json
import os
import re
import stat
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


def write_run_json(run_dir: Path, data: dict) -> None:
    """Atomic, 0600 write: a temp file in the same directory (same
    filesystem as the final path, so os.replace is atomic) then os.replace
    over run.json. A concurrent reader (e.g. `dirtywork runs list`) never
    sees a partially-written file."""
    tmp_path = run_dir / ".run.json.tmp"
    fd = open_nofollow(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
    except Exception:
        try:
            os.unlink(str(tmp_path))
        except OSError:
            pass
        raise
    os.replace(str(tmp_path), str(run_dir / "run.json"))


def read_run_json(run_dir: Path) -> dict:
    with open(run_dir / "run.json", "r", encoding="utf-8") as fh:
        return json.load(fh)
