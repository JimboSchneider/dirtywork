from __future__ import annotations

import re
import secrets
import subprocess
from datetime import datetime
from pathlib import Path

MAX_CONTEXT_CHARS = 32_000
# Separate from tools.MAX_READ_BYTES (also 5 MB) even though the value is the
# same today — this bounds a git blob size, not a filesystem read.
MAX_CONTEXT_BYTES = 5 * 1024 * 1024


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


def make_slug(task: str, now: datetime, salt: str | None = None) -> str:
    words = re.sub(r"[^a-z0-9\s-]", "", task.lower()).split()[:5]
    base = re.sub(r"-+", "-", "-".join(words))[:40].strip("-") or "task"
    if salt is None:
        salt = secrets.token_hex(2)
    return f"{base}-{now.strftime('%m%d%H%M%S')}-{salt}"


def create_worktree(repo: Path, slug: str, branch_from: str | None) -> Path:
    rel = Path(".worktrees") / f"dw-{slug}"
    ref = branch_from or "HEAD"
    branch = f"dirtywork/{slug}"
    existed = _git(repo, "rev-parse", "--verify", "--quiet",
                    f"refs/heads/{branch}").returncode == 0
    res = _git(repo, "worktree", "add", "-b", branch, str(rel), ref)
    if res.returncode != 0:
        if not existed:
            _git(repo, "branch", "-D", branch)  # best-effort cleanup; ignore result
        raise WorkspaceError(f"git worktree add failed: {res.stderr.strip()}")
    return repo / rel


def ensure_worktrees_excluded(repo: Path) -> None:
    # Use --git-path (not --git-dir) so this resolves to the shared repository's
    # info/exclude even when `repo` is itself a linked worktree — a linked
    # worktree's --git-dir is its private .git/worktrees/<name> dir, but git only
    # ever consults the common/shared info/exclude for status/ignore purposes.
    res = _git(repo, "rev-parse", "--git-path", "info/exclude")
    if res.returncode != 0:
        raise WorkspaceError(f"cannot locate git dir for {repo}")
    exclude = Path(res.stdout.strip())
    if not exclude.is_absolute():
        exclude = repo / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.exists() else ""
    if ".worktrees/" not in existing:
        with open(exclude, "a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(".worktrees/\n")


def worktree_base_commit(worktree: Path) -> str:
    res = _git(worktree, "rev-parse", "HEAD")
    if res.returncode != 0:
        raise WorkspaceError(f"cannot resolve HEAD in {worktree}: {res.stderr.strip()}")
    return res.stdout.strip()


def load_repo_context(repo: Path, base_commit: str) -> str | None:
    """Read CLAUDE.md/AGENTS.md from the base commit's git object store, not
    the filesystem. This closes two problems with a filesystem read: a
    symlinked CLAUDE.md pointing outside the repo (Path.is_file() follows
    links) and an unbounded read of whatever happens to be on disk right now
    (which could be dirty/uncommitted content unrelated to the commit the
    worktree was branched from). `cat-file -p` on a blob never runs a smudge
    filter, so this is also immune to a hostile .gitattributes.
    """
    res = _git(repo, "ls-tree", base_commit, "--", "CLAUDE.md", "AGENTS.md")
    if res.returncode != 0:
        return None
    entries = {}
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        meta, _, name = line.partition("\t")
        parts = meta.split()
        if len(parts) != 3:
            continue
        mode, obj_type, sha = parts
        # Only plain blobs at mode 100644 (file) or 100755 (executable) are
        # eligible. Symlink entries (mode 120000) and gitlinks/submodules
        # (mode 160000) are ignored — a symlinked CLAUDE.md in the commit
        # itself must not be followed either.
        if obj_type != "blob" or mode not in ("100644", "100755"):
            continue
        entries[name] = sha
    for name in ("CLAUDE.md", "AGENTS.md"):
        sha = entries.get(name)
        if sha is None:
            continue
        size_res = _git(repo, "cat-file", "-s", sha)
        if size_res.returncode != 0:
            continue
        try:
            size = int(size_res.stdout.strip())
        except ValueError:
            continue
        if size > MAX_CONTEXT_BYTES:
            continue
        content_res = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-p", sha],
            capture_output=True,
        )
        if content_res.returncode != 0:
            continue
        text = content_res.stdout.decode("utf-8", errors="replace")
        if len(text) > MAX_CONTEXT_CHARS:
            text = text[:MAX_CONTEXT_CHARS] + "\n[truncated at 32000 chars]"
        return text
    return None
