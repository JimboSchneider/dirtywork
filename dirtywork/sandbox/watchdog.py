from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Callable


MAX_DISK_CHECK_FAILURES = 3  # consecutive stat failures before the unmeasurable bound is treated as violated


class Watchdog(threading.Thread):
    """Background thread for the container's whole lifetime (spec §6):
    every 0.5s, the smaller of the host free-space across `storage_paths`
    is compared to `min_free_mb`; every 5s while a bash call is in flight,
    `sample()` (worktree kbytes, entry count) is compared to
    `max_worktree_mb`/`max_worktree_files`. A breach calls `kill(reason)`
    and records `.violation`, then the loop returns (the container is dead;
    nothing more to watch until the sandbox resets or stops it).

    The synchronous post-bash-call sample the spec also requires is NOT run
    by this thread — DockerSandbox calls `check_worktree_budget_once()`
    directly right after every bash call returns, so that check happens exactly
    when the spec says to, independent of this thread's own timer.
    """

    DISK_POLL_INTERVAL = 0.5
    WORKTREE_POLL_INTERVAL = 5.0

    def __init__(self, kill: Callable, sample: Callable, storage_paths: list, *,
                 min_free_mb: int, max_worktree_mb: int, max_worktree_files: int,
                 clock=time.monotonic, sleep=time.sleep):
        super().__init__(daemon=True)
        self.kill = kill
        self.sample = sample
        self.storage_paths = list(storage_paths)
        self.min_free_mb = min_free_mb
        self.max_worktree_mb = max_worktree_mb
        self.max_worktree_files = max_worktree_files
        self.clock = clock
        self.sleep = sleep
        self.violation: str | None = None
        self._bash_in_flight = False
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._disk_check_failures = 0

    def note_bash_start(self) -> None:
        with self._lock:
            self._bash_in_flight = True

    def note_bash_end(self) -> None:
        with self._lock:
            self._bash_in_flight = False

    def stop(self) -> None:
        self._stop_event.set()

    def _check_disk(self) -> bool:
        try:
            free_mb = min(shutil.disk_usage(str(p)).free for p in self.storage_paths) / (1024 * 1024)
        except (OSError, ValueError) as e:
            # A bound we cannot measure is a bound we are not enforcing: tolerate
            # transient stat failures, but fail CLOSED after a few in a row.
            self._disk_check_failures += 1
            if self._disk_check_failures >= MAX_DISK_CHECK_FAILURES:
                reason = f"host free-space check failing ({e!s}); refusing to run unmeasured"
                self.violation = reason
                self.kill(reason)
                return True
            return False
        self._disk_check_failures = 0
        if free_mb < self.min_free_mb:
            reason = f"host free space below {self.min_free_mb} MB"
            self.violation = reason
            self.kill(reason)
            return True
        return False

    def check_worktree_budget_once(self) -> bool:
        """One worktree-size sample-and-check. Called by this thread's own
        loop (every 5s while a bash call is in flight) AND, synchronously,
        by DockerSandbox right after every bash call returns."""
        kbytes, entries = self.sample()
        mb = kbytes / 1024
        if mb > self.max_worktree_mb or entries > self.max_worktree_files:
            reason = (
                f"worktree exceeds {self.max_worktree_mb} MB or "
                f"{self.max_worktree_files} files (sampled {mb:.1f} MB, {entries} files)"
            )
            self.violation = reason
            self.kill(reason)
            return True
        return False

    def run(self) -> None:
        last_worktree_check = self.clock()
        while not self._stop_event.is_set():
            if self._check_disk():
                return
            with self._lock:
                in_flight = self._bash_in_flight
            if in_flight and self.clock() - last_worktree_check >= self.WORKTREE_POLL_INTERVAL:
                last_worktree_check = self.clock()
                if self.check_worktree_budget_once():
                    return
            self.sleep(self.DISK_POLL_INTERVAL)
