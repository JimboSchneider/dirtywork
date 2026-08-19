from __future__ import annotations

import os
import re
import secrets
import stat
import subprocess
from datetime import datetime
from pathlib import Path

MAX_CONTEXT_CHARS = 32_000
# Separate from tools.MAX_READ_BYTES (also 5 MB) even though the value is the
# same today — this bounds a git blob size, not a filesystem read.
MAX_CONTEXT_BYTES = 5 * 1024 * 1024
# Spec §2: the end-of-run file list, capped with a companion truncation flag.
MAX_FILES_CHANGED = 1000
# The ONE config-neutral git invocation shape for every host git command that
# looks at worker content (spec §2, §6.1, §6.2). No global/system config, no
# hooks, no fsmonitor, no commit signing: nothing the operator has configured
# can execute or interfere when dirtywork reads or commits what a worker wrote.
GIT_NEUTRAL_FLAGS = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                     "-c", "commit.gpgsign=false")


def git_env() -> dict:
    """os.environ plus the config-neutral overrides. A fresh dict per call, so
    a caller can add GIT_INDEX_FILE / GIT_AUTHOR_* without touching the next."""
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


class WorkspaceError(Exception):
    """Raised when the target repo or worktree operation is unusable."""


def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env
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
        salt = secrets.token_hex(4)
    return f"{base}-{now.strftime('%m%d%H%M%S')}-{salt}"


def create_worktree(repo: Path, slug: str, branch_from: str | None, *,
                     no_checkout: bool = False) -> Path:
    worktrees_dir = repo / ".worktrees"
    try:
        wd_st = os.lstat(worktrees_dir)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISDIR(wd_st.st_mode):
            raise WorkspaceError(
                f"{worktrees_dir} exists and is not a directory — refusing to "
                f"create a worktree through a symlink or other non-directory here"
            )

    rel = Path(".worktrees") / f"dw-{slug}"
    dest = repo / rel
    try:
        os.lstat(dest)
    except FileNotFoundError:
        pass
    else:
        # A pre-existing file, directory, or symlink at the EXACT destination
        # must abort before `git worktree add` runs: git would create through
        # a symlink, and a later `worktree remove` would then clean an
        # unrelated outside directory.
        raise WorkspaceError(
            f"{dest} already exists; refusing to create a worktree through a "
            f"pre-existing file, directory, or symlink at the exact destination"
        )

    ref = branch_from or "HEAD"
    branch = f"dirtywork/{slug}"
    existed = _git(repo, "rev-parse", "--verify", "--quiet",
                    f"refs/heads/{branch}").returncode == 0
    args = ["worktree", "add"]
    if no_checkout:
        args.append("--no-checkout")
    args += ["-b", branch, str(rel), ref]
    res = _git(repo, *args)
    if res.returncode != 0:
        if not existed:
            _git(repo, "branch", "-D", branch)  # best-effort cleanup; ignore result
        raise WorkspaceError(f"git worktree add failed: {res.stderr.strip()}")

    worktree = repo / rel
    # Never `.resolve()` the joined path and compare — that variant passes
    # wrongly when a component is a symlink. Resolve each side separately.
    expected_parent = repo.resolve() / ".worktrees"
    if expected_parent not in worktree.resolve().parents:
        remove_worktree(repo, slug)
        raise WorkspaceError(
            f"worktree resolved to {worktree.resolve()}, outside the expected "
            f"{expected_parent} — refusing (a symlinked .worktrees or ref "
            f"could redirect git worktree add outside the repo)"
        )
    return worktree


def remove_worktree(repo: Path, slug: str) -> None:
    """Best-effort removal of the worktree and branch that `create_worktree`
    created for `slug`. Used to roll back when a later preflight step fails
    so a `return 2` doesn't leave an orphaned .worktrees/dw-<slug> + branch.
    Never raises; git errors are ignored."""
    _git(repo, "worktree", "remove", "--force", str(Path(".worktrees") / f"dw-{slug}"))
    _git(repo, "branch", "-D", f"dirtywork/{slug}")


