from __future__ import annotations

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
