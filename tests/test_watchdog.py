from __future__ import annotations

import time
from pathlib import Path

from dirtywork.sandbox.watchdog import Watchdog


def test_note_bash_start_and_end_toggle_in_flight():
    wdg = Watchdog(kill=lambda r: None, sample=lambda: (0, 0), storage_paths=[],
                    min_free_mb=1, max_worktree_mb=1, max_worktree_files=1)
    assert wdg._bash_in_flight is False
    wdg.note_bash_start()
    assert wdg._bash_in_flight is True
    wdg.note_bash_end()
    assert wdg._bash_in_flight is False


def test_check_worktree_budget_once_under_caps_no_kill():
    kills = []
    wdg = Watchdog(kill=lambda r: kills.append(r), sample=lambda: (1024, 5),
                    storage_paths=[], min_free_mb=1, max_worktree_mb=2048,
                    max_worktree_files=200_000)
    result = wdg.check_worktree_budget_once()
    assert result is False
    assert kills == []
    assert wdg.violation is None


def test_check_worktree_budget_once_over_mb_cap_kills():
    kills = []
    wdg = Watchdog(kill=lambda r: kills.append(r), sample=lambda: (3 * 1024 * 1024, 10),
                    storage_paths=[], min_free_mb=1, max_worktree_mb=2048,
                    max_worktree_files=200_000)
    result = wdg.check_worktree_budget_once()
    assert result is True
    assert kills and "worktree exceeds" in kills[0]
    assert wdg.violation == kills[0]


def test_check_worktree_budget_once_over_file_cap_kills():
    kills = []
    wdg = Watchdog(kill=lambda r: kills.append(r), sample=lambda: (10, 500_000),
                    storage_paths=[], min_free_mb=1, max_worktree_mb=2048,
                    max_worktree_files=200_000)
    result = wdg.check_worktree_budget_once()
    assert result is True
    assert kills and "worktree exceeds" in kills[0]


def test_run_loop_kills_on_disk_floor_breach(tmp_path, monkeypatch):
    import dirtywork.sandbox.watchdog as wd

    class FakeUsage:
        free = 100 * 1024 * 1024  # 100 MB, below the 2048 MB floor

    monkeypatch.setattr(wd.shutil, "disk_usage", lambda path: FakeUsage())

    kills = []
    wdg = wd.Watchdog(
        kill=lambda reason: kills.append(reason), sample=lambda: (0, 0),
        storage_paths=[tmp_path], min_free_mb=2048, max_worktree_mb=2048,
        max_worktree_files=200_000, clock=lambda: 0.0, sleep=lambda s: None,
    )

    wdg.run()  # call directly (not .start()) for deterministic single-thread testing

    assert kills == ["host free space below 2048 MB"]
    assert wdg.violation == "host free space below 2048 MB"


def test_run_loop_kills_on_worktree_over_cap_while_bash_in_flight(tmp_path, monkeypatch):
    import dirtywork.sandbox.watchdog as wd

    class FakeUsage:
        free = 10 * 1024 * 1024 * 1024  # 10 GB, plenty

    monkeypatch.setattr(wd.shutil, "disk_usage", lambda path: FakeUsage())

    kills = []
    clock = {"t": 0.0}
    wdg = wd.Watchdog(
        kill=lambda reason: kills.append(reason),
        sample=lambda: (3 * 1024 * 1024, 10),  # 3 GB, over the 2048 MB cap
        storage_paths=[tmp_path], min_free_mb=2048, max_worktree_mb=2048,
        max_worktree_files=200_000,
        clock=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
    )
    wdg.note_bash_start()

    wdg.run()

    assert kills and "worktree exceeds" in kills[0]


def test_run_loop_does_not_sample_worktree_when_no_bash_in_flight(tmp_path, monkeypatch):
    import dirtywork.sandbox.watchdog as wd

    class FakeUsage:
        free = 10 * 1024 * 1024 * 1024

    monkeypatch.setattr(wd.shutil, "disk_usage", lambda path: FakeUsage())

    sample_calls = []
    clock = {"t": 0.0}

    def fake_sample():
        sample_calls.append(1)
        return (3 * 1024 * 1024, 10)  # would violate if ever sampled

    stop_after = {"n": 20}

    def fake_sleep(s):
        clock["t"] += s
        stop_after["n"] -= 1
        if stop_after["n"] <= 0:
            wdg.stop()

    wdg = wd.Watchdog(
        kill=lambda reason: None, sample=fake_sample,
        storage_paths=[tmp_path], min_free_mb=2048, max_worktree_mb=2048,
        max_worktree_files=200_000, clock=lambda: clock["t"], sleep=fake_sleep,
    )
    # note_bash_start() is never called — no bash call in flight

    wdg.run()

    assert sample_calls == []


