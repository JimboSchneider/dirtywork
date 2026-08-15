from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from dirtywork.tools import TOOL_SCHEMAS, ToolExecutor, bash, grep
from dirtywork.transcript import Transcript


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


def test_bash_env_is_minimal(wt: Path, monkeypatch):
    monkeypatch.setenv("MY_SECRET", "sekrit")
    out = bash(wt, "env")
    assert "PATH=" in out
    assert "MY_SECRET" not in out  # parent env not inherited wholesale


def test_schemas_shape():
    names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    assert names == {"read_file", "write_file", "edit_file", "list_dir", "grep", "bash"}
    for s in TOOL_SCHEMAS:
        assert s["type"] == "function"
        assert "parameters" in s["function"]


def test_executor_dispatch_and_unknown(wt: Path):
    ex = ToolExecutor(wt)
    assert "hi" in ex.execute("read_file", {"path": "hello.txt"})
    with pytest.raises(KeyError):
        ex.execute("format_disk", {})


def test_executor_deadline_exceeded_blocks_execution(wt: Path):
    ex = ToolExecutor(wt)
    ex.deadline = time.monotonic() - 1
    out = ex.execute("bash", {"command": "touch created.txt"})
    assert "deadline exceeded" in out.lower()
    assert not (wt / "created.txt").exists()


def test_executor_clamps_bash_timeout_to_remaining_deadline(wt: Path):
    captured = {}

    def fake_bash(worktree, command, timeout=120):
        captured["timeout"] = timeout
        return "exit code: 0\n"

    ex = ToolExecutor(wt)
    ex._table["bash"] = fake_bash
    ex.deadline = time.monotonic() + 3

    ex.execute("bash", {"command": "true", "timeout": 600})

    assert captured["timeout"] <= 3
    assert captured["timeout"] >= 1


def test_grep_timeout_kwarg_works(wt: Path):
    out = grep(wt, "hi", timeout=5)
    assert "hello.txt" in out


def test_executor_clamps_grep_timeout_to_remaining_deadline(wt: Path):
    captured = {}

    def fake_grep(worktree, pattern, path=".", glob=None, timeout=30):
        captured["timeout"] = timeout
        return "No matches found."

    ex = ToolExecutor(wt)
    ex._table["grep"] = fake_grep
    ex.deadline = time.monotonic() + 3

    ex.execute("grep", {"pattern": "hi"})

    assert captured["timeout"] <= 3
    assert captured["timeout"] >= 1


def test_executor_logs_guardrail_block(wt: Path, tmp_path: Path):
    t = Transcript(tmp_path / "log.jsonl")
    ex = ToolExecutor(wt, transcript=t)
    out = ex.execute("bash", {"command": "git push"})
    t.close()
    assert out.startswith("BLOCKED:")
    events = [json.loads(l) for l in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert any(e["event"] == "guardrail_block" for e in events)
