from __future__ import annotations

import os
import stat
from pathlib import Path

DIRTYWORK_HOME = Path.home() / ".dirtywork"
RUNS_DIR = DIRTYWORK_HOME / "runs"


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


def ensure_runs_dir(runs_dir: Path = RUNS_DIR) -> Path:
    runs_dir = Path(runs_dir)
    _ensure_owned_dir(runs_dir.parent)
    _ensure_owned_dir(runs_dir)
    return runs_dir


def create_run_dir(runs_dir: Path, slug: str) -> Path:
    run_dir = Path(runs_dir) / slug
    try:
        os.mkdir(run_dir, mode=0o700)
    except FileExistsError:
        raise RunDirError(f"{run_dir} already exists — slug collision")
    except OSError as e:
        raise RunDirError(f"cannot create {run_dir}: {e}")
    return run_dir