def test_stop_sets_stop_event_and_thread_exits(tmp_path, monkeypatch):
    import dirtywork.sandbox.watchdog as wd

    class FakeUsage:
        free = 10 * 1024 * 1024 * 1024

    monkeypatch.setattr(wd.shutil, "disk_usage", lambda path: FakeUsage())
    wdg = wd.Watchdog(kill=lambda r: None, sample=lambda: (0, 0), storage_paths=[tmp_path],
                       min_free_mb=1, max_worktree_mb=1, max_worktree_files=1,
                       sleep=lambda s: time.sleep(0.01))

    wdg.start()
    wdg.stop()
    wdg.join(timeout=2)

    assert not wdg.is_alive()


def test_disk_check_fails_closed_after_repeated_stat_errors(tmp_path, monkeypatch):
    import dirtywork.sandbox.watchdog as wd

    class FakeUsage:
        free = 10 * 1024 * 1024 * 1024

    def raising_disk_usage(path):
        raise OSError("boom")

    monkeypatch.setattr(wd.shutil, "disk_usage", raising_disk_usage)

    kills = []
    wdg = wd.Watchdog(
        kill=lambda reason: kills.append(reason), sample=lambda: (0, 0),
        storage_paths=[tmp_path], min_free_mb=2048, max_worktree_mb=2048,
        max_worktree_files=200_000, clock=lambda: 0.0, sleep=lambda s: None,
    )

    result1 = wdg._check_disk()
    assert result1 is False
    assert len(kills) == 0
    assert wdg._disk_check_failures == 1

    result2 = wdg._check_disk()
    assert result2 is False
    assert len(kills) == 0
    assert wdg._disk_check_failures == 2

    result3 = wdg._check_disk()
    assert result3 is True
    assert len(kills) == 1
    assert wdg.violation is not None
    assert "unmeasured" in wdg.violation


def test_run_loop_does_not_die_silently_when_sample_raises(tmp_path, monkeypatch):
    # Important #2: SandboxError (or any Exception) from sample()/_check_disk
    # must not propagate out of run() and kill the thread silently — it must
    # be converted into a recorded, killed violation so _after_bash can
    # surface it on the runner's next tool call.
    import dirtywork.sandbox.watchdog as wd

    class FakeUsage:
        free = 10 * 1024 * 1024 * 1024  # plenty, so disk check never fires

    monkeypatch.setattr(wd.shutil, "disk_usage", lambda path: FakeUsage())

    def raising_sample():
        raise RuntimeError("exec failed twice")

    kills = []
    clock = {"t": 0.0}
    wdg = wd.Watchdog(
        kill=lambda reason: kills.append(reason), sample=raising_sample,
        storage_paths=[tmp_path], min_free_mb=1, max_worktree_mb=1,
        max_worktree_files=1, clock=lambda: clock["t"],
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
    )
    wdg.note_bash_start()

    wdg.run()  # must not raise

    assert wdg.violation is not None
    assert "exec failed twice" in wdg.violation
    assert kills and "exec failed twice" in kills[0]


def test_disk_check_failure_counter_resets_on_success(tmp_path, monkeypatch):
    import dirtywork.sandbox.watchdog as wd

    class FakeUsage:
        free = 10 * 1024 * 1024 * 1024

    def raising_disk_usage(path):
        raise OSError("boom")

    monkeypatch.setattr(wd.shutil, "disk_usage", raising_disk_usage)

    kills = []
    wdg = wd.Watchdog(
        kill=lambda reason: kills.append(reason), sample=lambda: (0, 0),
        storage_paths=[tmp_path], min_free_mb=2048, max_worktree_mb=2048,
        max_worktree_files=200_000, clock=lambda: 0.0, sleep=lambda s: None,
    )

    # Two failing calls
    result1 = wdg._check_disk()
    assert result1 is False
    assert wdg._disk_check_failures == 1

    result2 = wdg._check_disk()
    assert result2 is False
    assert wdg._disk_check_failures == 2

    # Restore disk_usage to a plentiful value
    monkeypatch.setattr(wd.shutil, "disk_usage", lambda path: FakeUsage())

    # Now it should succeed and reset counter
    result3 = wdg._check_disk()
    assert result3 is False  # free space is plenty
    assert wdg._disk_check_failures == 0
