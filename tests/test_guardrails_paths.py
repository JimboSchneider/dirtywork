from __future__ import annotations

import os
from pathlib import Path

import pytest

from dirtywork.guardrails import GuardrailError, resolve_in_worktree


@pytest.fixture()
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    (wt / ".git").mkdir(parents=True)
    (wt / "src").mkdir()
    (wt / "src" / "a.txt").write_text("hi")
    return wt


def test_relative_path_resolves_inside(worktree: Path):
    p = resolve_in_worktree("src/a.txt", worktree)
    assert p == (worktree / "src" / "a.txt").resolve()


def test_dotdot_escape_rejected(worktree: Path):
    with pytest.raises(GuardrailError):
        resolve_in_worktree("../outside.txt", worktree)


def test_absolute_path_outside_rejected(worktree: Path):
    with pytest.raises(GuardrailError):
        resolve_in_worktree("/etc/hosts", worktree)


def test_absolute_path_inside_allowed(worktree: Path):
    p = resolve_in_worktree(str(worktree / "src" / "a.txt"), worktree)
    assert p == (worktree / "src" / "a.txt").resolve()


def test_symlink_escape_rejected(worktree: Path, tmp_path: Path):
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    os.symlink(outside, worktree / "link.txt")
    with pytest.raises(GuardrailError):
        resolve_in_worktree("link.txt", worktree)


def test_git_dir_write_rejected_read_allowed(worktree: Path):
    (worktree / ".git" / "config").write_text("x")
    # reading .git is fine
    resolve_in_worktree(".git/config", worktree)
    # writing is not
    with pytest.raises(GuardrailError):
        resolve_in_worktree(".git/hooks/pre-commit", worktree, writing=True)


def test_nonexistent_target_ok_for_writing(worktree: Path):
    # write_file creates new files; resolution must work for paths that don't exist yet
    p = resolve_in_worktree("src/new/deep/file.txt", worktree, writing=True)
    assert str(p).startswith(str(worktree.resolve()))
