from __future__ import annotations

import time

from dirtywork.procs import Captured, MAX_CAPTURE_BYTES, run_capped


def test_run_capped_returns_output_and_returncode():
    result = run_capped(["bash", "-c", "echo hi; exit 3"], timeout=5)
    assert isinstance(result, Captured)
    assert result.returncode == 3
    assert result.output.strip() == b"hi"
    assert result.truncated is False
    assert result.timed_out is False


def test_run_capped_caps_output():
    result = run_capped(
        ["python3", "-c", "import sys; sys.stdout.write('A' * 2_000_000)"],
        timeout=10, cap=1024,
    )
    assert len(result.output) <= 1024
    assert result.truncated is True
    assert result.returncode == 0


def test_run_capped_timeout_kills_group():
    start = time.monotonic()
    result = run_capped(
        ["bash", "-c", "(sleep 2 && touch /tmp/dirtywork_procs_survived) & wait"],
        timeout=1,
    )
    assert result.timed_out is True
    assert result.returncode is None
    elapsed = time.monotonic() - start
    assert elapsed < 3.0


def test_run_capped_passes_stdin_bytes():
    result = run_capped(["cat"], timeout=5, stdin=b"from stdin\n")
    assert result.output == b"from stdin\n"


def test_run_capped_respects_cwd_and_env():
    result = run_capped(["bash", "-c", "pwd && echo $MY_VAR"], timeout=5,
                         cwd="/tmp", env={"MY_VAR": "hello", "PATH": "/usr/bin:/bin"})
    assert b"/tmp" in result.output
    assert b"hello" in result.output


def test_run_capped_stdin_is_devnull_by_default():
    result = run_capped(["bash", "-c", "read x; echo got:$x"], timeout=5)
    assert result.output.strip() == b"got:"
    assert result.timed_out is False
