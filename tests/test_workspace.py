from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from dirtywork.workspace import (
    WorkspaceError,
    create_worktree,
    ensure_worktrees_excluded,
    host_read_tree,
    host_diff_stat,
    host_untracked,
    load_repo_context,
    make_slug,
    preflight_repo,
    remove_worktree,
    worktree_base_commit,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("hello")
    _git(r, "add", ".")
    _git(r, "commit", "-m", "init")
    return r


def test_preflight_ok(repo: Path):
    preflight_repo(repo)  # no raise


def test_preflight_not_git(tmp_path: Path):
    with pytest.raises(WorkspaceError):
        preflight_repo(tmp_path)


def test_preflight_no_commits(tmp_path: Path):
    r = tmp_path / "empty"
    r.mkdir()
    _git(r, "init")
    with pytest.raises(WorkspaceError):
        preflight_repo(r)


def test_make_slug():
    now = datetime(2026, 8, 14, 11, 9)
    slug = make_slug("Add unit tests for the invoice footer!", now, salt="ab12")
    assert slug == "add-unit-tests-for-the-0814110900-ab12"


def test_make_slug_default_salt_is_random():
    now = datetime(2026, 8, 14, 11, 9)
    s1 = make_slug("same task", now)
    s2 = make_slug("same task", now)
    assert s1 != s2


def test_make_slug_empty_task():
    now = datetime(2026, 8, 14, 11, 9)
    slug = make_slug("", now, salt="ab12")
    assert slug == "task-0814110900-ab12"


def test_make_slug_punctuation_only():
    now = datetime(2026, 8, 14, 11, 9)
    slug = make_slug("!!! ???", now, salt="ab12")
    assert slug == "task-0814110900-ab12"


def test_make_slug_long_task_truncates():
    now = datetime(2026, 8, 14, 11, 9)
    long_task = "a" * 50 + " b"
    slug = make_slug(long_task, now, salt="ab12")
    base_part = slug.rsplit("-", 3)[0]
    assert len(base_part) <= 40
    assert base_part == "a" * 40


def test_create_worktree(repo: Path):
    wt = create_worktree(repo, "demo-08141109", None)
    assert wt == repo / ".worktrees" / "dw-demo-08141109"
    assert (wt / "f.txt").read_text() == "hello"
    branches = _git(repo, "branch", "--list", "dirtywork/demo-08141109")
    assert "dirtywork/demo-08141109" in branches


def test_create_worktree_bad_ref(repo: Path):
    with pytest.raises(WorkspaceError):
        create_worktree(repo, "x-08141109", "no-such-branch")


def test_create_worktree_preexisting_branch_not_deleted(repo: Path):
    # Pre-create the branch create_worktree would try to create-with `-b`, with a
    # distinct commit on it (simulating saved work from a prior run). git refuses
    # "worktree add -b" on an already-existing branch, so this must fail -- but
    # the best-effort cleanup must NOT delete a branch that pre-dates this call.
    _git(repo, "branch", "dirtywork/pre-08141109-ab12")
    _git(repo, "checkout", "dirtywork/pre-08141109-ab12")
    _git(repo, "commit", "--allow-empty", "-m", "saved work on pre-existing branch")
    _git(repo, "checkout", "main")

    with pytest.raises(WorkspaceError):
        create_worktree(repo, "pre-08141109-ab12", None)

    branches = _git(repo, "branch", "--list", "dirtywork/pre-08141109-ab12")
    assert "dirtywork/pre-08141109-ab12" in branches


def test_remove_worktree_removes_dir_and_branch(repo: Path):
    wt = create_worktree(repo, "abc", None)
    assert wt.is_dir()
    branch_check = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "refs/heads/dirtywork/abc"],
        capture_output=True, text=True,
    )
    assert branch_check.returncode == 0

    remove_worktree(repo, "abc")

    assert not wt.exists()
    branch_check = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "refs/heads/dirtywork/abc"],
        capture_output=True, text=True,
    )
    assert branch_check.returncode != 0


def test_remove_worktree_is_idempotent(repo: Path):
    remove_worktree(repo, "never-created")  # no raise


def test_ensure_worktrees_excluded_idempotent(repo: Path):
    ensure_worktrees_excluded(repo)
    ensure_worktrees_excluded(repo)
    exclude = repo / ".git" / "info" / "exclude"
    assert exclude.read_text().count(".worktrees/") == 1


