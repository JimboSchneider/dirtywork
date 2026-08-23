from __future__ import annotations

import os
import sys
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


# `os.getuid` does not exist on Windows, and this decorator is evaluated at
# COLLECTION time -- calling it unguarded makes the whole module fail to import
# there, which would hide every other budget test from the advisory Windows run
# (spec §5). `chmod 000` does not deny access on Windows either, so the test is
# skipped rather than fixed.
@pytest.mark.skipif(getattr(os, "getuid", lambda: -1)() == 0 or sys.platform == "win32",
                    reason="root (or Windows, where chmod 000 does not deny access) "
                           "ignores directory permissions")
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


def test_measure_worktree_sweeps_only_generated_temps(wt: Path):
    # Spec §2.5: the sweep matches the FULL generated shape, so a worker file
    # that merely starts like one survives.
    from dirtywork import tools
    ours = wt / tools.tmp_name("app.py")
    ours.write_text("staged")
    theirs = wt / ".dw-tmp.notes"
    theirs.write_text("mine")
    (wt / "keep.txt").write_text("keep")
    report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000,
                              sweep_temps=True)
    assert report.swept == 1
    assert not ours.exists()
    assert theirs.read_text() == "mine"
    assert (wt / "keep.txt").read_text() == "keep"
    assert report.files == 2          # a swept temp is not counted


def test_measure_worktree_does_not_sweep_unless_asked(wt: Path):
    from dirtywork import tools
    ours = wt / tools.tmp_name("app.py")
    ours.write_text("staged")
    report = measure_worktree(wt, max_bytes=10 * 1024 * 1024, max_files=1000)
    assert report.swept == 0
    assert ours.exists()
    assert report.files == 1
