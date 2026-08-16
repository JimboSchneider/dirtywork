from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from dirtywork.rundir import RunDirError, create_run_dir, ensure_runs_dir


def test_ensure_runs_dir_creates_0700_dirs(tmp_path: Path):
    # ensure_runs_dir only creates its two direct levels (mirroring the real
    # ~/.dirtywork/runs, where ~ always already exists) — the stand-in "home"
    # directory itself must exist before the call, same as a real $HOME.
    home = tmp_path / "home"
    home.mkdir()
    runs = home / ".dirtywork" / "runs"
    result = ensure_runs_dir(runs)
    assert result == runs
    assert stat.S_IMODE((home / ".dirtywork").stat().st_mode) == 0o700
    assert stat.S_IMODE(runs.stat().st_mode) == 0o700


def test_ensure_runs_dir_idempotent(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    runs = home / ".dirtywork" / "runs"
    ensure_runs_dir(runs)
    ensure_runs_dir(runs)  # second call must not raise


def test_ensure_runs_dir_symlink_raises(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / ".dirtywork").mkdir()
    (home / ".dirtywork" / "runs").symlink_to(outside)
    with pytest.raises(RunDirError):
        ensure_runs_dir(home / ".dirtywork" / "runs")


def test_ensure_runs_dir_wrong_owner_raises(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    runs = home / ".dirtywork" / "runs"
    ensure_runs_dir(runs)  # create it as the real user first
    real_getuid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real_getuid + 1)
    with pytest.raises(RunDirError):
        ensure_runs_dir(runs)


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX only")
def test_ensure_runs_dir_tightens_loose_perms(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    dirtywork_dir = home / ".dirtywork"
    dirtywork_dir.mkdir()
    os.chmod(dirtywork_dir, 0o755)  # after mkdir, to defeat umask
    runs = dirtywork_dir / "runs"
    runs.mkdir()
    os.chmod(runs, 0o777)

    ensure_runs_dir(runs)

    assert stat.S_IMODE(dirtywork_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(runs.stat().st_mode) == 0o700


def test_create_run_dir(tmp_path: Path):
    runs = tmp_path / "runs"
    runs.mkdir()
    run_dir = create_run_dir(runs, "some-slug")
    assert run_dir == runs / "some-slug"
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700


def test_create_run_dir_refuses_existing_dir(tmp_path: Path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "dup-slug").mkdir()
    with pytest.raises(RunDirError):
        create_run_dir(runs, "dup-slug")


def test_create_run_dir_refuses_existing_symlink(tmp_path: Path):
    runs = tmp_path / "runs"
    runs.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (runs / "dup-slug").symlink_to(outside)
    with pytest.raises(RunDirError):
        create_run_dir(runs, "dup-slug")
