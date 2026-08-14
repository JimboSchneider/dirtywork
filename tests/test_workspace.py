from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from localagent.workspace import (
    WorkspaceError,
    create_worktree,
    ensure_worktrees_excluded,
    load_repo_context,
    make_slug,
    preflight_repo,
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
    assert wt == repo / ".worktrees" / "la-demo-08141109"
    assert (wt / "f.txt").read_text() == "hello"
    branches = _git(repo, "branch", "--list", "localagent/demo-08141109")
    assert "localagent/demo-08141109" in branches


def test_create_worktree_bad_ref(repo: Path):
    with pytest.raises(WorkspaceError):
        create_worktree(repo, "x-08141109", "no-such-branch")


def test_create_worktree_preexisting_branch_not_deleted(repo: Path):
    # Pre-create the branch create_worktree would try to create-with `-b`, with a
    # distinct commit on it (simulating saved work from a prior run). git refuses
    # "worktree add -b" on an already-existing branch, so this must fail -- but
    # the best-effort cleanup must NOT delete a branch that pre-dates this call.
    _git(repo, "branch", "localagent/pre-08141109-ab12")
    _git(repo, "checkout", "localagent/pre-08141109-ab12")
    _git(repo, "commit", "--allow-empty", "-m", "saved work on pre-existing branch")
    _git(repo, "checkout", "main")

    with pytest.raises(WorkspaceError):
        create_worktree(repo, "pre-08141109-ab12", None)

    branches = _git(repo, "branch", "--list", "localagent/pre-08141109-ab12")
    assert "localagent/pre-08141109-ab12" in branches


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


def test_load_repo_context(repo: Path):
    assert load_repo_context(repo) is None
    (repo / "AGENTS.md").write_text("agents rules")
    assert load_repo_context(repo) == "agents rules"
    (repo / "CLAUDE.md").write_text("claude rules")  # CLAUDE.md wins
    assert load_repo_context(repo) == "claude rules"


def test_create_worktree_existing_dir_no_stale_branch(repo: Path):
    (repo / ".worktrees" / "la-dup-08141109").mkdir(parents=True)
    (repo / ".worktrees" / "la-dup-08141109" / "junk.txt").write_text("junk")
    with pytest.raises(WorkspaceError):
        create_worktree(repo, "dup-08141109", None)
    branches = _git(repo, "branch", "--list", "localagent/dup-08141109")
    assert "localagent/dup-08141109" not in branches
