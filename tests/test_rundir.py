from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from dirtywork.rundir import (RunDirError, create_run_dir, ensure_bench_dir, ensure_runs_dir,
                              read_run_json, write_run_json)


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


def test_ensure_bench_dir_creates_0700_dirs(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    bench = home / ".dirtywork" / "bench"
    result = ensure_bench_dir(bench)
    assert result == bench
    assert stat.S_IMODE((home / ".dirtywork").stat().st_mode) == 0o700
    assert stat.S_IMODE(bench.stat().st_mode) == 0o700


def test_ensure_bench_dir_idempotent(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    bench = home / ".dirtywork" / "bench"
    ensure_bench_dir(bench)
    ensure_bench_dir(bench)  # second call must not raise


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


def test_write_run_json_creates_file_mode_0600(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    write_run_json(run_dir, {"status": "running", "slug": "x"})
    p = run_dir / "run.json"
    assert p.exists()
    mode = stat.S_IMODE(os.stat(p).st_mode)
    assert mode == 0o600


def test_write_run_json_then_read_round_trips(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    data = {"status": "running", "slug": "x", "nested": {"a": 1}}
    write_run_json(run_dir, data)
    assert read_run_json(run_dir) == data


def test_write_run_json_overwrite_is_atomic_and_leaves_no_temp_file(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    write_run_json(run_dir, {"status": "running"})
    write_run_json(run_dir, {"status": "completed", "ended": "later"})
    assert read_run_json(run_dir) == {"status": "completed", "ended": "later"}
    assert not any(p.name.endswith(".tmp") for p in run_dir.iterdir())


def test_read_run_json_missing_raises_oserror(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    with pytest.raises(OSError):
        read_run_json(run_dir)


def test_run_dir_for_returns_the_managed_directory(tmp_path: Path):
    from dirtywork.rundir import run_dir_for
    runs = tmp_path / "runs"
    runs.mkdir()
    assert run_dir_for("2026-08-23-abc123", runs) == runs / "2026-08-23-abc123"


@pytest.mark.parametrize("slug", ["../escape", "/etc", ".", "..", "", "a/b", "-leading"])
def test_run_dir_for_refuses_a_slug_that_could_name_a_path(tmp_path: Path, slug):
    from dirtywork.rundir import run_dir_for
    runs = tmp_path / "runs"
    runs.mkdir()
    with pytest.raises(RunDirError) as excinfo:
        run_dir_for(slug, runs)
    assert f"invalid run slug '{slug}'" in str(excinfo.value)


def test_writer_skips_an_existing_hardlinked_temp_candidate(tmp_path, monkeypatch):
    from dirtywork import rundir
    victim = tmp_path / 'victim'
    victim.write_text('do not truncate')
    candidate = tmp_path / '.run.json.collision.tmp'
    os.link(victim, candidate)
    tokens = iter(['collision', 'fresh'])
    monkeypatch.setattr(rundir.secrets, 'token_hex', lambda *a: next(tokens))
    rundir.write_run_json(tmp_path, {'status': 'done'})
    assert victim.read_text() == 'do not truncate'
    assert rundir.read_run_json(tmp_path) == {'status': 'done'}


def test_writer_rejects_stale_expected_signature(tmp_path):
    from dirtywork import rundir
    original = {'status': 'completed'}
    rundir.write_run_json(tmp_path, original)
    signature = rundir.run_json_signature(original)
    rundir.write_run_json(tmp_path, {**original, 'verdict': 'accepted'})
    with pytest.raises(rundir.RunJsonConflict):
        rundir.write_run_json(tmp_path, original, expected_signature=signature)
    assert rundir.read_run_json(tmp_path)['verdict'] == 'accepted'


def test_concurrent_merge_updates_preserve_both_fields(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from dirtywork import rundir
    rundir.write_run_json(tmp_path, {'status': 'completed'})
    barrier = Barrier(2)
    def update(key, value):
        first_attempt = True
        def transform(current):
            nonlocal first_attempt
            if first_attempt:
                first_attempt = False
                barrier.wait(timeout=5)
            return {**current, key: value}
        return rundir.update_run_json(tmp_path, transform)
    with ThreadPoolExecutor(max_workers=2) as pool:
        verdict = pool.submit(update, 'verdict', 'accepted')
        exported = pool.submit(update, 'export_status', 'ok')
        verdict.result(timeout=10)
        exported.result(timeout=10)
    assert rundir.read_run_json(tmp_path) == {
        'status': 'completed', 'verdict': 'accepted', 'export_status': 'ok'}


def test_metadata_lock_is_released_when_writer_process_dies(tmp_path):
    import subprocess
    import sys
    from dirtywork import rundir
    child = subprocess.Popen([
        sys.executable, '-c',
        'from pathlib import Path; import sys,time; '
        'from dirtywork.rundir import _run_json_lock; '
        'lock=_run_json_lock(Path(sys.argv[1])); lock.__enter__(); '
        'print("locked",flush=True); time.sleep(30)', str(tmp_path)],
        stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'locked'
    finally:
        child.terminate()
        child.wait(timeout=5)
    rundir.write_run_json(tmp_path, {'status': 'completed'})
    assert rundir.read_run_json(tmp_path)['status'] == 'completed'


def test_metadata_lock_refuses_hardlink_without_touching_target(tmp_path):
    from dirtywork import rundir
    victim = tmp_path / 'victim'
    victim.write_text('untouched')
    os.link(victim, tmp_path / '.run.json.lock')
    with pytest.raises(OSError, match='unsafe run metadata lock'):
        rundir.write_run_json(tmp_path, {'status': 'done'})
    assert victim.read_text() == 'untouched'
