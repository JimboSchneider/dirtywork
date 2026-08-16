from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_WORKTREE_MB = 2048
DEFAULT_MAX_WORKTREE_FILES = 200_000


class BudgetExceeded(Exception):
    """Raised when a worktree exceeds a configured disk/file-count budget."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class BudgetReport:
    bytes: int
    files: int
    escaping_symlinks: list
    violation: str | None


class _UnreadableDir(Exception):
    def __init__(self, path: str):
        self.path = path


def _is_escaping(dirpath: str, target: str, root: str) -> bool:
    """A symlink target counts as escaping if it is absolute (regardless of
    where it actually points — matching the SP2 export validator's rule) or,
    for a relative target, if normalizing it against the symlink's own
    directory lands outside `root`."""
    if os.path.isabs(target):
        return True
    candidate = os.path.normpath(os.path.join(dirpath, target))
    root_norm = os.path.normpath(root)
    return not (candidate == root_norm or candidate.startswith(root_norm + os.sep))


def _measure_posix(worktree: Path, max_bytes: int, max_files: int) -> BudgetReport:
    root = str(worktree)
    total_bytes = 0
    total_files = 0
    escaping: list = []

    def _onerror(err: OSError) -> None:
        raise _UnreadableDir(err.filename or str(err))

    try:
        for dirpath, dirnames, filenames, dirfd in os.fwalk(
            root, onerror=_onerror, follow_symlinks=False
        ):
            for name in dirnames + filenames:
                try:
                    st = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
                except OSError as e:
                    raise _UnreadableDir(e.filename or str(e))
                total_files += 1
                total_bytes += (
                    st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
                )
                if stat.S_ISLNK(st.st_mode):
                    target = os.readlink(name, dir_fd=dirfd)
                    if _is_escaping(dirpath, target, root):
                        rel = os.path.relpath(os.path.join(dirpath, name), root)
                        escaping.append(rel)
                if total_bytes > max_bytes:
                    return BudgetReport(
                        total_bytes, total_files, escaping,
                        f"worktree exceeds {max_bytes // (1024 * 1024)} MB",
                    )
                if total_files > max_files:
                    return BudgetReport(
                        total_bytes, total_files, escaping,
                        f"worktree exceeds {max_files} entries",
                    )
    except _UnreadableDir as e:
        return BudgetReport(total_bytes, total_files, escaping,
                             f"unreadable directory: {e.path}")

    return BudgetReport(total_bytes, total_files, escaping, None)


def _measure_windows(worktree: Path, max_bytes: int, max_files: int) -> BudgetReport:
    # Best-effort; not exercised by this (POSIX-developed) test suite. `\\?\`
    # -prefixed paths avoid MAX_PATH limits; FILE_ATTRIBUTE_REPARSE_POINT
    # entries (symlinks and junctions) are counted but not descended into,
    # matching follow_symlinks=False on POSIX. Escaping-target detection is
    # skipped — reading a Windows reparse point's target needs the reparse
    # API, which this stdlib-only runtime does not attempt here; the export
    # validator (SP2, POSIX today) is the enforced boundary either way.
    import ctypes

    FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    resolved_root = str(worktree.resolve())
    walk_root = resolved_root if resolved_root.startswith("\\\\?\\") else "\\\\?\\" + resolved_root
    total_bytes = 0
    total_files = 0
    escaping: list = []

    for dirpath, dirnames, filenames in os.walk(walk_root):
        reparse_dirs = []
        for name in dirnames:
            full = os.path.join(dirpath, name)
            attrs = ctypes.windll.kernel32.GetFileAttributesW(full)
            if attrs != -1 and attrs & FILE_ATTRIBUTE_REPARSE_POINT:
                reparse_dirs.append(name)
        for name in reparse_dirs:
            dirnames.remove(name)  # do not descend into reparse points
        for name in dirnames + filenames + reparse_dirs:
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError as e:
                return BudgetReport(
                    total_bytes, total_files, escaping,
                    f"unreadable directory: {e.filename or e}",
                )
            total_files += 1
            total_bytes += st.st_size
            if total_bytes > max_bytes:
                return BudgetReport(
                    total_bytes, total_files, escaping,
                    f"worktree exceeds {max_bytes // (1024 * 1024)} MB",
                )
            if total_files > max_files:
                return BudgetReport(
                    total_bytes, total_files, escaping,
                    f"worktree exceeds {max_files} entries",
                )
    return BudgetReport(total_bytes, total_files, escaping, None)


def measure_worktree(worktree: Path, *, max_bytes: int, max_files: int) -> BudgetReport:
    if os.name == "nt":
        return _measure_windows(worktree, max_bytes, max_files)
    return _measure_posix(worktree, max_bytes, max_files)
