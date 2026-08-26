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