def test_ensure_worktrees_excluded_from_linked_worktree(repo: Path, tmp_path: Path):
    # A linked worktree's `git rev-parse --git-dir` points at the private
    # .git/worktrees/<name> dir, but git only ever consults the shared
    # repository's info/exclude. Calling ensure_worktrees_excluded with the
    # linked worktree's path must still land the entry in the PRIMARY repo's
    # info/exclude, not the worktree's private gitdir.
    wt2 = tmp_path / "wt2"
    _git(repo, "worktree", "add", str(wt2), "-b", "side")

    ensure_worktrees_excluded(wt2)

    primary_exclude = repo / ".git" / "info" / "exclude"
    assert ".worktrees/" in primary_exclude.read_text()

    # Prove git actually consults that shared file when run from the worktree:
    # a .worktrees/ dir inside the linked worktree should be ignored by status.
    (wt2 / ".worktrees" / "dummy").mkdir(parents=True)
    status = subprocess.run(
        ["git", "-C", str(wt2), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert ".worktrees" not in status


def test_ensure_worktrees_excluded_rejects_symlinked_exclude(repo: Path, tmp_path: Path):
    exclude = repo / ".git" / "info" / "exclude"
    outside = tmp_path / "outside-exclude.txt"
    outside.write_text("original content\n")
    exclude.unlink()
    exclude.symlink_to(outside)

    with pytest.raises(WorkspaceError):
        ensure_worktrees_excluded(repo)

    assert outside.read_text() == "original content\n"  # untouched


def _commit_file(repo: Path, name: str, content: str) -> str:
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", f"add {name}")
    return _git(repo, "rev-parse", "HEAD").strip()


def test_load_repo_context_none_when_absent(repo: Path):
    base = _git(repo, "rev-parse", "HEAD").strip()
    assert load_repo_context(repo, base) is None


def test_load_repo_context_reads_from_base_commit(repo: Path):
    base = _commit_file(repo, "CLAUDE.md", "claude rules")
    assert load_repo_context(repo, base) == "claude rules"


def test_load_repo_context_agents_md_fallback(repo: Path):
    base = _commit_file(repo, "AGENTS.md", "agents rules")
    assert load_repo_context(repo, base) == "agents rules"


def test_load_repo_context_claude_md_preferred_over_agents_md(repo: Path):
    _commit_file(repo, "AGENTS.md", "agents rules")
    base = _commit_file(repo, "CLAUDE.md", "claude rules")
    assert load_repo_context(repo, base) == "claude rules"


def test_load_repo_context_mode_100755_accepted(repo: Path):
    (repo / "CLAUDE.md").write_text("exec rules")
    (repo / "CLAUDE.md").chmod(0o755)
    _git(repo, "add", "CLAUDE.md")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "exec claude")
    base = _git(repo, "rev-parse", "HEAD").strip()
    assert load_repo_context(repo, base) == "exec rules"


def test_load_repo_context_ignores_uncommitted_file(repo: Path):
    # File exists on disk but was never committed at base_commit — must be
    # invisible. This is the whole point of reading from the object store
    # instead of the filesystem.
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "CLAUDE.md").write_text("not committed")
    assert load_repo_context(repo, base) is None


def test_load_repo_context_ignores_symlink(repo: Path):
    import os
    os.symlink("/etc/passwd", repo / "CLAUDE.md")
    _git(repo, "add", "CLAUDE.md")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "symlinked claude md")
    base = _git(repo, "rev-parse", "HEAD").strip()
    assert load_repo_context(repo, base) is None


def test_load_repo_context_skips_oversized_blob(repo: Path, monkeypatch):
    import dirtywork.workspace as workspace_mod
    monkeypatch.setattr(workspace_mod, "MAX_CONTEXT_BYTES", 10)
    base = _commit_file(repo, "CLAUDE.md", "this content is over ten bytes")
    assert load_repo_context(repo, base) is None


def test_load_repo_context_truncates_long_content(repo: Path):
    base = _commit_file(repo, "CLAUDE.md", "x" * 40000)
    result = load_repo_context(repo, base)
    assert result is not None
    marker = "\n[truncated at 32000 chars]"
    assert result.endswith(marker)
    assert len(result) == 32000 + len(marker)


def test_worktree_base_commit(repo: Path):
    wt = create_worktree(repo, "ctx-08141109", None)
    expected = _git(repo, "rev-parse", "HEAD").strip()
    assert worktree_base_commit(wt) == expected


