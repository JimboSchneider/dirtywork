from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class SandboxError(Exception):
    """Raised when a sandbox backend cannot complete an operation the runner
    depends on (container lifecycle failure, docker CLI timeout/expiry, export
    failure that must abort the run). Caught by Runner and turned into status
    'sandbox_error'."""


@dataclass
class RunArtifacts:
    """What a Sandbox reports at the end of a run. export_status is one of
    "ok", f"export_failed: {reason}", or "n/a" (host mode never exports).
    watchdog_violation is docker-mode only: the reason string the Watchdog
    recorded (and killed the container for) if that happened after the last
    bash call returned -- None otherwise, and always None in host mode.
    watchdog_violation_kind (D1) is Watchdog.violation_kind at the moment
    watchdog_violation was captured -- "sandbox_error" or "budget" -- and is
    only meaningful (non-None) when watchdog_violation itself is set;
    _final_status uses it to report "sandbox_error" instead of the default
    "budget_exceeded" for a watchdog-thread sample failure."""
    diff_stat: str = ""
    untracked: str = ""
    patch_path: str | None = None
    worktree_bytes: int | None = None
    worktree_files: int | None = None
    escaping_symlinks: list = field(default_factory=list)
    dropped_git_entries: list = field(default_factory=list)
    export_status: str = "ok"
    watchdog_violation: str | None = None
    watchdog_violation_kind: str | None = None
    # Spec §2: repo-relative paths the run changed, sorted and capped at
    # workspace.MAX_FILES_CHANGED. Docker mode computes it in the container
    # (no host git ever touches worker content); host mode computes it beside
    # diff_stat. Empty list when nothing changed or the export never ran.
    files_changed: list = field(default_factory=list)
    files_changed_truncated: bool = False


class Sandbox(Protocol):
    """Every tool call and the run's start/finalize/stop lifecycle go through
    exactly this surface. HostSandbox (dirtywork.sandbox.host) and
    DockerSandbox (dirtywork.sandbox.docker) both implement it; ToolExecutor
    never knows which one it holds.

    Tool methods (read_file/write_file/edit_file/apply_edits/insert_before/insert_after/list_dir/grep/bash) may raise BudgetExceeded (worktree over budget) or SandboxError (backend failure); the runner catches both."""

    def start(self, worktree: Path, repo: Path, slug: str, base_commit: str, *, branch: str | None = None, seed_from_worktree: bool = False) -> None: ...

    def read_file(self, path: str, offset: int = 0, limit: int = 400) -> str: ...

    def write_file(self, path: str, content: str) -> str: ...

    def edit_file(self, path: str, old_string: str, new_string: str) -> str: ...

    def apply_edits(self, path: str, edits: list) -> str: ...

    def insert_before(self, path: str, anchor: str, text: str) -> str: ...

    def insert_after(self, path: str, anchor: str, text: str) -> str: ...

    def list_dir(self, path: str = ".") -> str: ...

    def grep(self, pattern: str, path: str = ".", glob: str | None = None,
             timeout: int = 30) -> str: ...

    def bash(self, command: str, timeout: int = 120) -> str: ...

    def finalize(self) -> RunArtifacts: ...

    def stop(self) -> None: ...
