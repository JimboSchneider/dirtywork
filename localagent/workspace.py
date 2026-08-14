from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path


class WorkspaceError(Exception):
    """Raised when the target repo or worktree operation is unusable."""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


def preflight_repo(repo: Path) -> None:
    if not repo.is_dir():
        raise WorkspaceError(f"{repo} is not a directory")
    if _git(repo, "rev-parse", "--is-inside-work-tree").returncode != 0:
        raise WorkspaceError(f"{repo} is not a git repository")
    if _git(repo, "rev-parse", "HEAD").returncode != 0:
        raise WorkspaceError(f"{repo} has no commits (worktrees need a base ref)")


def make_slug(task: str, now: datetime) -> str:
    words = re.sub(r"[^a-z0-9\s-]", "", task.lower()).split()[:5]
    base = re.sub(r"-+", "-", "-".join(words))[:40].strip("-") or "task"
    return f"{base}-{now.strftime('%m%d%H%M')}"


def create_worktree(repo: Path, slug: str, branch_from: str | None) -> Path:
    rel = Path(".worktrees") / f"la-{slug}"
    ref = branch_from or "HEAD"
    res = _git(repo, "worktree", "add", "-b", f"localagent/{slug}", str(rel), ref)
    if res.returncode != 0:
        _git(repo, "branch", "-D", f"localagent/{slug}")  # best-effort cleanup; ignore result
        raise WorkspaceError(f"git worktree add failed: {res.stderr.strip()}")
    return repo / rel


def ensure_worktrees_excluded(repo: Path) -> None:
    res = _git(repo, "rev-parse", "--git-dir")
    if res.returncode != 0:
        raise WorkspaceError(f"cannot locate git dir for {repo}")
    git_dir = Path(res.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    exclude = git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.exists() else ""
    if ".worktrees/" not in existing:
        with open(exclude, "a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(".worktrees/\n")


def load_repo_context(repo: Path) -> str | None:
    for name in ("CLAUDE.md", "AGENTS.md"):
        p = repo / name
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    return None
