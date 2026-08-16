from __future__ import annotations

import os
from pathlib import Path

import pytest

from dirtywork.budget import DEFAULT_MAX_WORKTREE_FILES, DEFAULT_MAX_WORKTREE_MB, measure_worktree


@pytest.fixture()
def wt(tmp_path: Path) -> Path:
    d = tmp_path / "wt"
    d.mkdir()
    return d


def test_measure_small_tree(wt: Path):
    (wt / "a.txt").write_text("hello")
    sub = wt / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("world")
    report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000)
    assert report.violation is None
    assert report.files == 3  # a.txt, sub/, sub/b.txt
    assert report.bytes > 0
    assert report.escaping_symlinks == []


def test_measure_over_bytes_violation(wt: Path):
    (wt / "big.bin").write_bytes(b"x" * (2 * 1024 * 1024))
    report = measure_worktree(wt, max_bytes=1024 * 1024, max_files=1000)
    assert report.violation is not None
    assert "MB" in report.violation


def test_measure_over_files_violation(wt: Path):
    for i in range(5):
        (wt / f"f{i}.txt").write_text("x")
    report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=3)
    assert report.violation is not None
    assert "entries" in report.violation


def test_measure_reports_absolute_escaping_symlink(wt: Path):
    outside = wt.parent / "outside.txt"
    outside.write_text("secret")
    (wt / "esc.txt").symlink_to(outside)
    report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000)
    assert "esc.txt" in report.escaping_symlinks


def test_measure_reports_relative_escaping_symlink(wt: Path):
    outside_dir = wt.parent / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "x").write_text("secret")
    (wt / "esc_rel.txt").symlink_to(Path("../outside_dir/x"))
    report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000)
    assert "esc_rel.txt" in report.escaping_symlinks


def test_measure_does_not_report_internal_symlink(wt: Path):
    # An ABSOLUTE target is always reported regardless of what it points at
    # (matching the SP2 export validator's rule) — so this uses a RELATIVE
    # target that stays inside the worktree to exercise the "not escaping"
    # branch specifically.
    (wt / "real.txt").write_text("hi")
    (wt / "link.txt").symlink_to(Path("real.txt"))
    report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000)
    assert report.escaping_symlinks == []


@pytest.mark.skipif(os.getuid() == 0, reason="root ignores directory permissions")
def test_measure_unreadable_dir_is_violation(wt: Path):
    locked = wt / "locked"
    locked.mkdir()
    (locked / "secret.txt").write_text("x")
    os.chmod(locked, 0o000)
    try:
        report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000)
        assert report.violation is not None
        assert "unreadable directory" in report.violation
    finally:
        os.chmod(locked, 0o755)


def test_measure_does_not_descend_into_symlinked_dir(wt: Path):
    outside_dir = wt.parent / "big_outside"
    outside_dir.mkdir()
    for i in range(20):
        (outside_dir / f"f{i}.txt").write_bytes(b"x" * 1000)
    (wt / "link_dir").symlink_to(outside_dir)
    report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000)
    assert report.files == 1  # only the symlink itself, not its 20 targets
    assert "link_dir" in report.escaping_symlinks


def test_default_constants():
    assert DEFAULT_MAX_WORKTREE_MB == 2048
    assert DEFAULT_MAX_WORKTREE_FILES == 200_000
