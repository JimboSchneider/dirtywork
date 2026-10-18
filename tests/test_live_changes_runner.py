from __future__ import annotations

from dirtywork.builtin_tools import default_registry
from dirtywork.runner import Runner
from dirtywork.sandbox.host import HostSandbox
from dirtywork.transcript import Transcript

from .provider_doubles import DictProvider, tool_call_body


class ToolProvider(DictProvider):
    def __init__(self, calls):
        super().__init__()
        self.body = tool_call_body("finish", {"summary": "done"})
        self.body["choices"][0]["message"]["tool_calls"] = [
            tool_call_body(name, args, call_id=str(index))["choices"][0]["message"]["tool_calls"][0]
            for index, (name, args) in enumerate(calls)
        ]

    def reply(self, model, history, tools):
        assert self.calls == 1, "The run should finish in one model turn."
        return self.body


def run_tools(tmp_path, calls, callback, *, verify=None):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    transcript = Transcript(tmp_path / "transcript.jsonl")
    try:
        runner = Runner(
            ToolProvider(calls), default_registry(transcript=transcript),
            HostSandbox(worktree), transcript, model="test",
            on_live_changes=callback, verify=verify,
        )
        return runner.run("system", "update the file")
    finally:
        transcript.close()


def test_live_changes_capture_each_mutation_before_the_next_tool(tmp_path):
    snapshots = []
    target = tmp_path / "worktree" / "file.txt"
    calls = [
        ("write_file", {"path": "file.txt", "content": "anchor\n"}),
        ("read_file", {"path": "file.txt"}),
        ("append_file", {"path": "file.txt", "text": "tail\n"}),
        ("edit_file", {"path": "file.txt", "old_string": "anchor", "new_string": "edited"}),
        ("apply_edits", {"path": "file.txt", "edits": [{"old": "tail", "new": "end"}]}),
        ("list_dir", {"path": "."}),
        ("insert_before", {"path": "file.txt", "anchor": "edited", "text": "start\n"}),
        ("insert_after", {"path": "file.txt", "anchor": "edited", "text": "middle\n"}),
        ("grep", {"pattern": "edited", "path": "file.txt"}),
        ("bash", {"command": "printf 'shell\\n' >> file.txt"}),
        ("finish", {"summary": "done"}),
    ]

    result = run_tools(tmp_path, calls, lambda: snapshots.append(target.read_text()))

    assert result.status == "completed"
    assert snapshots == [
        "anchor\n",
        "anchor\ntail\n",
        "edited\ntail\n",
        "edited\nend\n",
        "start\nedited\nend\n",
        "start\nedited\nmiddle\nend\n",
        "start\nedited\nmiddle\nend\nshell\n",
    ]


def test_failed_live_changes_callback_does_not_stop_later_tools(tmp_path):
    snapshots = []
    target = tmp_path / "worktree" / "file.txt"

    def failing_callback():
        snapshots.append(target.read_text())
        raise RuntimeError("preview unavailable")

    result = run_tools(tmp_path, [
        ("write_file", {"path": "file.txt", "content": "first\n"}),
        ("append_file", {"path": "file.txt", "text": "second\n"}),
        ("finish", {"summary": "done"}),
    ], failing_callback)

    assert result.status == "completed"
    assert snapshots == ["first\n", "first\nsecond\n"]
    assert target.read_text() == "first\nsecond\n"


def test_live_changes_capture_files_written_by_verify(tmp_path):
    snapshots = []
    target = tmp_path / "worktree" / "verified.txt"

    result = run_tools(
        tmp_path, [("finish", {"summary": "done"})],
        lambda: snapshots.append(target.read_text()),
        verify="printf 'verified\\n' > verified.txt",
    )

    assert result.status == "completed"
    assert result.extra["verify"]["passed"] is True
    assert snapshots == ["verified\n"]


def test_live_changes_keyboard_interrupt_stops_the_run(tmp_path):
    target = tmp_path / "worktree" / "file.txt"

    def interrupting_callback():
        raise KeyboardInterrupt

    result = run_tools(tmp_path, [
        ("write_file", {"path": "file.txt", "content": "first\n"}),
        ("append_file", {"path": "file.txt", "text": "second\n"}),
        ("finish", {"summary": "done"}),
    ], interrupting_callback)

    assert result.status == "interrupted"
    assert target.read_text() == "first\n"