def ensure_worktrees_excluded(repo: Path) -> None:
    # Use --git-path (not --git-dir) so this resolves to the shared repository's
    # info/exclude even when `repo` is itself a linked worktree — a linked
    # worktree's --git-dir is its private .git/worktrees/<name> dir, but git only
    # ever consults the common/shared info/exclude for status/ignore purposes.
    common_res = _git(repo, "rev-parse", "--git-common-dir")
    if common_res.returncode != 0:
        raise WorkspaceError(f"cannot locate git common dir for {repo}")
    common = Path(common_res.stdout.strip())
    if not common.is_absolute():
        common = repo / common
    common = common.resolve()

    exclude_res = _git(repo, "rev-parse", "--git-path", "info/exclude")
    if exclude_res.returncode != 0:
        raise WorkspaceError(f"cannot locate git dir for {repo}")
    exclude = Path(exclude_res.stdout.strip())
    if not exclude.is_absolute():
        exclude = repo / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)

    # A replaced info/exclude (symlink to a file outside the repo, planted by a
    # hostile committed tree or a prior compromised run) must not redirect this
    # write outside the git dir. Require the resolved path inside the resolved
    # common dir before opening it at all.
    resolved_exclude = exclude.resolve()
    if resolved_exclude.parent != common and common not in resolved_exclude.parents:
        raise WorkspaceError(
            f"info/exclude resolved to {resolved_exclude}, outside the git "
            f"common dir {common} — refusing to write"
        )

    try:
        read_fd = os.open(str(exclude), os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        existing = ""
    except OSError as e:
        raise WorkspaceError(f"cannot read {exclude}: {e}")
    else:
        with os.fdopen(read_fd, "r", encoding="utf-8") as fh:
            existing = fh.read()

    if ".worktrees/" in existing:
        return

    try:
        write_fd = os.open(
            str(exclude), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o644
        )
    except OSError as e:
        raise WorkspaceError(f"cannot open {exclude} for writing: {e}")
    with os.fdopen(write_fd, "a", encoding="utf-8") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write(".worktrees/\n")


def worktree_base_commit(worktree: Path) -> str:
    res = _git(worktree, "rev-parse", "HEAD")
    if res.returncode != 0:
        raise WorkspaceError(f"cannot resolve HEAD in {worktree}: {res.stderr.strip()}")
    return res.stdout.strip()


def host_diff_stat(worktree: Path, base_commit: str, cap: int = 64_000) -> str:
    """`git diff --stat` of the worktree's working tree against base_commit,
    capped. Host mode only — comparing against base_commit (not the index)
    means unstaged, staged, AND committed changes to TRACKED files all show
    up, since the model is allowed to `git add`/`git commit` in the worktree.
    A brand-new file the model wrote but never `git add`ed is still invisible
    here (documented in README.md)."""
    res = _git(worktree, "diff", "--stat", base_commit)
    if res.returncode != 0:
        return f"[diff --stat failed: {res.stderr.strip()[:500]}]"
    out = res.stdout
    if len(out) > cap:
        out = out[:cap] + f"\n[truncated at {cap} chars]"
    return out


def host_untracked(worktree: Path, cap: int = 64_000) -> str:
    """Untracked paths in the worktree, capped. Complements host_diff_stat,
    which only sees TRACKED files — a new file the model wrote and never
    `git add`ed is invisible to `git diff --stat` but shows up here. Uses
    `git status --porcelain` in its DEFAULT untracked mode, deliberately NOT
    `-uall`: default mode collapses a whole untracked directory into one
    `?? dir/` line, so a model that ran `npm ci`/`volta install` under the
    worktree yields `.npm/`, `.volta/` rather than thousands of paths.
    `--untracked-files=normal` is passed explicitly so a user-level
    `status.showUntrackedFiles=no`/`all` cannot hide or explode the list.
    Gitignored paths are excluded automatically. Read-only — this never runs
    `git add` on the model's behalf, it only reports what's already there."""
    res = _git(worktree, "status", "--porcelain", "--untracked-files=normal")
    if res.returncode != 0:
        return f"[status failed: {res.stderr.strip()[:500]}]"
    lines = [
        line[len("?? "):] for line in res.stdout.splitlines() if line.startswith("?? ")
    ]
    out = "\n".join(lines)
    if len(out) > cap:
        out = out[:cap] + f"\n[truncated at {cap} chars]"
    return out


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


def host_read_tree(worktree: Path) -> None:
    """Index-only, against the base tree, using the operator's own object store
    — writes no working-tree files (verified). Config-neutral env (git_env +
    GIT_NEUTRAL_FLAGS) so no checked-out state, hook or filter can influence
    it, even though only objects/ was ever mounted into any container."""
    res = _git(worktree, *GIT_NEUTRAL_FLAGS, "read-tree", "HEAD", env=git_env())
    if res.returncode != 0:
        raise WorkspaceError(f"git read-tree HEAD failed in {worktree}: {res.stderr.strip()}")


def host_files_changed(worktree: Path, base_commit: str, cap: int = MAX_FILES_CHANGED) -> tuple:
    """(paths, truncated) — repo-relative paths that differ from base_commit
    plus every untracked, non-ignored path, sorted and de-duplicated, capped at
    `cap`. Host mode's half of spec §2's `files_changed`; the docker export
    computes the same list inside the container. A git failure on either half
    contributes nothing rather than aborting: this is evidence, not a gate."""
    env = git_env()
    paths = set()
    for args in (("diff", "--name-only", base_commit),
                 ("ls-files", "--others", "--exclude-standard")):
        res = _git(worktree, *GIT_NEUTRAL_FLAGS, *args, env=env)
        if res.returncode != 0:
            continue
        for line in res.stdout.splitlines():
            line = line.strip()
            if line:
                paths.add(line)
    ordered = sorted(paths)
    return ordered[:cap], len(ordered) > cap


def commit_exists(repo: Path, sha: str) -> bool:
    """True when `sha` names a commit reachable in the operator's repo."""
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0
