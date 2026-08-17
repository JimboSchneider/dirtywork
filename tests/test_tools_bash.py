from __future__ import annotations

import time
from pathlib import Path

import pytest

from dirtywork.tools import bash, grep


@pytest.fixture()
def wt(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hi\n")
    return tmp_path


def test_bash_runs_in_worktree_cwd(wt: Path):
    out = bash(wt, "pwd && cat hello.txt")
    assert "exit code: 0" in out
    assert str(wt.resolve()) in out
    assert "hi" in out


def test_bash_nonzero_exit_reported(wt: Path):
    out = bash(wt, "exit 3")
    assert "exit code: 3" in out


def test_bash_blocked_command(wt: Path):
    out = bash(wt, "sudo ls")
    assert out.startswith("BLOCKED:")


def test_bash_timeout(wt: Path):
    out = bash(wt, "sleep 5", timeout=1)
    assert "timed out" in out.lower()


def test_bash_cd_into_worktree_by_absolute_path_allowed(wt: Path):
    # A model cd-ing into the worktree with an absolute path (common local-model
    # behavior) must not be denylisted — only escapes past the worktree root should be.
    out = bash(wt, f"cd {wt} && pwd")
    assert not out.startswith("BLOCKED")
    assert str(wt.resolve()) in out


def test_bash_env_is_minimal(wt: Path, monkeypatch):
    monkeypatch.setenv("MY_SECRET", "sekrit")
    out = bash(wt, "env")
    assert "PATH=" in out
    assert "MY_SECRET" not in out  # parent env not inherited wholesale


def test_grep_timeout_kwarg_works(wt: Path):
    out = grep(wt, "hi", timeout=5)
    assert "hello.txt" in out


def test_bash_output_is_capped(wt: Path):
    # 2 MB of output must not blow up; it is capped and noted.
    out = bash(wt, "python3 -c \"import sys; sys.stdout.write('A'*2000000)\"")
    assert len(out) < 20000
    assert "capped" in out


def test_bash_runaway_output_times_out_without_ooming(wt: Path):
    # cat /dev/zero would OOM under unbounded capture; here it is drained and killed.
    out = bash(wt, "cat /dev/zero", timeout=1)
    assert "timed out" in out.lower()


def test_bash_backgrounded_child_does_not_stall(wt: Path):
    start = time.monotonic()
    out = bash(wt, "sleep 30 & echo hi", timeout=10)
    assert "hi" in out
    # Before the process-group fix, the reader thread stalled ~5s on the
    # backgrounded sleep still holding the stdout pipe.
    assert time.monotonic() - start < 3.0


def test_bash_timeout_reaps_process_tree(wt: Path):
    out = bash(wt, "(sleep 2 && touch survived.txt) & wait", timeout=1)
    assert "timed out" in out.lower()
    time.sleep(2.5)  # past when the sleep would fire if it had survived the kill
    assert not (wt / "survived.txt").exists()  # killpg reaped the whole group


def test_bash_popen_failure_returns_error_prefix(wt: Path, monkeypatch):
    import dirtywork.procs
    original_popen = dirtywork.procs.subprocess.Popen

    def fake_popen(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(dirtywork.procs.subprocess, "Popen", fake_popen)
    out = bash(wt, "true")
    assert out.startswith("ERROR: bash failed:")
    assert "boom" in out
