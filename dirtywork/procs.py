from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass

MAX_CAPTURE_BYTES = 1024 * 1024


@dataclass
class Captured:
    returncode: int | None
    output: bytes
    truncated: bool
    timed_out: bool


def _kill_group(pid: int) -> None:
    """SIGKILL the whole process group led by pid (a no-op if already gone).

    On the clean-exit path pid is already reaped, so there is a negligible
    PID-reuse window; it would only signal an unrelated process that had both
    become a group leader AND reclaimed this exact pgid, which we accept.
    """
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def run_capped(argv: list[str], *, timeout: float, cwd=None, env=None,
               stdin: bytes | None = None, cap: int = MAX_CAPTURE_BYTES,
               kill_group: bool = True) -> Captured:
    """Run argv, capturing merged stdout+stderr up to `cap` bytes without
    buffering the whole stream in memory (a drain thread keeps the child's
    pipe from filling, even past the cap), and enforce `timeout` by killing
    the whole process group so backgrounded children cannot outlive the call.
    """
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=kill_group,
        )
    except OSError as e:
        return Captured(returncode=None, output=str(e).encode(), truncated=False,
                         timed_out=False)

    captured = bytearray()
    truncated = False
    lock = threading.Lock()

    def _drain() -> None:
        nonlocal truncated
        with proc.stdout:  # type: ignore[union-attr]
            for chunk in iter(lambda: proc.stdout.read(65536), b""):  # type: ignore[union-attr]
                with lock:
                    room = cap - len(captured)
                    if room > 0:
                        captured.extend(chunk[:room])
                    if len(chunk) > room:
                        truncated = True  # keep draining so the child never blocks

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()

    if stdin is not None:
        def _feed() -> None:
            try:
                proc.stdin.write(stdin)  # type: ignore[union-attr]
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    proc.stdin.close()  # type: ignore[union-attr]
                except OSError:
                    pass

        writer = threading.Thread(target=_feed, daemon=True)
        writer.start()
    else:
        writer = None

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True

    if kill_group:
        _kill_group(proc.pid)
    else:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    reader.join(timeout=5)
    if writer is not None:
        writer.join(timeout=5)

    with lock:
        out = bytes(captured)
        trunc = truncated
    returncode = None if timed_out else proc.returncode
    return Captured(returncode=returncode, output=out, truncated=trunc, timed_out=timed_out)
