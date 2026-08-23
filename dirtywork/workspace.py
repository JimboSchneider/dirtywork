from __future__ import annotations

import os
import re
import secrets
import stat
import subprocess
import tempfile
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


def _git(repo: Path, *args: str, env: dict | None = None,
         stdin_text: str | None = None,
         timeout: float | None = None) -> subprocess.CompletedProcess:
    """`stdin_text` is how the snapshot plumbing feeds path lists and index
    lines to git (hash-object --stdin-paths, update-index --index-info);
    None keeps today's behaviour of inheriting stdin. `timeout` is passed
    straight to subprocess.run (None: no timeout, today's behaviour); callers
    on a destructive path (e.g. host_worktree_dirty) pass one so a hung git
    process cannot hang them."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env,
        input=stdin_text, timeout=timeout
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
    here (documented in docs/machine-contract.md)."""
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


def host_worktree_dirty(worktree) -> bool:
    """True when `git status --porcelain` reports anything, or cannot be run at
    all (fail closed: an unanswerable worktree is treated as having work worth
    snapshotting). Config-neutral, like every host git command that looks at
    worker content — the operator's own filters must not run here. This is the
    ONE dirty check in the codebase: `runs._worktree_is_dirty` delegates here."""
    try:
        res = _git(Path(worktree), *GIT_NEUTRAL_FLAGS, "status", "--porcelain",
                   "--untracked-files=normal", env=git_env(), timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    return res.returncode != 0 or bool(res.stdout.strip())


def commit_exists(repo: Path, sha: str) -> bool:
    """True when `sha` names a commit reachable in the operator's repo."""
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


SNAPSHOT_AUTHOR = ("dirtywork", "dirtywork@localhost")


def _check_snapshot_path(worktree: Path, rel: str) -> None:
    """Refuse a repo-relative path git's stdin protocols cannot carry safely.

    Factored out of snapshot_worktree (spec §4.4) so the breadth-first walk can
    run it on every DIRECTORY path before that path reaches `check-ignore` --
    preserving this module's promise that an unsafe name raises WorkspaceError,
    never a UnicodeEncodeError from inside a text=True _git call."""
    if any(ord(c) < 32 for c in rel):
        raise WorkspaceError(
            f"cannot snapshot {worktree}: path {rel!r} contains a control character, "
            f"which git's stdin path protocols cannot carry safely"
        )
    try:
        rel.encode("utf-8")
    except UnicodeEncodeError:
        # An undecodable filename (surrogate-escaped by os.fsdecode) is
        # ord(c) >= 32 for every char, so the guard above lets it through;
        # _git's text=True calls would then raise UnicodeEncodeError themselves
        # (not a WorkspaceError) trying to encode it for git's stdin.
        raise WorkspaceError(
            f"cannot snapshot {worktree}: path {rel!r} is not valid UTF-8 "
            f"(undecodable filename), which git's stdin path protocols "
            f"cannot carry safely"
        )


def _walk_worktree(worktree: Path) -> tuple:
    """(files, links, skipped, unreadable_dirs) for everything under
    `worktree`, walked breadth-first ONE TREE DEPTH at a time (spec §4.4).

    `files` is [(repo-relative path, is_executable)], `links` is
    [(repo-relative path, link target string)], `skipped` counts entries that
    are neither a regular file nor a symlink (FIFOs, sockets, devices) or that
    failed `os.stat`/`os.readlink` outright.

    Why breadth-first: each level's candidate DIRECTORIES go through ONE
    batched `git check-ignore` call, and an ignored directory is dropped before
    it is descended into -- so an ignored `node_modules/` costs one path in one
    batch instead of a full traversal plus a per-file filter. The check is
    deliberately INDEX-AWARE (never `--no-index`): `check-ignore` does not
    report a directory as ignored while a TRACKED file lives inside it, which
    is exactly what keeps a tracked `build/keep.txt` in the snapshot when
    `build/` matches an ignore pattern. Every directory path passes
    `_check_snapshot_path` BEFORE it reaches `check-ignore`, so the module's
    WorkspaceError-not-UnicodeEncodeError promise holds for directories too.
    Files and symlinks are NOT filtered here -- snapshot_worktree still runs
    its own single batch over them, unchanged.

    A DIRECTORY that cannot be listed (`chmod 000` on the directory itself) is
    recorded in `unreadable_dirs` rather than raised on: snapshot_worktree runs
    it through the ignore check afterward and raises only for one that is NOT
    ignored. An ignored directory is never listed at all, so it can never be
    recorded. The TOP-LEVEL `.git` entry is skipped and nothing else is skipped
    by name. Symlinks -- including symlinked directories -- are recorded by
    their target string and never followed or descended into."""
    files, links, skipped, unreadable_dirs = [], [], 0, []
    level = [""]                       # "" is the worktree root itself
    while level:
        children = []
        for rel_dir in level:
            here = worktree / rel_dir if rel_dir else worktree
            try:
                with os.scandir(str(here)) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError as e:
                unreadable_dirs.append((rel_dir or ".", e))
                continue
            for entry in entries:
                if not rel_dir and entry.name == ".git":
                    continue
                rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    skipped += 1
                    continue
                if stat.S_ISLNK(st.st_mode):
                    try:
                        links.append((rel, os.readlink(entry.path)))
                    except OSError:
                        skipped += 1
                elif stat.S_ISDIR(st.st_mode):
                    children.append(rel)
                elif stat.S_ISREG(st.st_mode):
                    files.append((rel, bool(st.st_mode & 0o111)))
                else:
                    skipped += 1
        if not children:
            break
        for rel in children:
            _check_snapshot_path(worktree, rel)
        ignored = _ignored_relpaths(worktree, children)
        level = [rel for rel in children if rel not in ignored]
    files.sort()
    links.sort()
    return files, links, skipped, unreadable_dirs


def _ignored_relpaths(worktree: Path, rels: list) -> set:
    """Every path in `rels` (repo-relative, POSIX-separated) that the repo's
    ignore rules cover, per ONE batch `git check-ignore -z --stdin` run from
    `worktree` under the config-neutral env (`git_env()` + GIT_NEUTRAL_FLAGS)
    — the same rules `.gitignore`/`.git/info/exclude`/`core.excludesFile`
    that `git add -A` honors (global excludes are disabled by the env, same
    as everywhere else in this module). `check-ignore` is index-aware by
    default: a path that is already TRACKED is never reported here even if it
    matches a pattern, which is exactly what `git add -A` does too — call
    ONLY after the control-character/UTF-8 guard in `snapshot_worktree` has
    already validated every path, since `-z` gives the paths no quoting step
    to rely on. Exit code 0 means some paths matched (their names are on
    stdout, NUL-separated); exit code 1 means none did (empty stdout, not an
    error); anything else is a WorkspaceError."""
    if not rels:
        return set()
    stdin_text = "".join(rel + "\0" for rel in rels)
    res = _git(worktree, *GIT_NEUTRAL_FLAGS, "check-ignore", "-z", "--stdin",
               env=git_env(), stdin_text=stdin_text)
    if res.returncode not in (0, 1):
        raise WorkspaceError(f"git check-ignore failed in {worktree}: {res.stderr.strip()}")
    if not res.stdout:
        return set()
    return {p for p in res.stdout.split("\0") if p}


def _check(res: subprocess.CompletedProcess, what: str, worktree: Path) -> None:
    """Raise WorkspaceError for a failed plumbing call in `snapshot_worktree`;
    `what` names the git subcommand (plus any call-specific detail)."""
    if res.returncode != 0:
        raise WorkspaceError(f"git {what} failed in {worktree}: {res.stderr.strip()}")


def _hash_entries(worktree: Path, files: list, links: list, env: dict, *,
                  write: bool) -> list:
    """The `<mode> <sha>\\t<relpath>` index lines for `files` + `links`.

    `write` selects `-w` (write each blob into the object store) or not. Spec
    §4.3 calls this TWICE: once WITHOUT -w, only to decide whether anything
    changed at all (a no-op snapshot must not litter the object store with
    loose objects), and then -- only when something did -- once WITH -w,
    rebuilding the entries from the SECOND pass's shas. Rebuilding is
    load-bearing: a file that changed between the two passes must not leave the
    tree pointing at a blob that was never written. (Verified: `write-tree`
    fails on absent blobs, so the no-op decision cannot instead be made after a
    -w-less update-index.)

    `--no-filters` is load-bearing too: the filter that would otherwise run is
    configured REPO-locally, which GIT_CONFIG_GLOBAL=/dev/null does not
    disable. `worktree` is absolute, so every line handed to `--stdin-paths`
    starts with `/` and a worker-chosen filename can never be the FIRST
    character of that line -- which neutralises `--stdin-paths`' own C-quoting
    of a leading `"`."""
    hash_argv = ["hash-object"] + (["-w"] if write else []) + ["--no-filters"]
    entries = []
    if files:
        paths = "".join(str(worktree / rel) + "\n" for rel, _ in files)
        res = _git(worktree, *GIT_NEUTRAL_FLAGS, *hash_argv, "--stdin-paths",
                   env=env, stdin_text=paths)
        _check(res, "hash-object", worktree)
        shas = res.stdout.split()
        if len(shas) != len(files):
            raise WorkspaceError(
                f"git hash-object returned {len(shas)} hashes for {len(files)} files "
                f"in {worktree}")
        for (rel, is_exec), sha in zip(files, shas):
            entries.append(f"{'100755' if is_exec else '100644'} {sha}\t{rel}")
    for rel, target in links:
        res = _git(worktree, *GIT_NEUTRAL_FLAGS, *hash_argv, "--stdin",
                   env=env, stdin_text=target)
        _check(res, f"hash-object for symlink {rel}", worktree)
        entries.append(f"120000 {res.stdout.strip()}\t{rel}")
    return entries


def _head_entries(worktree: Path, head_tree: str, env: dict) -> list:
    """`head_tree`'s contents in the SAME normalized form _hash_entries
    produces, so the two can be compared directly (spec §4.3). `-r -z`: `-r`
    because _hash_entries lists leaves only, `-z` because a path with a
    newline or a quote in it must round-trip literally."""
    res = _git(worktree, *GIT_NEUTRAL_FLAGS, "ls-tree", "-r", "-z", head_tree, env=env)
    _check(res, f"ls-tree {head_tree}", worktree)
    out = []
    for record in res.stdout.split("\0"):
        if not record:
            continue
        meta, _tab, rel = record.partition("\t")
        mode, _kind, sha = meta.split(" ", 2)
        out.append(f"{mode} {sha}\t{rel}")
    return out


def snapshot_worktree(worktree: Path, branch: str, message: str,
                       report: dict | None = None) -> str | None:
    """Spec §6.1: commit the worktree's CURRENT content onto `branch` using
    nothing but git plumbing — no `git add`, no `git commit`, so no clean
    filter, no `.gitattributes` rule and no hook can ever execute on the host
    against content a worker wrote. Returns the new commit sha, or None when
    the resulting tree equals the branch head's tree (nothing to snapshot).
    When `report` is given, fills it with `{"skipped": N}` — the count of
    non-regular, non-symlink entries `_walk_worktree` skipped. Applies the
    repo's ignore rules exactly like `git add -A` would: every path
    `_walk_worktree` found is dropped from the snapshot when `git
    check-ignore` reports it ignored (a tracked file matching an ignore
    pattern is kept — check-ignore is index-aware, same as `git add -A`),
    which is what keeps this in agreement with `host_worktree_dirty`'s `git
    status`. Raises WorkspaceError.

    `hash-object -w --no-filters` is load-bearing: the filter that would
    otherwise run is configured REPO-locally, which GIT_CONFIG_GLOBAL=/dev/null
    does not disable (verified, git 2.48.1). `worktree` is resolved to an
    absolute path up front, so every line handed to `hash-object
    --stdin-paths` starts with `/` — a worker-chosen filename can never be the
    FIRST character of that line, which is what neutralises `--stdin-paths`'
    own C-quoting of a leading `"` (verified: a relative path starting with
    `"` is silently misread as a different, C-unescaped path; an absolute one
    is not). `update-index --index-info` cannot be neutralised the same way —
    the field it quotes is the tree-relative path itself, which a worker fully
    controls — so it runs as `-z`/NUL-delimited instead, a protocol with no
    quoting step at all (verified against git 2.48.1: an on-disk file literally
    named `"src\\057main.py"` round-trips as that literal name under `-z`, but
    is silently re-recorded as `src/main.py` without it). A relative path
    containing any control character (at minimum `\\n`, which
    `--stdin-paths`' line protocol treats as the path terminator, and `\\r`,
    which `--stdin-paths` strips from the end of a line before opening it) is
    refused outright rather than mis-hashed or mis-addressed. `worktree` must
    already have `branch` checked out (`symbolic-ref HEAD`, checked before
    anything is hashed) — a worktree on a different branch never gets its
    content committed onto `branch` out from under it."""
    worktree = Path(worktree).resolve()
    env = git_env()

    symref_res = _git(worktree, *GIT_NEUTRAL_FLAGS, "symbolic-ref", "-q", "HEAD", env=env)
    current = symref_res.stdout.strip() if symref_res.returncode == 0 else "(detached HEAD)"
    if current != f"refs/heads/{branch}":
        raise WorkspaceError(
            f"worktree {worktree} has {current} checked out, not refs/heads/{branch}; "
            f"refusing to commit its content onto a branch it is not on"
        )

    # Spec §4.2: the empty tree's id is content-addressed, so it depends on the
    # repo's OBJECT FORMAT -- the well-known 4b825dc… is the SHA-1 answer only.
    # Computed here, once per call, from the repo itself. `--stdin` with empty
    # stdin (never a /dev/null path) and no `-w`: this asks for an id, it does
    # not write an object.
    empty_tree_res = _git(worktree, *GIT_NEUTRAL_FLAGS, "hash-object", "-t", "tree",
                          "--stdin", env=env, stdin_text="")
    _check(empty_tree_res, "hash-object -t tree", worktree)
    empty_tree = empty_tree_res.stdout.strip()

    files, links, skipped, unreadable_dirs = _walk_worktree(worktree)
    if report is not None:
        report["skipped"] = skipped
    unreadable_rels = [rel for rel, _exc in unreadable_dirs]
    for rel in [r for r, _ in files] + [r for r, _ in links] + unreadable_rels:
        _check_snapshot_path(worktree, rel)

    # Spec §6.1 step 1 / fix item 1: apply the repo's ignore rules like
    # `git add -A` would, via ONE batch `git check-ignore` call, only now that
    # every path is known to be safe to hand to git's stdin protocols. The
    # unreadable directories `_walk_worktree` could not descend into ride
    # along in this SAME batch call (round 2 fix): a directory that turns out
    # to be ignored (e.g. root-owned content inside an ignored `build/` or
    # `.venv/`) must not hard-fail the snapshot, since nothing under it would
    # ever be committed anyway.
    ignored = _ignored_relpaths(
        worktree, [r for r, _ in files] + [r for r, _ in links] + unreadable_rels)
    if ignored:
        files = [(r, is_exec) for r, is_exec in files if r not in ignored]
        links = [(r, target) for r, target in links if r not in ignored]

    for rel, exc in unreadable_dirs:
        if rel not in ignored:
            raise WorkspaceError(
                f"cannot snapshot {worktree}: cannot read directory {exc.filename}: {exc}"
            )

    head_res = _git(worktree, *GIT_NEUTRAL_FLAGS, "rev-parse", "--verify", "--quiet",
                    f"refs/heads/{branch}", env=env)
    if head_res.returncode != 0:
        raise WorkspaceError(f"branch {branch} does not exist in {worktree}")
    head = head_res.stdout.strip()
    head_tree_res = _git(worktree, *GIT_NEUTRAL_FLAGS, "rev-parse", f"{head}^{{tree}}", env=env)
    _check(head_tree_res, f"rev-parse {head}^{{tree}}", worktree)
    head_tree = head_tree_res.stdout.strip()

    if not files and not links and head_tree != empty_tree:
        # An index built from zero entries writes the empty tree. Comparing
        # that against a NON-empty head_tree below would look like a real
        # change and commit "delete everything" — refuse before hashing (there
        # is nothing to hash) or writing anything at all.
        raise WorkspaceError(
            f"refusing to snapshot an empty tree over a non-empty branch head "
            f"({worktree} holds nothing snapshot_worktree can see)"
        )

    # Spec §4.3, pass 1: hash WITHOUT -w and compare against the head tree. If
    # they agree there is nothing to snapshot, and returning here means a no-op
    # call has written no objects at all.
    probe_entries = _hash_entries(worktree, files, links, env, write=False)
    if sorted(probe_entries) == sorted(_head_entries(worktree, head_tree, env)):
        return None
    # Pass 2: the same hashing WITH -w, and the entries rebuilt from THESE
    # shas -- a file that changed between the passes must not leave the tree
    # pointing at a blob that was never written.
    entries = _hash_entries(worktree, files, links, env, write=True)

    with tempfile.TemporaryDirectory(prefix="dirtywork-snapshot-") as tmpdir:
        index_env = dict(env)
        index_env["GIT_INDEX_FILE"] = str(Path(tmpdir) / "index")
        res = _git(worktree, *GIT_NEUTRAL_FLAGS, "update-index", "-z", "--index-info",
                   env=index_env, stdin_text="".join(e + "\0" for e in entries))
        _check(res, "update-index", worktree)
        res = _git(worktree, *GIT_NEUTRAL_FLAGS, "write-tree", env=index_env)
        _check(res, "write-tree", worktree)
        tree = res.stdout.strip()

    if tree == head_tree:
        return None

    commit_env = dict(env)
    commit_env.update({
        "GIT_AUTHOR_NAME": SNAPSHOT_AUTHOR[0], "GIT_AUTHOR_EMAIL": SNAPSHOT_AUTHOR[1],
        "GIT_COMMITTER_NAME": SNAPSHOT_AUTHOR[0], "GIT_COMMITTER_EMAIL": SNAPSHOT_AUTHOR[1],
    })
    res = _git(worktree, *GIT_NEUTRAL_FLAGS, "commit-tree", tree, "-p", head, "-m", message,
               env=commit_env)
    _check(res, "commit-tree", worktree)
    commit = res.stdout.strip()

    # The old-value argument makes this a compare-and-swap: if anything moved
    # the branch since `head` was read, the update fails instead of clobbering.
    res = _git(worktree, *GIT_NEUTRAL_FLAGS, "update-ref", f"refs/heads/{branch}",
               commit, head, env=env)
    _check(res, "update-ref", worktree)
    # The already-sanctioned index-only refresh, so the worktree's index matches
    # its new HEAD and `git status` is clean rather than showing every file.
    host_read_tree(worktree)
    return commit
