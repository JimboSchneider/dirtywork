from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .tools import is_temp_name

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
    # Spec §2.5: staging temps this walk removed. Defaulted and LAST, so every
    # existing four-positional construction stays valid; 0 unless the caller
    # asked for the sweep.
    swept: int = 0


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


def _measure_posix(worktree: Path, max_bytes: int, max_files: int,
                   sweep_temps: bool = False) -> BudgetReport:
    """`sweep_temps` (spec §2.5) removes a leftover `.dw-tmp.<name>.<8 hex>`
    staging file as this walk passes it, rather than counting it. Folded in
    here so the sweep costs NO second traversal: HostSandbox already walks the
    worktree at start and again at finalize. A swept entry is not counted
    toward `files`/`bytes` -- it is about to stop existing."""
    root = str(worktree)
    total_bytes = 0
    total_files = 0
    swept = 0
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
                if sweep_temps and stat.S_ISREG(st.st_mode) and is_temp_name(name):
                    try:
                        os.unlink(name, dir_fd=dirfd)
                    except OSError:
                        pass          # still there next walk; never fail a run for it
                    else:
                        swept += 1
                        continue
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
                        f"worktree exceeds {max_bytes // (1024 * 1024)} MB", swept,
                    )
                if total_files > max_files:
                    return BudgetReport(
                        total_bytes, total_files, escaping,
                        f"worktree exceeds {max_files} entries", swept,
                    )
    except _UnreadableDir as e:
        return BudgetReport(total_bytes, total_files, escaping,
                             f"unreadable directory: {e.path}", swept)

    return BudgetReport(total_bytes, total_files, escaping, None, swept)


def _measure_windows(worktree: Path, max_bytes: int, max_files: int,
                     sweep_temps: bool = False) -> BudgetReport:
    # `sweep_temps` is accepted and IGNORED here: Windows is unsupported (see
    # README's platform table), the sweep is a POSIX dir_fd unlink, and a
    # silently skipped sweep only means a leftover temp is reported as an
    # ordinary file rather than removed. Stated, not hidden.
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


def measure_worktree(worktree: Path, *, max_bytes: int, max_files: int,
                     sweep_temps: bool = False) -> BudgetReport:
    if os.name == "nt":
        return _measure_windows(worktree, max_bytes, max_files, sweep_temps)
    return _measure_posix(worktree, max_bytes, max_files, sweep_temps)