def test_host_diff_stat_reports_tracked_changes(repo: Path):
    wt = create_worktree(repo, "diff-08141109", None)
    base = worktree_base_commit(wt)
    (wt / "f.txt").write_text("changed content")
    out = host_diff_stat(wt, base)
    assert "f.txt" in out


def test_host_diff_stat_no_changes_is_empty(repo: Path):
    wt = create_worktree(repo, "nodiff-08141109", None)
    base = worktree_base_commit(wt)
    out = host_diff_stat(wt, base)
    assert out.strip() == ""


def test_host_diff_stat_ignores_untracked_new_files(repo: Path):
    # git diff --stat only reports TRACKED changes — a brand-new file that
    # was never `git add`ed does not appear. This is a documented limitation
    # of host-mode diff_stat, not a bug.
    wt = create_worktree(repo, "untracked-08141109", None)
    base = worktree_base_commit(wt)
    (wt / "new.txt").write_text("hello")
    out = host_diff_stat(wt, base)
    assert "new.txt" not in out


def test_host_diff_stat_includes_staged_changes(repo: Path):
    # The model is allowed to run `git add` inside the worktree; diff_stat
    # compares against base_commit so staged-but-uncommitted changes must
    # still show up (a plain `git diff --stat` with no ref would miss them).
    wt = create_worktree(repo, "staged-08141109", None)
    base = worktree_base_commit(wt)
    (wt / "f.txt").write_text("staged content")
    _git(wt, "add", "f.txt")
    out = host_diff_stat(wt, base)
    assert "f.txt" in out


def test_host_diff_stat_includes_committed_changes(repo: Path):
    # The model is also allowed to `git commit` inside the worktree; diff_stat
    # compares against base_commit (not HEAD vs index) so committed changes
    # must still show up.
    wt = create_worktree(repo, "committed-08141109", None)
    base = worktree_base_commit(wt)
    (wt / "f.txt").write_text("committed content")
    _git(wt, "add", "f.txt")
    _git(wt, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "change f.txt")
    out = host_diff_stat(wt, base)
    assert "f.txt" in out


def test_host_diff_stat_truncates(repo: Path):
    # git diff --stat only reports TRACKED changes (see the untracked-files
    # test above) — commit the files first, then modify them, so there is
    # real tracked diff output to truncate.
    for i in range(50):
        (repo / f"file{i}.txt").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "add files")
    wt = create_worktree(repo, "trunc-08141109", None)
    base = worktree_base_commit(wt)
    for i in range(50):
        (wt / f"file{i}.txt").write_text("changed content\n" * 5)
    out = host_diff_stat(wt, base, cap=200)
    assert len(out) <= 200 + len("\n[truncated at 200 chars]")
    assert "truncated at 200 chars" in out


def test_host_untracked_lists_new_file(repo: Path):
    wt = create_worktree(repo, "untracked-list-08161109", None)
    (wt / "new.txt").write_text("hello")
    out = host_untracked(wt)
    assert out == "new.txt"


def test_host_untracked_collapses_directory(repo: Path):
    # Default `git status --porcelain` mode (not -uall) reports a whole
    # untracked directory as one `?? dir/` line rather than every file
    # inside it — this is what keeps a `npm ci`/`volta install` run from
    # producing thousands of lines here.
    wt = create_worktree(repo, "untracked-dir-08161109", None)
    pkgs = wt / "pkgs"
    pkgs.mkdir()
    (pkgs / "a.txt").write_text("a")
    (pkgs / "b.txt").write_text("b")
    out = host_untracked(wt)
    assert out == "pkgs/"


def test_host_untracked_empty_when_clean(repo: Path):
    wt = create_worktree(repo, "untracked-clean-08161109", None)
    out = host_untracked(wt)
    assert out == ""


def test_host_untracked_excludes_staged(repo: Path):
    # Once `git add`ed, a file is staged (not untracked) and is covered by
    # host_diff_stat instead — it must not also show up here.
    wt = create_worktree(repo, "untracked-staged-08161109", None)
    (wt / "new.txt").write_text("hello")
    _git(wt, "add", "new.txt")
    out = host_untracked(wt)
    assert "new.txt" not in out


def test_host_untracked_truncates(repo: Path):
    wt = create_worktree(repo, "untracked-trunc-08161109", None)
    for i in range(300):
        (wt / f"file-with-a-long-name-{i:04d}.txt").write_text("x")
    out = host_untracked(wt, cap=200)
    assert len(out) <= 200 + len("\n[truncated at 200 chars]")
    assert out.endswith("[truncated at 200 chars]")


