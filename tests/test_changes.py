"""Tests for dirtywork.changes (spec #66 §4.1, §4.3, §4.4)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from dirtywork.changes import (
    FINGERPRINT_SCRIPT,
    DEFAULT_NO_CHANGE_TURNS,
    MAX_REASON_CHARS,
    UNCHANGED_PLAIN,
    UNCHANGED_REQUIRED,
    _reason,
    fingerprint,
    parse_fingerprint,
)
from dirtywork.changes import FINGERPRINT_SCRIPT as FINGERPRINT_SCRIPT_REF
from dirtywork.guardrails import check_bash_command
from dirtywork.sandbox.host import HostSandbox

from .provider_doubles import FingerprintSandbox


def test_parse_fingerprint_sorts_and_requires_two_hashes():
    # two hex lines → the sorted join
    result = "exit code: 0\n" + "b" * 40 + "\n" + "a" * 40
    fp, reason = parse_fingerprint(result)
    assert reason is None
    assert fp == "a" * 40 + "\n" + "b" * 40

    # the same four lines given in two different orders → equal fingerprints
    r1 = "exit code: 0\n" + "d" * 40 + "\n" + "a" * 40 + "\n" + "c" * 40 + "\n" + "b" * 40
    r2 = "exit code: 0\n" + "b" * 40 + "\n" + "c" * 40 + "\n" + "a" * 40 + "\n" + "d" * 40
    fp1, _ = parse_fingerprint(r1)
    fp2, _ = parse_fingerprint(r2)
    assert fp1 == fp2

    # "exit code: 0\n" + one hex line → (None, "fewer than two hash lines")
    result = "exit code: 0\n" + "a" * 40
    fp, reason = parse_fingerprint(result)
    assert fp is None
    assert reason == "fewer than two hash lines"

    # "" → (None, "no output")
    fp, reason = parse_fingerprint("")
    assert fp is None
    assert reason == "no output"


def test_parse_fingerprint_ignores_rc0_warnings():
    # "exit code: 0\nwarning: something\n<h1>\n<h2>" → the two hashes joined
    result = "exit code: 0\nwarning: something\n" + "a" * 40 + "\n" + "b" * 40
    fp, reason = parse_fingerprint(result)
    assert reason is None
    assert fp == "a" * 40 + "\n" + "b" * 40


def test_parse_fingerprint_fails_open_with_a_reason():
    # "exit code: 1\nerror: 'vendor/x/' does not have a commit checked out\n<h1>" → (None, "error: 'vendor/x/' does not have a commit checked out")
    result = "exit code: 1\nerror: 'vendor/x/' does not have a commit checked out\n" + "a" * 40
    fp, reason = parse_fingerprint(result)
    assert fp is None
    assert "error: 'vendor/x/'" in reason

    # "exit code: 128\n" → (None, "exit code: 128")
    result = "exit code: 128\n"
    fp, reason = parse_fingerprint(result)
    assert fp is None
    assert reason == "exit code: 128"

    # tools.timeout_result(60) → (None, a string starting with "ERROR: command timed out after")
    from dirtywork.tools import timeout_result
    result = timeout_result(60)
    fp, reason = parse_fingerprint(result)
    assert fp is None
    assert reason.startswith("ERROR: command timed out after")

    # "ERROR: bash failed: no such container" → (None, that line)
    result = "ERROR: bash failed: no such container"
    fp, reason = parse_fingerprint(result)
    assert fp is None
    assert reason == "ERROR: bash failed: no such container"

    # "BLOCKED: sudo is not allowed…" → (None, that line)
    result = "BLOCKED: sudo is not allowed"
    fp, reason = parse_fingerprint(result)
    assert fp is None
    assert reason == "BLOCKED: sudo is not allowed"

    # "exit code: 0\n" + 240 hex lines + "\n[output truncated at 10000 chars — bash output capped]" → (None, "[output truncated at 10000 chars — bash output capped]")
    hex_lines = "\n".join("a" * 40 for _ in range(240))
    capped = f"exit code: 0\n{hex_lines}\n[output truncated at 10000 chars — bash output capped]"
    fp, reason = parse_fingerprint(capped)
    assert fp is None
    assert "[output truncated at 10000 chars" in reason

    # a 500-char diagnostic → reason of length 200
    diag_500 = "x" * 500
    result = f"exit code: 1\n{diag_500}\n" + "a" * 40
    fp, reason = parse_fingerprint(result)
    assert fp is None
    assert len(reason) == 200


def test_fingerprint_without_bash():
    # fingerprint(object()) == (None, "sandbox has no bash")
    fp, reason = fingerprint(object())
    assert fp is None
    assert reason == "sandbox has no bash"


def test_script_shape_and_guardrails():
    # FINGERPRINT_SCRIPT contains each of the required substrings
    required = [
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        ":(exclude,literal)",
        "trap 'rm -rf \"$tmp\"' EXIT",
        "git -c core.fsmonitor=false add -A -- .",
        "git write-tree",
        "git rev-parse HEAD",
        "GIT_CONFIG_GLOBAL=/dev/null",
    ]
    for substr in required:
        assert substr in FINGERPRINT_SCRIPT

    # guardrails.check_bash_command(FINGERPRINT_SCRIPT, worktree=tmp_path) is None
    # and check_bash_command(FINGERPRINT_SCRIPT, sandboxed=True) is None.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp)
        assert check_bash_command(FINGERPRINT_SCRIPT, worktree=worktree) is None
        assert check_bash_command(FINGERPRINT_SCRIPT, sandboxed=True) is None


def test_fingerprint_sandbox_double(tmp_path: Path):
    # FingerprintSandbox(tmp_path, ["a"*40, None, RuntimeError("x")]).bash(FINGERPRINT_SCRIPT, 60)
    # returns "exit code: 0\n" + "a"*40 + "\n" + "0"*40 the first time,
    # "exit code: 1\nerror: boom" the second, raises RuntimeError the third
    # and every time after; a non-hex str entry raises AssertionError at construction;
    # commands records (FINGERPRINT_SCRIPT, 60).
    sandbox = FingerprintSandbox(tmp_path, hashes=["a" * 40, None, RuntimeError("x")])
    sandbox.start(tmp_path, tmp_path / ".git", "test-repo", "abc123")

    # First call
    result = sandbox.bash(FINGERPRINT_SCRIPT, 60)
    assert result == "exit code: 0\n" + "a" * 40 + "\n" + "0" * 40

    # Second call
    result = sandbox.bash(FINGERPRINT_SCRIPT, 60)
    assert result == "exit code: 1\nerror: boom"

    # Third call - raises RuntimeError
    with pytest.raises(RuntimeError, match="x"):
        sandbox.bash(FINGERPRINT_SCRIPT, 60)

    # Fourth call - also raises RuntimeError (last entry repeats)
    with pytest.raises(RuntimeError, match="x"):
        sandbox.bash(FINGERPRINT_SCRIPT, 60)

    # Non-hex str entry raises AssertionError at construction
    with pytest.raises(AssertionError):
        FingerprintSandbox(tmp_path, hashes=["not_hex"])

    # commands records (FINGERPRINT_SCRIPT, 60)
    assert any(cmd == FINGERPRINT_SCRIPT and to == 60 for cmd, to in sandbox.commands)

import re


def _git(*args, cwd):
    """Helper to run git commands with user.email and user.name set."""
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_real_script_on_the_host(tmp_path: Path):
    """Test the real fingerprint script on a repository with nested repositories.

    Spec §7 test 11b: one host test that runs the real fingerprint script
    (dirtywork.changes.FINGERPRINT_SCRIPT, added in W2b-1) on a repository with
    nested repositories.
    """
    import os

    # 1. Build a simple repo in tmp_path
    repo = tmp_path
    _git("init", "-q", cwd=repo)
    (repo / "README.md").write_text("# test repo\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)

    # 2. Run fingerprint on the simple repo
    from dirtywork.changes import FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT
    raw = HostSandbox(tmp_path).bash(FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT)
    fp, reason = parse_fingerprint(raw)

    assert reason is None
    assert fp is not None

    # Count hex lines (40 chars, lowercase)
    hex_pattern = re.compile(r"^[0-9a-f]{40}$")
    hex_lines = [line for line in raw.split("\n") if hex_pattern.match(line)]
    assert len(hex_lines) == 2, f"Expected 2 hash lines (tree and HEAD), got {len(hex_lines)}"

    # 3. Create nested repositories
    vendor_inner = tmp_path / "vendor" / "inner"
    vendor_inner.mkdir(parents=True)
    _git("init", "-q", cwd=vendor_inner)
    (vendor_inner / "file.txt").write_text("inner file\n")
    _git("add", "-A", cwd=vendor_inner)
    _git("commit", "-qm", "inner init", cwd=vendor_inner)

    vendor_unborn = tmp_path / "vendor" / "unborn"
    vendor_unborn.mkdir(parents=True)
    _git("init", "-q", cwd=vendor_unborn)
    (vendor_unborn / "x.txt").write_text("unborn file\n")
    # NO commit for unborn

    vendor_inner_deeper = tmp_path / "vendor" / "inner" / "deeper"
    vendor_inner_deeper.mkdir(parents=True)
    _git("init", "-q", cwd=vendor_inner_deeper)
    (vendor_inner_deeper / "deep.txt").write_text("deeper file\n")
    _git("add", "-A", cwd=vendor_inner_deeper)
    _git("commit", "-qm", "deeper init", cwd=vendor_inner_deeper)

    vendor_cafe = tmp_path / "vendor" / "café"
    vendor_cafe.mkdir(parents=True)
    _git("init", "-q", cwd=vendor_cafe)
    (vendor_cafe / "file.txt").write_text("café file\n")
    _git("add", "-A", cwd=vendor_cafe)
    _git("commit", "-qm", "café init", cwd=vendor_cafe)

    vendor_space = tmp_path / "vendor" / "sp ace"
    vendor_space.mkdir(parents=True)
    _git("init", "-q", cwd=vendor_space)
    (vendor_space / "file.txt").write_text("space file\n")
    _git("add", "-A", cwd=vendor_space)
    _git("commit", "-qm", "space init", cwd=vendor_space)

    # Run again - should have 7 hash lines
    raw2 = HostSandbox(tmp_path).bash(FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT)
    fp2, reason2 = parse_fingerprint(raw2)

    code = parse_exit_code(raw2)
    assert code == 0, f"Expected exit code 0, got {code}"

    hex_lines2 = [line for line in raw2.split("\n") if hex_pattern.match(line)]
    assert len(hex_lines2) == 7, f"Expected 7 hash lines (root tree/HEAD + 5 repos), got {len(hex_lines2)}: {hex_lines2}"
    assert fp2 is not None

    # 4. Write a new file inside vendor/unborn
    (vendor_unborn / "new.txt").write_text("new in unborn\n")
    raw3 = HostSandbox(tmp_path).bash(FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT)
    fp3, reason3 = parse_fingerprint(raw3)

    # The fingerprint should change because vendor/unborn changed
    hex_lines3 = [line for line in raw3.split("\n") if hex_pattern.match(line)]
    # Compare sets: symmetric difference should have 2 elements
    set2 = set(hex_lines2)
    set3 = set(hex_lines3)
    diff = set2 ^ set3
    assert len(diff) == 2, f"Expected symmetric difference of 2 hex lines, got {len(diff)}: {diff}"
    assert fp2 != fp3, "Fingerprint should have changed after writing to unborn repo"

    # 5. Rewrite README.md byte-identically
    readme_content = (tmp_path / "README.md").read_bytes()
    (tmp_path / "README.md").write_bytes(readme_content)

    raw4 = HostSandbox(tmp_path).bash(FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT)
    fp4, reason4 = parse_fingerprint(raw4)

    assert fp4 == fp3, "Byte-identical rewrite should not change fingerprint"

    # 6. Count files under real object store before and after a run with new file
    import os

    # Get the real git objects path
    result = subprocess.run(
        ["git", "rev-parse", "--git-path", "objects"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True
    )
    real_objects = result.stdout.strip()

    def count_files(path):
        """Count files under a path recursively."""
        count = 0
        for root, dirs, files in os.walk(path):
            count += len(files)
        return count

    before_count = count_files(real_objects)

    # Create a new 100KB untracked file
    large_file = tmp_path / "large.dat"
    large_file.write_bytes(os.urandom(100 * 1024))  # 100 KB

    raw5 = HostSandbox(tmp_path).bash(FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT)

    after_count = count_files(real_objects)
    assert after_count == before_count, f"Object store file count should not change: {before_count} -> {after_count}"

    # 7. Count entries of tempfile.gettempdir() before and after
    import tempfile

    temp_dir = tempfile.gettempdir()

    def count_temp_entries(path):
        """Count entries in a directory."""
        return len(os.listdir(path))

    before_temp = count_temp_entries(temp_dir)

    raw6 = HostSandbox(tmp_path).bash(FINGERPRINT_SCRIPT, FINGERPRINT_TIMEOUT)

    after_temp = count_temp_entries(temp_dir)
    assert after_temp == before_temp, f"Temp dir entry count should not change: {before_temp} -> {after_temp}"


def parse_exit_code(result):
    """The integer after 'exit code: ' on a bash result's first line, or None."""
    if not isinstance(result, str):
        return None
    head = result.split("\n", 1)[0]
    prefix = "exit code: "
    if not head.startswith(prefix):
        return None
    try:
        return int(head[len(prefix):].strip())
    except ValueError:
        return None
