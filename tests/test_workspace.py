from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from dirtywork.workspace import (
    WorkspaceError,
    create_worktree,
    ensure_worktrees_excluded,
    load_repo_context,
    make_slug,
    preflight_repo,
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
