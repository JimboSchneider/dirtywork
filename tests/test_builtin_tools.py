from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from dirtywork.budget import BudgetExceeded
from dirtywork.builtin_tools import default_registry
from dirtywork.sandbox.host import HostSandbox
from dirtywork.transcript import Transcript

FROZEN_SCHEMAS = Path(__file__).parent / "fixtures" / "tool_schemas.json"


class FakeSandbox:
    def __init__(self):
        self.calls = []

    def read_file(self, path, offset, limit):
        self.calls.append(("read_file", path, offset, limit))
        return f"read:{path}:{offset}:{limit}"

    def write_file(self, path, content):
        self.calls.append(("write_file", path, content))
        return f"wrote:{path}:{len(content)}"

    def edit_file(self, path, old_string, new_string):
        self.calls.append(("edit_file", path, old_string, new_string))
        return f"edited:{path}"

    def insert_before(self, path, anchor, text):
        self.calls.append(("insert_before", path, anchor, text))
        return f"inserted-before:{path}"

    def insert_after(self, path, anchor, text):
        self.calls.append(("insert_after", path, anchor, text))
        return f"inserted-after:{path}"

    def list_dir(self, path):
        self.calls.append(("list_dir", path))
        return f"listing:{path}"

    def grep(self, pattern, path, glob, timeout):
        self.calls.append(("grep", pattern, path, glob, timeout))
        return f"grepped:{pattern}"

    def bash(self, command, timeout):
        self.calls.append(("bash", command, timeout))
        return f"exit code: 0\n{command}"


@pytest.fixture()
def wt(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hi\n")
    return tmp_path


def test_schemas_match_the_frozen_wire_fixture():
    # The model-facing contract must not drift without a deliberate, matching
    # change to builtin_tools.py AND to this fixture. The fixture tracks HEAD
    # (it was regenerated in 0.8 and again in 0.9), which is why it is no
    # longer named after 0.5.1: regenerate it with
    #   python3 -c "import json; from dirtywork.builtin_tools import default_registry; \
    #     open('tests/fixtures/tool_schemas.json','w',encoding='utf-8').write(\
    #     json.dumps(default_registry().schemas(), indent=2, ensure_ascii=False) + '\n')"
    # and read the diff before committing it.
    expected = json.loads(FROZEN_SCHEMAS.read_text(encoding="utf-8"))
    assert default_registry().schemas() == expected


def test_schemas_shape():
    schemas = default_registry().schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"read_file", "write_file", "edit_file", "insert_before", "insert_after",
                     "list_dir", "grep", "bash", "finish"}
    for s in schemas:
        assert s["type"] == "function"
        assert "parameters" in s["function"]


def test_bash_schema_mentions_reset_behavior():
    schema = next(s for s in default_registry().schemas() if s["function"]["name"] == "bash")
    description = schema["function"]["description"]
    assert "reset" in description.lower()
    assert "index" in description.lower() or "git state" in description.lower()


def test_finish_is_the_only_terminal_spec():
    registry = default_registry()
    terminal = [name for name in registry.names() if registry.spec(name).terminal]
    assert terminal == ["finish"]


def test_read_file_dispatches_positionally():
    sandbox = FakeSandbox()
    result = default_registry().execute("read_file", {"path": "a.txt"},
                                        sandbox=sandbox, deadline=None)
    assert result.kind == "ok"
    assert sandbox.calls == [("read_file", "a.txt", 0, 400)]


def test_write_file_dispatches():
    sandbox = FakeSandbox()
    result = default_registry().execute("write_file", {"path": "a.txt", "content": "hi"},
                                        sandbox=sandbox, deadline=None)
    assert result.kind == "ok"
    assert sandbox.calls == [("write_file", "a.txt", "hi")]


def test_edit_file_dispatches_old_new():
    sandbox = FakeSandbox()
    default_registry().execute("edit_file", {"path": "a.txt", "old_string": "x",
                                             "new_string": "y"},
                               sandbox=sandbox, deadline=None)
    assert sandbox.calls == [("edit_file", "a.txt", "x", "y")]


def test_list_dir_default_path():
    sandbox = FakeSandbox()
    default_registry().execute("list_dir", {}, sandbox=sandbox, deadline=None)
    assert sandbox.calls == [("list_dir", ".")]


def test_grep_dispatches_with_hidden_timeout_default():
    sandbox = FakeSandbox()
    default_registry().execute("grep", {"pattern": "foo"}, sandbox=sandbox, deadline=None)
    assert sandbox.calls == [("grep", "foo", ".", None, 30)]


def test_bash_dispatches_with_timeout_default():
    sandbox = FakeSandbox()
    default_registry().execute("bash", {"command": "ls"}, sandbox=sandbox, deadline=None)
    assert sandbox.calls == [("bash", "ls", 120)]


def test_bash_timeout_clamped_to_600():
    sandbox = FakeSandbox()
    default_registry().execute("bash", {"command": "ls", "timeout": 9999},
                               sandbox=sandbox, deadline=None)
    assert sandbox.calls == [("bash", "ls", 600)]


