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
    bash call returned -- None otherwise, and always None in host mode."""
    diff_stat: str = ""
    untracked: str = ""
    patch_path: str | None = None
    worktree_bytes: int | None = None
    worktree_files: int | None = None
    escaping_symlinks: list = field(default_factory=list)
    dropped_git_entries: list = field(default_factory=list)
    export_status: str = "ok"
    watchdog_violation: str | None = None


class Sandbox(Protocol):
    """Every tool call and the run's start/finalize/stop lifecycle go through
    exactly this surface. HostSandbox (dirtywork.sandbox.host) and
    DockerSandbox (dirtywork.sandbox.docker) both implement it; ToolExecutor
    never knows which one it holds.

    Tool methods (read_file/write_file/edit_file/list_dir/grep/bash) may raise BudgetExceeded (worktree over budget) or SandboxError (backend failure); the runner catches both."""

    def start(self, worktree: Path, repo: Path, slug: str, base_commit: str) -> None: ...

    def read_file(self, path: str, offset: int = 0, limit: int = 400) -> str: ...

    def write_file(self, path: str, content: str) -> str: ...

    def edit_file(self, path: str, old_string: str, new_string: str) -> str: ...

    def list_dir(self, path: str = ".") -> str: ...

    def grep(self, pattern: str, path: str = ".", glob: str | None = None,
             timeout: int = 30) -> str: ...

    def bash(self, command: str, timeout: int = 120) -> str: ...

    def finalize(self) -> RunArtifacts: ...

    def stop(self) -> None: ...