def test_create_worktree_existing_dir_no_stale_branch(repo: Path):
    (repo / ".worktrees" / "dw-dup-08141109").mkdir(parents=True)
    (repo / ".worktrees" / "dw-dup-08141109" / "junk.txt").write_text("junk")
    with pytest.raises(WorkspaceError):
        create_worktree(repo, "dup-08141109", None)
    branches = _git(repo, "branch", "--list", "dirtywork/dup-08141109")
    assert "dirtywork/dup-08141109" not in branches


def test_create_worktree_worktrees_symlink_rejected(repo: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / ".worktrees").symlink_to(outside)
    with pytest.raises(WorkspaceError):
        create_worktree(repo, "sym-08141109", None)
    assert list(outside.iterdir()) == []  # nothing created through the symlink


def test_create_worktree_destination_symlink_rejected(repo: Path, tmp_path: Path):
    (repo / ".worktrees").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (repo / ".worktrees" / "dw-pre-08141109").symlink_to(elsewhere)
    with pytest.raises(WorkspaceError):
        create_worktree(repo, "pre-08141109", None)
    porcelain = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert porcelain.count("worktree ") == 1  # only the main worktree


def test_create_worktree_destination_empty_dir_rejected(repo: Path):
    (repo / ".worktrees" / "dw-emptydir-08141109").mkdir(parents=True)
    with pytest.raises(WorkspaceError):
        create_worktree(repo, "emptydir-08141109", None)


def test_make_slug_salt_is_8_hex_chars():
    now = datetime(2026, 8, 14, 11, 9)
    slug = make_slug("same task", now)
    salt = slug.rsplit("-", 1)[-1]
    assert len(salt) == 8
    int(salt, 16)  # raises ValueError if not valid hex


def test_create_worktree_no_checkout_leaves_only_dot_git(repo: Path):
    wt = create_worktree(repo, "nc-08141109", None, no_checkout=True)
    entries = list(wt.iterdir())
    assert len(entries) == 1
    assert entries[0].name == ".git"
    assert entries[0].is_file()  # linked worktree: .git is a file pointing at the gitdir


def test_create_worktree_no_checkout_head_matches_repo_head(repo: Path):
    wt = create_worktree(repo, "nc2-08141109", None, no_checkout=True)
    wt_head = _git(wt, "rev-parse", "HEAD").strip()
    repo_head = _git(repo, "rev-parse", "HEAD").strip()
    assert wt_head == repo_head


def test_host_read_tree_populates_index_not_working_tree(repo: Path):
    wt = create_worktree(repo, "hrt-08141109", None, no_checkout=True)
    host_read_tree(wt)
    ls_files = _git(wt, "ls-files")
    assert "f.txt" in ls_files
    assert not (wt / "f.txt").exists()  # index only — no working-tree write


def test_host_read_tree_failure_raises_workspace_error(tmp_path: Path):
    not_a_worktree = tmp_path / "not-a-worktree"
    not_a_worktree.mkdir()
    with pytest.raises(WorkspaceError):
        host_read_tree(not_a_worktree)


def test_host_files_changed_lists_tracked_and_untracked(repo: Path, tmp_path: Path):
    from dirtywork.workspace import host_files_changed
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "f.txt").write_text("changed")
    (repo / "brand-new.txt").write_text("new")
    (repo / "sub").mkdir()
    (repo / "sub" / "deep.txt").write_text("deep")
    (repo / ".gitignore").write_text("ignored.txt\n")
    (repo / "ignored.txt").write_text("nope")
    _git(repo, "add", ".gitignore")
    paths, truncated = host_files_changed(repo, base)
    assert paths == [".gitignore", "brand-new.txt", "f.txt", "sub/deep.txt"]
    assert truncated is False
    assert "ignored.txt" not in paths


def test_host_files_changed_caps_and_reports_truncation(repo: Path):
    from dirtywork.workspace import host_files_changed
    base = _git(repo, "rev-parse", "HEAD").strip()
    for i in range(12):
        (repo / f"n{i:02d}.txt").write_text("x")
    paths, truncated = host_files_changed(repo, base, cap=5)
    assert len(paths) == 5
    assert paths == sorted(paths)
    assert truncated is True