def test_registry_output_cap_never_re_truncates_a_tool_s_own_capped_result():
    # tools._cap already truncates at MAX_RESULT_CHARS and appends a note; the
    # registry cap sits above that, so the note survives intact.
    class CappedSandbox(FakeSandbox):
        def read_file(self, path, offset, limit):
            from dirtywork.tools import MAX_RESULT_CHARS
            return "z" * MAX_RESULT_CHARS + "\n[output truncated at 8000 chars — re-run with offset/limit to see more]"

    result = default_registry().execute("read_file", {"path": "a.txt"},
                                        sandbox=CappedSandbox(), deadline=None)
    assert result.text.endswith("re-run with offset/limit to see more]")


# --- moved here from tests/test_tools_bash.py (the ToolExecutor tests): the
# --- subject moved from ToolExecutor to ToolRegistry + builtin specs.

def test_dispatch_and_unknown_tool(wt: Path):
    registry = default_registry()
    sandbox = HostSandbox(wt)
    assert "hi" in registry.execute("read_file", {"path": "hello.txt"},
                                    sandbox=sandbox, deadline=None).text
    unknown = registry.execute("format_disk", {}, sandbox=sandbox, deadline=None)
    assert unknown.failure == "unknown_tool"
    assert "unknown tool 'format_disk'" in unknown.text


def test_drops_unknown_tool_args(wt: Path):
    # qwen/other local models attach e.g. Claude Code's `description` to bash
    # calls; that must not become a bad_args strike (3 in a row aborts the run).
    registry = default_registry()
    out = registry.execute("bash", {"command": "echo hi", "description": "say hi"},
                           sandbox=HostSandbox(wt), deadline=None)
    assert out.kind == "ok"
    assert "hi" in out.text


def test_missing_required_arg_is_bad_args(wt: Path):
    out = default_registry().execute("bash", {"description": "no command"},
                                     sandbox=HostSandbox(wt), deadline=None)
    assert out.kind == "error" and out.failure == "bad_args"
    assert "command" in out.text


def test_deadline_exceeded_blocks_execution(wt: Path):
    out = default_registry().execute("bash", {"command": "touch created.txt"},
                                     sandbox=HostSandbox(wt),
                                     deadline=time.monotonic() - 1)
    assert "deadline exceeded" in out.text.lower()
    assert not (wt / "created.txt").exists()


def test_clamps_bash_timeout_to_remaining_deadline(wt: Path):
    captured = {}

    class CapturingSandbox(FakeSandbox):
        def bash(self, command, timeout):
            captured["timeout"] = timeout
            return "exit code: 0\n"

    default_registry().execute("bash", {"command": "true", "timeout": 600},
                               sandbox=CapturingSandbox(),
                               deadline=time.monotonic() + 3)
    assert 1 <= captured["timeout"] <= 3


def test_clamps_grep_timeout_to_remaining_deadline():
    captured = {}

    class CapturingSandbox(FakeSandbox):
        def grep(self, pattern, path, glob, timeout):
            captured["timeout"] = timeout
            return "No matches found."

    default_registry().execute("grep", {"pattern": "hi"}, sandbox=CapturingSandbox(),
                               deadline=time.monotonic() + 3)
    assert 1 <= captured["timeout"] <= 3


def test_logs_guardrail_block(wt: Path, tmp_path: Path):
    t = Transcript(tmp_path / "log.jsonl")
    registry = default_registry(transcript=t)
    out = registry.execute("bash", {"command": "git push"}, sandbox=HostSandbox(wt),
                           deadline=None)
    t.close()
    assert out.kind == "blocked"
    assert out.text.startswith("BLOCKED:")
    events = [json.loads(l) for l in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert any(e["event"] == "guardrail_block" for e in events)


def test_blocked_result_from_sandbox_marks_kind_blocked():
    class BlockingSandbox(FakeSandbox):
        def bash(self, command, timeout):
            return "BLOCKED: sudo is not allowed."

    result = default_registry().execute("bash", {"command": "sudo ls"},
                                        sandbox=BlockingSandbox(), deadline=None)
    assert result.kind == "blocked"
    assert result.failure is None


def test_budget_exceeded_propagates_over_file_limit(wt: Path):
    registry = default_registry()
    sb = HostSandbox(wt, max_worktree_files=3)
    # wt already has 1 entry (hello.txt from the fixture). Each write adds one
    # more; the check runs AFTER the write, so it must succeed through exactly
    # 3 total entries and only raise once a 4th is created.
    registry.execute("write_file", {"path": "a.txt", "content": "x"}, sandbox=sb, deadline=None)
    registry.execute("write_file", {"path": "b.txt", "content": "x"}, sandbox=sb, deadline=None)
    with pytest.raises(BudgetExceeded):
        registry.execute("write_file", {"path": "c.txt", "content": "x"}, sandbox=sb, deadline=None)


def test_insert_before_dispatches():
    sandbox = FakeSandbox()
    result = default_registry().execute(
        "insert_before", {"path": "a.txt", "anchor": "x", "text": "y"},
        sandbox=sandbox, deadline=None)
    assert result.kind == "ok"
    assert sandbox.calls == [("insert_before", "a.txt", "x", "y")]


def test_insert_after_dispatches():
    sandbox = FakeSandbox()
    result = default_registry().execute(
        "insert_after", {"path": "a.txt", "anchor": "x", "text": "y"},
        sandbox=sandbox, deadline=None)
    assert result.kind == "ok"
    assert sandbox.calls == [("insert_after", "a.txt", "x", "y")]
