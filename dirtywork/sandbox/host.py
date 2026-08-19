from __future__ import annotations

from pathlib import Path

from .. import tools
from ..budget import (
    DEFAULT_MAX_WORKTREE_FILES,
    DEFAULT_MAX_WORKTREE_MB,
    BudgetExceeded,
    measure_worktree,
)
from ..workspace import host_diff_stat, host_untracked
from . import RunArtifacts, SandboxError


class HostSandbox:
    """Wraps today's tools.py functions unchanged (plus SP1 hardening). The
    worktree-budget check that used to live in ToolExecutor now lives here:
    every mutating call re-measures the worktree afterward and raises
    BudgetExceeded on violation, exactly as the pre-SP2 ToolExecutor did."""

    def __init__(self, worktree: Path, *, max_worktree_mb: int = DEFAULT_MAX_WORKTREE_MB,
                 max_worktree_files: int = DEFAULT_MAX_WORKTREE_FILES):
        self.worktree = worktree
        self.max_worktree_mb = max_worktree_mb
        self.max_worktree_files = max_worktree_files
        self.base_commit: str | None = None

    def start(self, worktree: Path, repo: Path, slug: str, base_commit: str, *, branch: str | None = None, seed_from_worktree: bool = False) -> None:
        self.worktree = worktree  # host mode: no container to create
        self.base_commit = base_commit
        self.repo = repo
        self.slug = slug

    def _measure(self) -> dict:
        return measure_worktree(self.worktree, max_bytes=self.max_worktree_mb * 1024 * 1024,
                                   max_files=self.max_worktree_files)

    def _check_budget(self) -> None:
        report = self._measure()
        if report.violation:
            raise BudgetExceeded(report.violation)

    def read_file(self, path: str, offset: int = 0, limit: int = 400) -> str:
        return tools.read_file(self.worktree, path, offset, limit)

    def write_file(self, path: str, content: str) -> str:
        result = tools.write_file(self.worktree, path, content)
        self._check_budget()
        return result

    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        result = tools.edit_file(self.worktree, path, old_string, new_string)
        self._check_budget()
        return result

    def insert_before(self, path: str, anchor: str, text: str) -> str:
        result = tools.insert_before(self.worktree, path, anchor, text)
        self._check_budget()
        return result

    def insert_after(self, path: str, anchor: str, text: str) -> str:
        result = tools.insert_after(self.worktree, path, anchor, text)
        self._check_budget()
        return result

    def list_dir(self, path: str = ".") -> str:
        return tools.list_dir(self.worktree, path)

    def grep(self, pattern: str, path: str = ".", glob: str | None = None,
             timeout: int = 30) -> str:
        return tools.grep(self.worktree, pattern, path, glob, timeout)

    def bash(self, command: str, timeout: int = 120) -> str:
        result = tools.bash(self.worktree, command, timeout)
        self._check_budget()
        return result

    def finalize(self) -> RunArtifacts:
        if self.base_commit is None:
            raise SandboxError("finalize() called before start()")
        report = self._measure()
        return RunArtifacts(
            diff_stat=host_diff_stat(self.worktree, self.base_commit),
            untracked=host_untracked(self.worktree),
            worktree_bytes=report.bytes,
            worktree_files=report.files,
            escaping_symlinks=list(report.escaping_symlinks),
            export_status="n/a",
        )

    def stop(self) -> None:
        pass  # no container/volume to tear down in host mode
