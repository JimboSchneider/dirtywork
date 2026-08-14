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
    slug = make_slug("Add unit tests for the invoice footer!", now)
    assert slug == "add-unit-tests-for-the-08141109"


def test_create_worktree(repo: Path):
    wt = create_worktree(repo, "demo-08141109", None)
    assert wt == repo / ".worktrees" / "la-demo-08141109"
    assert (wt / "f.txt").read_text() == "hello"
    branches = _git(repo, "branch", "--list", "localagent/demo-08141109")
    assert "localagent/demo-08141109" in branches


def test_create_worktree_bad_ref(repo: Path):
    with pytest.raises(WorkspaceError):
        create_worktree(repo, "x-08141109", "no-such-branch")


def test_ensure_worktrees_excluded_idempotent(repo: Path):
    ensure_worktrees_excluded(repo)
    ensure_worktrees_excluded(repo)
    exclude = repo / ".git" / "info" / "exclude"
    assert exclude.read_text().count(".worktrees/") == 1


def test_load_repo_context(repo: Path):
    assert load_repo_context(repo) is None
    (repo / "AGENTS.md").write_text("agents rules")
    assert load_repo_context(repo) == "agents rules"
    (repo / "CLAUDE.md").write_text("claude rules")  # CLAUDE.md wins
    assert load_repo_context(repo) == "claude rules"