def _snapshot_repo(tmp_path: Path):
    """A repo plus a linked worktree rigged so a snapshot built from porcelain
    would be caught: a clean filter on every path, a pre-commit hook that
    leaves a sentinel and fails, a symlink, an executable file, a deletion."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "keep.txt").write_text("keep\n")
    (repo / "gone.txt").write_text("gone\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-b", "dirtywork/snap", str(wt))
    _git(repo, "config", "filter.x.clean", "sed s/RAW/FILTERED/")
    (wt / ".gitattributes").write_text("* filter=x\n")
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text('#!/bin/sh\ntouch "$(git rev-parse --git-common-dir)/hook-ran"\nexit 1\n')
    hook.chmod(0o755)
    (wt / "raw.txt").write_text("RAW content\n")
    (wt / "script.sh").write_text("#!/bin/sh\necho hi\n")
    (wt / "script.sh").chmod(0o755)
    (wt / "link").symlink_to("/etc/passwd")
    (wt / "gone.txt").unlink()
    return repo, wt


def test_snapshot_worktree_commits_raw_content_without_filters_or_hooks(tmp_path: Path):
    from dirtywork.workspace import snapshot_worktree
    repo, wt = _snapshot_repo(tmp_path)
    sha = snapshot_worktree(wt, "dirtywork/snap", "wip: dirtywork run snap")
    assert sha is not None and len(sha) == 40

    entries = {}
    for line in _git(repo, "ls-tree", "-r", sha).splitlines():
        meta, _, name = line.partition("\t")
        mode, _kind, blob = meta.split()
        entries[name] = (mode, blob)
    assert set(entries) == {".gitattributes", "keep.txt", "raw.txt", "script.sh", "link"}
    assert "gone.txt" not in entries                 # a deletion is part of the snapshot
    assert entries["keep.txt"][0] == "100644"
    assert entries["script.sh"][0] == "100755"       # executable bit preserved
    assert entries["link"][0] == "120000"            # symlink, recorded not followed
    assert _git(repo, "cat-file", "-p", entries["link"][1]) == "/etc/passwd"
    # the clean filter never ran: the blob holds the raw bytes
    assert _git(repo, "cat-file", "-p", entries["raw.txt"][1]) == "RAW content\n"
    # the pre-commit hook never ran
    assert not (repo / ".git" / "hook-ran").exists()
    # the branch moved onto the new commit, authored by dirtywork
    assert _git(repo, "rev-parse", "dirtywork/snap").strip() == sha
    assert _git(repo, "log", "-1", "--format=%an <%ae>", sha).strip() == (
        "dirtywork <dirtywork@localhost>")
    assert _git(repo, "log", "-1", "--format=%s", sha).strip() == "wip: dirtywork run snap"


def test_snapshot_worktree_returns_none_when_the_tree_is_unchanged(tmp_path: Path):
    from dirtywork.workspace import snapshot_worktree
    repo, wt = _snapshot_repo(tmp_path)
    first = snapshot_worktree(wt, "dirtywork/snap", "wip: one")
    assert first is not None
    assert snapshot_worktree(wt, "dirtywork/snap", "wip: two") is None
    assert _git(repo, "rev-parse", "dirtywork/snap").strip() == first


def test_snapshot_worktree_refuses_a_path_containing_a_newline(tmp_path: Path):
    from dirtywork.workspace import snapshot_worktree
    repo, wt = _snapshot_repo(tmp_path)
    (wt / "we\nird.txt").write_text("x")
    with pytest.raises(WorkspaceError) as excinfo:
        snapshot_worktree(wt, "dirtywork/snap", "wip: newline")
    assert "control character" in str(excinfo.value)


def test_snapshot_worktree_refuses_a_path_containing_a_carriage_return(tmp_path: Path):
    # `hash-object --stdin-paths` strips a trailing \r from a line before
    # opening the file, so a name ending in \r would hash a DIFFERENT path (or
    # fail outright) rather than the file that was actually walked — refused
    # for the same reason as \n, by the same control-character guard.
    from dirtywork.workspace import snapshot_worktree
    repo, wt = _snapshot_repo(tmp_path)
    (wt / "weird\rname.txt").write_text("x")
    with pytest.raises(WorkspaceError) as excinfo:
        snapshot_worktree(wt, "dirtywork/snap", "wip: carriage return")
    assert "control character" in str(excinfo.value)


def test_snapshot_worktree_refuses_a_path_ending_in_a_carriage_return(tmp_path: Path):
    # The exploitable shape the docstring above describes: `hash-object
    # --stdin-paths` strips a TRAILING \r from a line before opening the
    # file, so a name ENDING in \r (not just containing one) would hash a
    # different path than the one _walk_worktree actually recorded.
    from dirtywork.workspace import snapshot_worktree
    repo, wt = _snapshot_repo(tmp_path)
    (wt / "trailing\r").write_text("x")
    with pytest.raises(WorkspaceError) as excinfo:
        snapshot_worktree(wt, "dirtywork/snap", "wip: trailing cr")
    assert "control character" in str(excinfo.value)


def test_snapshot_worktree_refuses_an_undecodable_filename(tmp_path: Path):
    # A filename with bytes that are not valid UTF-8 round-trips through
    # os.walk as a str with surrogate-escaped code points (os.fsdecode) --
    # every ord(c) >= 32, so the control-character guard lets it through, and
    # _git's text=True stdin encoding would then raise UnicodeEncodeError
    # itself (not a WorkspaceError) instead of a clean refusal.
    from dirtywork.workspace import snapshot_worktree
    repo, wt = _snapshot_repo(tmp_path)
    bad_name = os.fsdecode(b"bad\xffname.txt")
    try:
        (wt / bad_name).write_bytes(b"x")
    except (OSError, UnicodeEncodeError):
        pytest.skip("filesystem refuses an undecodable filename")
    with pytest.raises(WorkspaceError) as excinfo:
        snapshot_worktree(wt, "dirtywork/snap", "wip: undecodable name")
    assert "not valid UTF-8" in str(excinfo.value)


def test_snapshot_worktree_records_a_literal_quote_and_escape_in_a_filename(tmp_path: Path):
    # `git update-index --index-info` C-unquotes a path that STARTS with `"`:
    # without -z, a file literally named `"src\057main.py"` on disk gets
    # recorded in the tree as `src/main.py` (verified against git 2.48.1).
    # `snapshot_worktree` must use `-z`/NUL-delimited update-index so the
    # literal on-disk name round-trips untouched, and the real `src/main.py`
    # (a different file) must be unaffected.
    from dirtywork.workspace import snapshot_worktree
    repo, wt = _snapshot_repo(tmp_path)
    weird_name = '"src\\057main.py"'
    (wt / weird_name).write_text("quoted name content\n")
    (wt / "src").mkdir()
    (wt / "src" / "main.py").write_text("real main\n")

    sha = snapshot_worktree(wt, "dirtywork/snap", "wip: quoted name")
    assert sha is not None

    raw = _git(repo, "ls-tree", "-r", "-z", sha)
    entries = {}
    for record in raw.split("\0"):
        if not record:
            continue
        meta, _, name = record.partition("\t")
        _mode, _kind, blob = meta.split()
        entries[name] = blob
    assert weird_name in entries
    assert "src/main.py" in entries
    assert _git(repo, "cat-file", "-p", entries[weird_name]) == "quoted name content\n"
    assert _git(repo, "cat-file", "-p", entries["src/main.py"]) == "real main\n"


def test_snapshot_worktree_refuses_when_worktree_is_on_a_different_branch(tmp_path: Path):
    # `update-ref` moves `refs/heads/<branch>` while `host_read_tree` reads
    # HEAD — without this guard, a worktree switched to another branch would
    # get its content committed onto `branch` and its index reset to HEAD's
    # tree, out from under whatever the worktree is actually on.
    from dirtywork.workspace import snapshot_worktree
    repo, wt = _snapshot_repo(tmp_path)
    orig = _git(repo, "rev-parse", "dirtywork/snap").strip()
    _git(wt, "checkout", "-b", "dirtywork/other")
    with pytest.raises(WorkspaceError) as excinfo:
        snapshot_worktree(wt, "dirtywork/snap", "wip: wrong branch")
    message = str(excinfo.value)
    assert "dirtywork/other" in message and "dirtywork/snap" in message
    assert _git(repo, "rev-parse", "dirtywork/snap").strip() == orig  # ref untouched


def test_snapshot_worktree_refuses_to_commit_an_empty_tree_over_a_nonempty_head(tmp_path: Path):
    # entries built from a fully-emptied worktree write the canonical empty
    # tree; comparing that against a NON-empty branch head would look like a
    # real change and commit "delete everything onto branch" — refused
    # instead. This guard lives inside snapshot_worktree (not just the CLI)
    # because Task 8 calls snapshot_worktree directly.
    from dirtywork.workspace import snapshot_worktree
    repo = tmp_path / "repo2"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("content\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    wt = tmp_path / "wt2"
    _git(repo, "worktree", "add", "-b", "dirtywork/snap2", str(wt))
    orig = _git(repo, "rev-parse", "dirtywork/snap2").strip()
    (wt / "f.txt").unlink()

    with pytest.raises(WorkspaceError) as excinfo:
        snapshot_worktree(wt, "dirtywork/snap2", "wip: empty")
    assert "empty tree" in str(excinfo.value)
    assert _git(repo, "rev-parse", "dirtywork/snap2").strip() == orig  # ref untouched


def test_snapshot_worktree_reports_skipped_non_regular_entries(tmp_path: Path):
    import os
    from dirtywork.workspace import snapshot_worktree
    repo, wt = _snapshot_repo(tmp_path)
    os.mkfifo(wt / "fifo")
    report: dict = {}

    sha = snapshot_worktree(wt, "dirtywork/snap", "wip: fifo", report=report)
    assert sha is not None
    assert report["skipped"] == 1
    assert "fifo" not in _git(repo, "ls-tree", "-r", "--name-only", sha)


def test_snapshot_worktree_respects_ignore_rules_like_git_add_dash_a(tmp_path: Path):
    # Fix item 1: an untracked file matching .gitignore is dropped from the
    # snapshot tree, the same as `git add -A` would drop it, while a tracked
    # file matching the SAME pattern (force-added past the ignore rule) is
    # still committed -- check-ignore is index-aware, so tracking wins.
    from dirtywork.workspace import snapshot_worktree
    repo = tmp_path / "repo4"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text("*.log\n")
    (repo / "tracked.log").write_text("tracked\n")
    _git(repo, "add", "-f", "tracked.log", ".gitignore")
    _git(repo, "commit", "-m", "init")
    wt = tmp_path / "wt4"
    _git(repo, "worktree", "add", "-b", "dirtywork/snap4", str(wt))
    (wt / "untracked.log").write_text("untracked\n")
    (wt / "tracked.log").write_text("tracked, modified\n")

    sha = snapshot_worktree(wt, "dirtywork/snap4", "wip: ignore rules")
    assert sha is not None
    names = _git(repo, "ls-tree", "-r", "--name-only", sha).splitlines()
    assert "untracked.log" not in names
    assert "tracked.log" in names


def test_snapshot_worktree_and_dirty_check_agree_when_only_ignored_files_changed(tmp_path: Path):
    # Fix item 1: host_worktree_dirty (git status, which already excludes
    # ignored files) and snapshot_worktree must agree -- when the only change
    # in the worktree is an ignored file, neither sees anything to do.
    from dirtywork.workspace import host_worktree_dirty, snapshot_worktree
    repo = tmp_path / "repo5"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text("*.log\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    wt = tmp_path / "wt5"
    _git(repo, "worktree", "add", "-b", "dirtywork/snap5", str(wt))
    (wt / "debug.log").write_text("noise\n")

    assert host_worktree_dirty(wt) is False
    assert snapshot_worktree(wt, "dirtywork/snap5", "wip: nothing") is None


def test_snapshot_worktree_excludes_an_entire_ignored_directory(tmp_path: Path):
    # Fix item 1: an ignored DIRECTORY (a `build/` pattern) is excluded
    # entirely, including files nested inside it -- check-ignore reports a
    # nested path as ignored via the directory pattern, same as git add -A.
    from dirtywork.workspace import snapshot_worktree
    repo = tmp_path / "repo6"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text("build/\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    wt = tmp_path / "wt6"
    _git(repo, "worktree", "add", "-b", "dirtywork/snap6", str(wt))
    (wt / "build").mkdir()
    (wt / "build" / "out.o").write_text("binary junk\n")
    (wt / "build" / "nested").mkdir()
    (wt / "build" / "nested" / "deep.o").write_text("more junk\n")
    (wt / "keep.txt").write_text("keep\n")

    sha = snapshot_worktree(wt, "dirtywork/snap6", "wip: build excluded")
    assert sha is not None
    names = _git(repo, "ls-tree", "-r", "--name-only", sha).splitlines()
    assert not any(n.startswith("build/") for n in names)
    assert "keep.txt" in names


def test_walk_worktree_raises_loudly_on_an_unreadable_directory(tmp_path: Path):
    # Fix item 2: os.walk's default onerror silently skips a directory it
    # cannot list, which would make the snapshot commit that directory's
    # files as DELETED. A directory dirtywork cannot read must fail the
    # whole snapshot loudly instead.
    from dirtywork.workspace import snapshot_worktree
    if os.geteuid() == 0:
        pytest.skip("root can read a chmod 000 directory")
    repo, wt = _snapshot_repo(tmp_path)
    orig = _git(repo, "rev-parse", "dirtywork/snap").strip()
    blocked = wt / "blocked"
    blocked.mkdir()
    (blocked / "secret.txt").write_text("nope\n")
    blocked.chmod(0o000)
    try:
        with pytest.raises(WorkspaceError) as excinfo:
            snapshot_worktree(wt, "dirtywork/snap", "wip: blocked dir")
        assert "cannot read directory" in str(excinfo.value)
    finally:
        blocked.chmod(0o755)
    assert _git(repo, "rev-parse", "dirtywork/snap").strip() == orig  # ref untouched


def test_snapshot_worktree_succeeds_when_an_unreadable_directory_is_ignored(tmp_path: Path):
    # Round 2 fix: an unreadable directory that turns out to be IGNORED
    # (e.g. a root-owned dir inside a docker run's `.venv/`, or `build/sub`
    # under a `build/` gitignore pattern) must NOT hard-fail the snapshot --
    # nothing under it would ever have been committed anyway. The unreadable
    # dir's path rides through the same check-ignore batch as everything
    # else; only an unreadable dir that is NOT ignored still raises.
    from dirtywork.workspace import snapshot_worktree
    if os.geteuid() == 0:
        pytest.skip("root can read a chmod 000 directory")
    repo = tmp_path / "repo8"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text("build/\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    wt = tmp_path / "wt8"
    _git(repo, "worktree", "add", "-b", "dirtywork/snap8", str(wt))
    (wt / "build").mkdir()
    blocked = wt / "build" / "sub"
    blocked.mkdir()
    (blocked / "secret.txt").write_text("nope\n")
    (wt / "keep.txt").write_text("keep\n")
    blocked.chmod(0o000)
    try:
        sha = snapshot_worktree(wt, "dirtywork/snap8", "wip: unreadable but ignored")
    finally:
        blocked.chmod(0o755)
    assert sha is not None
    names = _git(repo, "ls-tree", "-r", "--name-only", sha).splitlines()
    assert not any(n.startswith("build/") for n in names)
    assert "keep.txt" in names


def test_host_worktree_dirty_sees_untracked_and_modified_and_fails_closed(tmp_path: Path):
    # Deliberately NOT _snapshot_repo: that fixture rigs a `.gitattributes`
    # clean filter to prove snapshot_worktree bypasses it when COMMITTING.
    # A real `git status` (which honours that filter, same as any ordinary
    # git command) would then see the raw committed blob as "modified"
    # relative to the filtered working-tree content forever, which is a
    # property of the filter, not of host_worktree_dirty -- reusing it here
    # would make "the snapshot made it clean" unprovable for reasons
    # unrelated to what this test checks.
    from dirtywork.workspace import host_worktree_dirty, snapshot_worktree
    repo = tmp_path / "repo3"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "keep.txt").write_text("keep\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    wt = tmp_path / "wt3"
    _git(repo, "worktree", "add", "-b", "dirtywork/snap3", str(wt))
    (wt / "keep.txt").write_text("modified\n")     # a tracked-file modification
    (wt / "new.txt").write_text("untracked\n")     # an untracked file
    assert host_worktree_dirty(wt) is True         # sees both the modification and the untracked file
    snapshot_worktree(wt, "dirtywork/snap3", "wip: clean it")
    assert host_worktree_dirty(wt) is False        # the snapshot made it clean
    assert host_worktree_dirty(tmp_path / "not-a-repo") is True   # fail closed


def test_host_worktree_dirty_fails_closed_on_a_timeout(tmp_path: Path, monkeypatch):
    # runs._worktree_is_dirty (the destructive `runs clean` gate this replaced)
    # had timeout=10 and treated TimeoutExpired as dirty; host_worktree_dirty's
    # own _git call must too, so a hung host git process cannot hang a
    # destructive path.
    import dirtywork.workspace as workspace_mod

    def _hangs(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0] if args else "git", timeout=10)

    monkeypatch.setattr(workspace_mod.subprocess, "run", _hangs)
    assert workspace_mod.host_worktree_dirty(tmp_path) is True
