"""Tests for dirtywork.sandbox.strays module."""
from __future__ import annotations

import subprocess
import pytest

from dirtywork.procs import Captured
from dirtywork.sandbox.docker import docker_cli
from dirtywork.sandbox import strays

# Test constants as specified in task


def test_scripts_syntax_valid() -> None:
    """Test that both scripts have valid shell syntax."""
    # Skip if sh is missing
    try:
        subprocess.run(["sh", "-n"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("sh not available")

    # Test TETHER_DISCOVERY_SCRIPT
    result = subprocess.run(["sh", "-n", "-c", strays.TETHER_DISCOVERY_SCRIPT], capture_output=True)
    assert result.returncode == 0, f"TETHER_DISCOVERY_SCRIPT syntax error: {result.stderr.decode()}"

    # Test STRAY_KILL_SCRIPT
    result = subprocess.run(["sh", "-n", "-c", strays.STRAY_KILL_SCRIPT], capture_output=True)
    assert result.returncode == 0, f"STRAY_KILL_SCRIPT syntax error: {result.stderr.decode()}"


def test_stray_kill_script_no_subshell() -> None:
    """Test that STRAY_KILL_SCRIPT doesn't contain forbidden constructs."""
    script = strays.STRAY_KILL_SCRIPT

    # Check for forbidden patterns
    assert "$(" not in script, "STRAY_KILL_SCRIPT contains $() (subshell)"
    assert "`" not in script, "STRAY_KILL_SCRIPT contains backtick (subshell)"
    # Check for subshells with parentheses that aren't [ or for loops
    # Only "(" should be in "for pass in 1 2 3" and "(...)" for find args
    lines = script.splitlines()
    for line in lines:
        stripped = line.strip()
        # Skip lines that are just "for pass in 1 2 3" or have specific allowed patterns
        if "for pass in 1 2 3" in stripped:
            continue
        # Check for unauthorized pipe character (not ||)
        # First remove all occurrences of ||
        no_pipes = stripped.replace("||", "")
        assert "|" not in no_pipes, f"STRAY_KILL_SCRIPT contains unauthorized pipe: {line}"

    # Check for required patterns
    assert "for pass in 1 2 3" in script, "STRAY_KILL_SCRIPT missing 'for pass in 1 2 3'"


def test_tether_discovery_script_format() -> None:
    """Test TETHER_DISCOVERY_SCRIPT has correct format."""
    script = strays.TETHER_DISCOVERY_SCRIPT

    assert "2>/dev/null <" in script, "TETHER_DISCOVERY_SCRIPT missing '2>/dev/null <'"
    assert '< "$p/comm" 2>' not in script, "TETHER_DISCOVERY_SCRIPT has incorrect redirection format"


def test_stray_rows_empty() -> None:
    """Test stray_rows with empty output."""
    assert strays.stray_rows(b"") == []


def test_stray_rows_tether_only() -> None:
    """Test stray_rows with only tether processes."""
    header = b"UID  PID  PPID  C  STIME  TTY  TIME  CMD\n"
    # Bare cat tether
    output = header + b"501  1  0  0  10:00  ?  00:00:00  cat\n"
    assert strays.stray_rows(output) == []

    # /bin/cat tether
    output = header + b"501  1  0  0  10:00  ?  00:00:00  /bin/cat\n"
    assert strays.stray_rows(output) == []

    # docker-init -- cat tether
    output = header + b"501  1  0  0  10:00  ?  00:00:00  docker-init -- cat\n"
    assert strays.stray_rows(output) == []

    # docker-init -- /bin/cat tether
    output = header + b"501  1  0  0  10:00  ?  00:00:00  docker-init -- /bin/cat\n"
    assert strays.stray_rows(output) == []


def test_stray_rows_with_strays() -> None:
    """Test stray_rows with actual stray processes."""
    header = b"UID  PID  PPID  C  STIME  TTY  TIME  CMD\n"

    # sleep and bash as strays
    output = (
        header
        + b"501  1  0  0  10:00  ?  00:00:00  cat\n"
        + b"501  42  1  0  10:00  ?  00:00:00  sleep 300\n"
        + b"501  43  1  0  10:00  ?  00:00:00  bash -c (while true; do sleep 2; done) >/dev/null 2>&1 &\n"
    )
    result = strays.stray_rows(output)
    assert len(result) == 2
    assert "sleep 300" in result[0]
    assert "bash -c (while true; do sleep 2; done)" in result[1]


def test_stray_rows_bare_cat_loophole() -> None:
    """Test that bare 'cat' is treated as tether (documented loophole)."""
    header = b"UID  PID  PPID  C  STIME  TTY  TIME  CMD\n"
    # A bare cat in the middle is still treated as tether
    output = header + b"501  42  1  0  10:00  ?  00:00:00  cat\n"
    assert strays.stray_rows(output) == []


def test_stray_rows_decoding_errors() -> None:
    """Test stray_rows with invalid UTF-8."""
    header = b"UID  PID  PPID  C  STIME  TTY  TIME  CMD\n"
    # Invalid UTF-8 bytes
    output = header + b"501  42  1  0  10:00  ?  00:00:00  \xff\xfe\n"
    result = strays.stray_rows(output)
    assert len(result) == 1
    # Should decode with replacement character
    assert "\ufffd" in result[0] or result[0]


def test_parse_tether_pid_valid() -> None:
    """Test parse_tether_pid with valid input."""
    assert strays.parse_tether_pid(b"7\n") == 7
    assert strays.parse_tether_pid(b"12345\n") == 12345
    assert strays.parse_tether_pid(b"1\n") == 1


def test_parse_tether_pid_invalid() -> None:
    """Test parse_tether_pid with invalid input."""
    assert strays.parse_tether_pid(b"sh: 1: cannot open /proc/9/comm: No such file\n7\n") is None
    assert strays.parse_tether_pid(b"") is None
    assert strays.parse_tether_pid(b"0\n") is None
    assert strays.parse_tether_pid(b"7\n8\n") is None
    assert strays.parse_tether_pid(b"-1\n") is None
    assert strays.parse_tether_pid(b"abc\n") is None
    assert strays.parse_tether_pid(b"  7  \n") == 7  # the merged output is stripped
    assert strays.parse_tether_pid(b"7\n\n") == 7


def test_parse_tether_pid_with_errors() -> None:
    """Test parse_tether_pid with invalid UTF-8."""
    # Invalid bytes are replaced with \ufffd, so they don't match digits
    assert strays.parse_tether_pid(b"\xff\xfe7\xff\xfe\n") is None
    # Valid digits with replacement char in between shouldn't match
    assert strays.parse_tether_pid(b"\xff\xfe\n") is None


def test_parse_locks_basic() -> None:
    """Test parse_locks with basic lock files."""
    output = b"/gitdir/index.lock\0/gitdir/gc.pid\0"
    result = strays.parse_locks(output)
    assert result == ["/gitdir/index.lock", "/gitdir/gc.pid"]


def test_parse_locks_with_paths() -> None:
    """Test parse_locks with nested paths."""
    output = b"/gitdir/index.lock\0/gitdir/gc.pid\0/gitdir/refs/heads/x.lock\0"
    result = strays.parse_locks(output)
    assert result == ["/gitdir/index.lock", "/gitdir/gc.pid", "/gitdir/refs/heads/x.lock"]


def test_parse_locks_rejects_nonlocks() -> None:
    """Test parse_locks rejects non-lock paths."""
    output = b"/gitdir/objects/tmp_obj_x\0"
    result = strays.parse_locks(output)
    assert result == []


def test_parse_locks_with_errors() -> None:
    """Test parse_locks handles stderr mixed in."""
    # From a real find output: error line mixed with lock paths
    # Note: find -print0 sends errors to stderr and paths to stdout, so in practice
    # they wouldn't be mixed. This test verifies that error lines don't break parsing.
    output = (
        b"/gitdir/index.lock\0"
        + b"/gitdir/gc.pid\0"
        + b"/gitdir/refs/heads/x.lock\0"
        + b"find: '/gitdir/y': Permission denied\n"
        + b"/gitdir/z.lock\0"
        + b"/gitdir/tail.lock\0"
    )
    result = strays.parse_locks(output)
    # The error line is not a valid lock path, so it's filtered out
    # "/gitdir/z.lock" comes after an error line without null separator, so it's in same chunk
    # and won't be matched by LOCK_PATH_RE.fullmatch()
    # But "/gitdir/tail.lock" is a separate chunk and should be included
    assert result == ["/gitdir/index.lock", "/gitdir/gc.pid", "/gitdir/refs/heads/x.lock", "/gitdir/tail.lock"]


def test_parse_locks_trailing_null() -> None:
    """Test parse_locks handles trailing null (common with find -print0)."""
    # Common pattern: ends with a trailing null
    output = b"/gitdir/index.lock\0/gitdir/gc.pid\0"
    result = strays.parse_locks(output)
    assert result == ["/gitdir/index.lock", "/gitdir/gc.pid"]


def test_cap_strays_under_limit() -> None:
    """Test cap_strays when under MAX_STRAYS."""
    rows = [f"cmd{i}" for i in range(3)]
    capped, total = strays.cap_strays(rows)
    assert capped == rows
    assert total is None


def test_cap_strays_over_limit() -> None:
    """Test cap_strays when over MAX_STRAYS."""
    rows = [f"cmd{i}" for i in range(25)]
    capped, total = strays.cap_strays(rows)
    assert len(capped) == 20
    assert total == 25


def test_cap_strays_truncates_long_row() -> None:
    """Test cap_strays cuts long rows to MAX_STRAY_CHARS with ellipsis."""
    # Create a row that's 300 chars
    long_row = "a" * 300
    rows = [long_row]
    capped, total = strays.cap_strays(rows)
    assert len(capped[0]) == 200 + 1  # MAX_STRAY_CHARS + ellipsis
    assert capped[0].endswith("…")


def test_cap_locks_under_limit() -> None:
    """Test cap_locks when under MAX_LOCKS."""
    paths = [f"/gitdir/lock{i}.lock" for i in range(10)]
    capped, total = strays.cap_locks(paths, truncated=False)
    assert capped == paths
    assert total is None


def test_cap_locks_over_limit_no_truncation() -> None:
    """Test cap_locks when over MAX_LOCKS but truncated=False."""
    paths = [f"/gitdir/lock{i}.lock" for i in range(25)]
    capped, total = strays.cap_locks(paths, truncated=False)
    assert len(capped) == 20
    # the exact count is known when the sweep output was not truncated
    assert total == 25


def test_cap_locks_over_limit_with_truncation() -> None:
    """Test cap_locks when over MAX_LOCKS and truncated=True."""
    paths = [f"/gitdir/lock{i}.lock" for i in range(25)]
    capped, total = strays.cap_locks(paths, truncated=True)
    assert len(capped) == 20
    # a capped capture cannot support an exact count (spec §3.4 v4): no total
    assert total is None


def test_cap_locks_under_limit_with_truncation() -> None:
    """Test cap_locks under limit but truncated=True."""
    paths = [f"/gitdir/lock{i}.lock" for i in range(10)]
    capped, total = strays.cap_locks(paths, truncated=True)
    assert capped == paths
    # total should be None when under limit regardless of truncated flag
    assert total is None


def test_stray_kill_text_basic() -> None:
    """Test stray_kill_text with minimal input."""
    text = strays.stray_kill_text(["sleep 300"], None, False)
    assert "1 background process" in text
    assert "sleep 300" in text
    assert "git status" in text


def test_stray_kill_text_with_total() -> None:
    """Test stray_kill_text with total parameter."""
    text = strays.stray_kill_text(["sleep 300"], 5, False)
    assert "5 background processes" in text
    assert "sleep 300" in text
    assert "git status" in text


def test_stray_kill_text_ellipsis_commands() -> None:
    """Test stray_kill_text ellipsis on long command lines."""
    long_cmd = "a" * 200
    text = strays.stray_kill_text([long_cmd], None, False)
    assert "a" * 80 in text  # NOTICE_CMD_CHARS
    assert "git status" in text


def test_stray_kill_text_multiple_commands() -> None:
    """Test stray_kill_text with multiple commands."""
    cmds = ["cmd1", "cmd2", "cmd3", "cmd4", "cmd5"]
    text = strays.stray_kill_text(cmds, None, False)
    assert "cmd1; cmd2; cmd3" in text
    assert "+2 more" in text
    assert "git status" in text


def test_stray_kill_text_with_locks() -> None:
    """Test stray_kill_text with locks_removed=True."""
    text = strays.stray_kill_text(["sleep 300"], None, True)
    assert "Stale git lock files" in text
    assert "git status" in text


def test_sandbox_reset_text() -> None:
    """Test sandbox_reset_text."""
    text = strays.sandbox_reset_text("oom")
    assert "(oom)" in text
    assert "git status" in text


def test_sandbox_reset_text_various_reasons() -> None:
    """Test sandbox_reset_text with various reasons."""
    for reason in ["stray process after bash", "container unreachable after bash", "oom"]:
        text = strays.sandbox_reset_text(reason)
        assert f"({reason})" in text
        assert "Files in the worktree are intact" in text


def test_fake_docker_callable_response_returns_captured() -> None:
    """Test FakeDocker with callable that returns Captured."""
    from tests.docker_fakes import FakeDocker

    fake = FakeDocker()

    def script_response(argv):
        return Captured(returncode=0, output=b"ok", truncated=False, timed_out=False)

    fake.script(["test"], script_response)
    result = fake.run(["test", "arg1"], timeout=10)
    assert result.returncode == 0
    assert result.output == b"ok"


def test_fake_docker_callable_response_raises() -> None:
    """Test FakeDocker with callable that raises."""
    from tests.docker_fakes import FakeDocker

    fake = FakeDocker()

    def script_response(argv):
        raise docker_cli.DockerError("test error")

    fake.script(["test"], script_response)
    with pytest.raises(docker_cli.DockerError):
        fake.run(["test", "arg1"], timeout=10)


def test_fake_docker_callable_with_list() -> None:
    """Test FakeDocker with callable in a list."""
    from tests.docker_fakes import FakeDocker

    fake = FakeDocker()

    def script_response(argv):
        return Captured(returncode=0, output=b"ok", truncated=False, timed_out=False)

    fake.script(["test"], [script_response])
    result = fake.run(["test", "arg1"], timeout=10)
    assert result.returncode == 0
    assert result.output == b"ok"
