from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from dirtywork.llm import LLMError, LLMTimeout, MalformedResponse
from dirtywork.providers import ChatResponse, ToolCall
from dirtywork.providers.openai_compat import CONTEXT_WINDOWS
from dirtywork.runner import (
    DEFAULT_STALL_TURNS,
    DEFAULT_WINDOW,
    FailureTracker,
    MAX_TOTAL_CONSECUTIVE_FAILURES,
    NUDGES,
    ProgressTracker,
    RunResult,
    Runner,
    STALL_NUDGE,
    TRIM_MARKER,
    _bash_fingerprint,
    classify_text_reply,
    resolve_context_window,
    strip_think,
    trim_messages,
)
from dirtywork.sandbox.host import HostSandbox
from dirtywork.builtin_tools import default_registry
from dirtywork.transcript import Transcript


def _resp(content=None, tool_calls=None, usage=None, finish_reason=None):
    return ChatResponse(text=content or "",
                        tool_calls=list(tool_calls or []),
                        finish_reason=finish_reason,
                        usage=usage or {"prompt_tokens": 10, "completion_tokens": 5})


def _call(call_id, name, args: dict):
    return ToolCall(id=call_id, name=name, arguments=args, error=None,
                    raw_arguments=json.dumps(args))


def _bad_args(call_id="x", name="read_file", raw="{not json"):
    """A tool call the provider could parse structurally but whose arguments it
    could not decode: addressable (has an id), so the runner answers it with an
    error tool result and counts a `malformed_args` strike."""
    return ToolCall(id=call_id, name=name, arguments=None,
                    error="malformed tool arguments: bad JSON", raw_arguments=raw)


def _bad_entry():
    """A structurally invalid wire entry: no usable id, so the runner cannot
    answer it and counts a `malformed_entry` strike."""
    return ToolCall(id="", name="", arguments=None,
                    error="malformed tool call entry (missing or invalid id/function fields)")


class FakeProvider:
    name = "fake"

    def __init__(self, responses, context_window=None):
        self.responses = list(responses)
        self.requests = []
        self.timeouts = []
        self._context_window = context_window

    def list_models(self):
        return ["m"]

    def context_window(self, model):
        return self._context_window

    def chat(self, model, history, tools, *, temperature=None, max_tokens=4096, timeout=None):
        # Deep-copy the history the way the old FakeProvider did, so later
        # mutation (trim_messages) cannot rewrite what a test already saw.
        self.requests.append([dict(m) for m in history])
        self.timeouts.append(timeout)
        return self.responses.pop(0)


@pytest.fixture()
def parts(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "f.txt").write_text("data\n")
    transcript = Transcript(tmp_path / "t.jsonl")
    registry = default_registry(transcript=transcript)
    sandbox = HostSandbox(wt)
    return wt, registry, sandbox, transcript, tmp_path


def _events(tmp_path: Path):
    return [json.loads(l) for l in (tmp_path / "t.jsonl").read_text().splitlines()]


def test_two_turn_run(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
        _resp(content="Done: file says data"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="qwen/qwen3-coder-next")
    result = r.run("sysprompt", "read the file")
    transcript.close()

    assert result.status == "completed"
    assert result.turns == 2
    assert "Done" in result.final_message
    assert result.usage == {"prompt_tokens": 20, "completion_tokens": 10}

    # second request must include the tool result message with matching id
    second = provider.requests[1]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "c1"
    assert "data" in tool_msgs[0]["content"]

    kinds = [e["event"] for e in _events(tmp)]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end"
    assert "assistant" in kinds and "tool_result" in kinds


def test_max_turns(parts):
    wt, registry, sandbox, transcript, tmp = parts
    loop_resp = _resp(tool_calls=[_call("c", "list_dir", {"path": "."})])
    provider = FakeProvider([loop_resp] * 3)
    r = Runner(provider, registry, sandbox, transcript, model="m", max_turns=3)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "max_turns"
    assert result.turns == 3


def test_malformed_args_three_strikes(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad = _resp(tool_calls=[_bad_args()])
    provider = FakeProvider([bad, bad, bad])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"


def test_unknown_tool_counts_as_strike_but_recovers(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "no_such_tool", {})]),
        _resp(content="ok done"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    # the model got an error message back as the tool result
    second = provider.requests[1]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert "unknown tool" in tool_msgs[0]["content"].lower()


def test_trim_messages():
    msgs = [
        {"role": "system", "content": "s" * 100},
        {"role": "tool", "tool_call_id": "1", "content": "x" * 1000},
        {"role": "assistant", "content": "a" * 100},
        {"role": "tool", "tool_call_id": "2", "content": "y" * 1000},
    ]
    fits = trim_messages(msgs, char_budget=1300)
    assert fits
    assert msgs[1]["content"] == TRIM_MARKER      # oldest trimmed first
    assert msgs[3]["content"] == "y" * 1000        # newer kept
    assert msgs[0]["content"] == "s" * 100         # system never trimmed


def test_trim_cannot_fit():
    msgs = [{"role": "system", "content": "s" * 5000}]
    assert trim_messages(msgs, char_budget=100) is False


def test_trim_counts_tool_call_arguments():
    msgs = [
        {"role": "assistant", "content": None,
         "tool_calls": [ToolCall(id="1", name="write_file", arguments=None,
                                 error=None, raw_arguments="a" * 1000)]},
    ]
    # No role=="tool" messages exist to trim, so this only passes if the
    # tool_call arguments are counted toward the budget in the first place.
    assert trim_messages(msgs, char_budget=500) is False


def test_malformed_tool_call_entry_recovers(parts):
    # An entry the provider could not address at all (no id) routes through the
    # malformed-tool-call recovery path: no tool message, one user nudge.
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(tool_calls=[_bad_entry()]), _resp(content="ok done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    second = provider.requests[1]
    assert not [m for m in second if m["role"] == "tool"]
    user_msgs = [m for m in second if m["role"] == "user"]
    assert any("malformed" in (m.get("content") or "").lower() for m in user_msgs)
    # The transcript records the ADAPTER's own error text for the entry (it
    # knows the wire shape it failed to parse), not runner-invented wording.
    bad_results = [e for e in _events(tmp) if e.get("event") == "tool_result" and e.get("tool") == ""]
    assert bad_results and bad_results[0]["result"] == "ERROR: " + _bad_entry().error


def test_malformed_response_is_model_error(parts):
    # The adapter raises MalformedResponse for a body it cannot read; the runner
    # converts it through finish(), so finalize() runs and run_end is written.
    wt, registry, sandbox, transcript, tmp = parts

    class BadBodyProvider(FakeProvider):
        def chat(self, *a, **k):
            raise MalformedResponse("malformed response from server (no choices[0].message)")

    r = Runner(BadBodyProvider([]), registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    assert "malformed response from server" in result.final_message
    assert _events(tmp)[-1]["event"] == "run_end"
    assert result.turns == 1     # the request was made and answered: it counts


def test_strike_counter_resets_on_success(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad = _resp(tool_calls=[_bad_args()])
    good = _resp(tool_calls=[_call("g", "list_dir", {"path": "."})])
    provider = FakeProvider([bad, bad, good, bad, bad, _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"


def test_timeout_status(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="never reached")])
    r = Runner(provider, registry, sandbox, transcript, model="m", timeout=-1)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "timeout"
    assert result.turns == 0


def test_context_exhausted_status(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    r.char_budget = 10
    result = r.run("s" * 100, "t")
    transcript.close()
    assert result.status == "context_exhausted"


def test_length_finish_reason_gives_helpful_hint(parts):
    wt, registry, sandbox, transcript, tmp = parts
    truncated = _resp(tool_calls=[_bad_args("c", "write_file", '{"path": "x", "content": "abc')],
                      finish_reason="length",
                      usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([truncated, _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert "cut off at the token limit" in tool_msgs[0]["content"]


def test_run_start_includes_run_info(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m", run_info={"repo": "/r"})
    result = r.run("s", "t")
    transcript.close()
    events = _events(tmp)
    run_start = next(e for e in events if e["event"] == "run_start")
    assert run_start["repo"] == "/r"


def test_chat_receives_bounded_timeout(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m", timeout=30)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert provider.timeouts and provider.timeouts[0] is not None
    assert 0 < provider.timeouts[0] <= 30


def test_llm_timeout_near_deadline_gives_timeout_status(parts):
    wt, registry, sandbox, transcript, tmp = parts

    class SlowTimeoutClient(FakeProvider):
        def chat(self, *a, **k):
            time.sleep(0.3)
            raise LLMTimeout("request timed out")

    r = Runner(SlowTimeoutClient([]), registry, sandbox, transcript, model="m", timeout=0.2)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "timeout"


def test_llm_timeout_far_from_deadline_gives_model_error(parts):
    wt, registry, sandbox, transcript, tmp = parts

    class ImmediateTimeoutClient(FakeProvider):
        def chat(self, *a, **k):
            raise LLMTimeout("request timed out")

    r = Runner(ImmediateTimeoutClient([]), registry, sandbox, transcript, model="m", timeout=1800)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"









def test_three_consecutive_malformed_tool_calls_aborts(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad = _resp(tool_calls=[_bad_entry()])
    provider = FakeProvider([bad, bad, bad])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"


def test_interrupted_status(parts):
    wt, registry, sandbox, transcript, tmp = parts
    class InterruptingClient(FakeProvider):
        def chat(self, *a, **k):
            raise KeyboardInterrupt
    r = Runner(InterruptingClient([]), registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "interrupted"
    events = _events(tmp)
    assert events[-1]["event"] == "run_end"



def test_finalize_merges_into_run_end_and_result_extra(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m",
              finalize=lambda: {"diff_stat": " 1 file changed"})
    result = r.run("s", "t")
    transcript.close()
    assert result.extra == {"stuck_on": None, "last_tool_result": None,
                            "last_assistant_text": "done", "diff_stat": " 1 file changed"}
    events = _events(tmp)
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end["diff_stat"] == " 1 file changed"


def test_finalize_exception_recorded_status_preserved(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done")])

    def boom():
        raise RuntimeError("disk gone")

    r = Runner(provider, registry, sandbox, transcript, model="m", finalize=boom)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    events = _events(tmp)
    run_end = next(e for e in events if e["event"] == "run_end")
    assert "disk gone" in run_end["finalize_error"]


def test_assistant_text_capped_in_transcript_full_text_resent(parts):
    wt, registry, sandbox, transcript, tmp = parts
    # Over MAX_ASSISTANT_TEXT_CHARS (64_000) but comfortably under the
    # default model's char_budget (~98_304 for the fallback DEFAULT_WINDOW),
    # so trim_messages doesn't ALSO trigger context_exhausted — this test is
    # about the transcript-only cap, not the trim path.
    huge_text = "y" * 70_000
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "list_dir", {"path": "."})], content=huge_text),
        _resp(content="done"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"

    events = _events(tmp)
    assistant_event = next(e for e in events if e["event"] == "assistant")
    assert len(assistant_event["text"]) < 70_000
    assert "truncated" in assistant_event["text"]

    # the resent history to the model must keep the FULL text
    second = provider.requests[1]
    assistant_msg = next(m for m in second if m["role"] == "assistant" and m.get("tool_calls"))
    assert assistant_msg["content"] == huge_text


def test_budget_exceeded_from_sandbox_ends_run(parts):
    wt, registry, sandbox, transcript, tmp = parts
    from dirtywork.budget import BudgetExceeded

    class BudgetBustingSandbox:
        def write_file(self, path, content):
            raise BudgetExceeded("worktree exceeds 2048 MB")

    provider = FakeProvider([_resp(tool_calls=[_call("c1", "write_file", {"path": "x", "content": "y"})])])
    r = Runner(provider, registry, BudgetBustingSandbox(), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "budget_exceeded"
    assert "2048 MB" in result.final_message
    events = _events(tmp)
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end["status"] == "budget_exceeded"


def test_finish_tool_ends_run_after_other_calls_in_turn(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[
            _call("f1", "finish", {"summary": "wrote g.txt"}),
            _call("w1", "write_file", {"path": "g.txt", "content": "hi\n"}),
        ]),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.final_message == "wrote g.txt"
    assert result.turns == 1
    assert (wt / "g.txt").read_text() == "hi\n"   # the later call still executed
    events = _events(tmp)
    finish_results = [e for e in events if e["event"] == "tool_result" and e["tool"] == "finish"]
    assert finish_results and finish_results[0]["result"] == "run finished"
    assert events[-1]["event"] == "run_end" and events[-1]["status"] == "completed"


def test_finish_without_summary_still_completes_with_empty_message(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {})])])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.final_message == ""


def test_finish_with_malformed_args_does_not_end_run(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad = _resp(tool_calls=[_bad_args(call_id="f1", name="finish")])
    provider = FakeProvider([bad, _resp(tool_calls=[_call("f2", "finish", {"summary": "ok"})])])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.turns == 2
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert "malformed tool arguments" in tool_msgs[0]["content"]


def test_unknown_tool_error_mentions_finish(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "no_such_tool", {})]),
        _resp(content="ok done"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    r.run("s", "t")
    transcript.close()
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert "finish(summary=...)" in tool_msgs[0]["content"]


def test_failure_tracker_per_kind_threshold():
    t = FailureTracker()
    assert t.record("unknown_tool") is None
    assert t.record("malformed_args") is None
    assert t.record("unknown_tool") is None
    reason = t.record("unknown_tool")
    assert reason == "aborted after 3 consecutive unknown_tool failures"


def test_failure_tracker_total_threshold_across_kinds():
    t = FailureTracker()
    seq = ["malformed_args", "unknown_tool", "bad_args", "malformed_args", "unknown_tool"]
    for k in seq:
        assert t.record(k) is None
    assert t.record("bad_args") == f"aborted after {MAX_TOTAL_CONSECUTIVE_FAILURES} consecutive tool failures"


def test_failure_tracker_reset_clears_all():
    t = FailureTracker()
    t.record("unknown_tool"); t.record("unknown_tool")
    t.reset()
    assert t.record("unknown_tool") is None
    assert t.record("unknown_tool") is None


def test_failure_tracker_rejects_unknown_kind():
    with pytest.raises(ValueError):
        FailureTracker().record("nope")


def test_mixed_failure_kinds_do_not_abort_at_three(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad_args = _resp(tool_calls=[_bad_args()])
    unknown = _resp(tool_calls=[_call("u", "no_such_tool", {})])
    wrong_type = _resp(tool_calls=[_call("t", "read_file", {})])  # missing required arg → registry validation → bad_args
    provider = FakeProvider([bad_args, unknown, wrong_type, _resp(content="ok done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.turns == 4


# NOTE: the tags below are built by concatenation ON PURPOSE. Local models' chat
# templates parse these exact tags in their own output (Qwen3-coder's tool-call XML
# uses function=/parameter= XML tags; think tags are stripped by the server), so a
# worker model editing this file through its tool channel cannot emit them literally.
# Keep every occurrence of these tags in this file and in runner.py concatenated.
def _tag(name: str) -> str:
    return "<" + name + ">"


_THINK = _tag("think")
_THINK_END = _tag("/think")


@pytest.mark.parametrize("content,finish_reason,expected", [
    ("Done: all tests pass", None, "answer"),
    ("Done", "stop", "answer"),
    ("anything", "length", "truncated"),
    ("", None, "empty"),
    (None, None, "empty"),
    ("   \n", None, "empty"),
    (_THINK + "let me reason" + _THINK_END, None, "empty"),
    (_THINK + "never closed the tag", None, "empty"),
    (_THINK + "plan" + _THINK_END + "Done, wrote the file.", None, "answer"),
    (_tag("tool_call") + '{"name":"bash"}' + _tag("/tool_call"), None, "text_tool_call"),
    ("<" + 'function=read_file>{"path":"x"}' + _tag("/function"), None, "text_tool_call"),
    ("<" + "|tool_call|>bash", None, "text_tool_call"),
    (_tag("function_call") + "bash" + _tag("/function_call"), None, "text_tool_call"),
    ('I will run {"name": "bash", "arguments": {"command": "ls"}} now', None, "text_tool_call"),
    ('The config is {"name": "app", "version": 2}', None, "answer"),
])
def test_classify_text_reply(content, finish_reason, expected):
    assert classify_text_reply(content, finish_reason) == expected


def test_strip_think_removes_blocks():
    assert strip_think(_THINK + "a" + _THINK_END + "x" + _THINK + "b" + _THINK_END + "y") == "xy"
    assert strip_think(_THINK + "open") == ""
    assert strip_think(None) == ""


def test_empty_reply_is_nudged_not_completed(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content=""), _resp(content="done for real")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.turns == 2
    assert result.final_message == "done for real"
    second = provider.requests[1]
    assert second[-1]["role"] == "user" and second[-1]["content"] == NUDGES["empty"]
    assert second[-2] == {"role": "assistant", "content": ""}
    events = _events(tmp)
    nudges = [e for e in events if e["event"] == "nudge"]
    assert len(nudges) == 1
    assert nudges[0]["kind"] == "empty" and nudges[0]["turn"] == 1


def test_think_only_reply_is_nudged(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content=_THINK + "hmm" + _THINK_END), _resp(content="ok")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed" and result.turns == 2


def test_text_tool_call_reply_is_nudged(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content=_tag("tool_call") + '{"name":"bash","arguments":{}}' + _tag("/tool_call")),
                         _resp(content="ok")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed" and result.turns == 2
    assert provider.requests[1][-1]["content"] == NUDGES["text_tool_call"]


def test_length_cutoff_without_tool_calls_is_not_completed(parts):
    wt, registry, sandbox, transcript, tmp = parts
    cut = _resp(content="I will now", finish_reason="length",
               usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([cut, _resp(content="ok")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed" and result.turns == 2
    assert provider.requests[1][-1]["content"] == NUDGES["truncated"]


def test_three_empty_replies_abort_as_model_error(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content=""), _resp(content=""), _resp(content="")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    assert result.final_message == "aborted after 3 consecutive empty_reply failures"


def test_successful_call_resets_empty_reply_count(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content=""), _resp(content=""),
                         _resp(tool_calls=[_call("c", "read_file", {"path": "f.txt"})]),
                         _resp(content=""), _resp(content=""), _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed" and result.turns == 6


def test_abort_message_names_the_kind(parts):
    wt, registry, sandbox, transcript, tmp = parts
    unknown = _resp(tool_calls=[_call("u", "no_such_tool", {})])
    provider = FakeProvider([unknown, unknown, unknown])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    assert result.final_message == "aborted after 3 consecutive unknown_tool failures"


def test_progress_tracker_definitions():
    t = ProgressTracker(stall_turns=4)
    # a read-only call seen for the first time is exploration → progress
    t.note_call("read_file", {"path": "a"}, "data")
    assert t.end_turn() is None and t.idle_turns == 0
    # the same read again: idle
    t.note_call("read_file", {"path": "a"}, "data")
    assert t.end_turn() is None and t.idle_turns == 1
    # a different file: progress
    t.note_call("read_file", {"path": "b"}, "data")
    assert t.end_turn() is None and t.idle_turns == 0
    # a successful write is progress even when repeated
    t.note_call("write_file", {"path": "a", "content": "x"}, "wrote 1 bytes")
    t.note_call("write_file", {"path": "a", "content": "x"}, "wrote 1 bytes")
    assert t.end_turn() is None and t.idle_turns == 0
    # an ERROR result is not progress even for write_file
    t.note_call("edit_file", {"path": "a"}, "ERROR: old_string not found")
    assert t.end_turn() is None and t.idle_turns == 1
    # first time a bash (command, output) pair is seen: progress
    t.note_call("bash", {"command": "pytest"}, "exit code: 1\n1 failed")
    assert t.end_turn() is None and t.idle_turns == 0
    # identical command with identical output: idle
    t.note_call("bash", {"command": "pytest"}, "exit code: 1\n1 failed")
    assert t.end_turn() is None and t.idle_turns == 1
    # same command, new output: progress
    t.note_call("bash", {"command": "pytest"}, "exit code: 0\n5 passed")
    assert t.end_turn() is None and t.idle_turns == 0


def test_bash_fingerprint_ignores_volatile_tokens_but_not_real_changes():
    a = _bash_fingerprint("pytest", "exit code: 0\n5 passed in 24.51s")
    b = _bash_fingerprint("pytest", "exit code: 0\n5 passed in 25.02s")
    c = _bash_fingerprint("pytest", "exit code: 0\n6 passed in 24.51s")
    d = _bash_fingerprint("pytest", "exit code: 1\n5 passed, 1 failed in 24.51s")
    e = _bash_fingerprint("pytest -q", "exit code: 0\n5 passed in 24.51s")
    f = _bash_fingerprint("test -e x", "exit code: 1\n")
    g = _bash_fingerprint("test -e x", "exit code: 0\n")
    assert a == b                      # timing does not matter
    assert a != c                      # a test count change is real (0.5.0 review P2)
    assert a != d                      # 'failed' appeared
    assert a != e                      # different command
    assert f != g                      # exit status alone is real news
    # counters / line numbers / ids in the body are progress, but timestamps and shas are not
    assert _bash_fingerprint("cat n", "exit code: 0\ncounter=41") != _bash_fingerprint("cat n", "exit code: 0\ncounter=42")
    assert _bash_fingerprint("grep x", "exit code: 0\nfoo.py:12: x") != _bash_fingerprint("grep x", "exit code: 0\nfoo.py:13: x")
    assert _bash_fingerprint("date", "exit code: 0\n12:01:03") == _bash_fingerprint("date", "exit code: 0\n12:01:04")
    assert _bash_fingerprint("date -I", "exit code: 0\n2026-08-17T12:01:03Z") == _bash_fingerprint("date -I", "exit code: 0\n2026-08-18T09:00:00Z")
    assert _bash_fingerprint("git log -1", "exit code: 0\nabc1234 fix") == _bash_fingerprint("git log -1", "exit code: 0\ndef5678 fix")
    assert _bash_fingerprint("time x", "exit code: 0\nreal 0.39s") == _bash_fingerprint("time x", "exit code: 0\nreal 0.41s")
    # plain decimal counts are real changes even when long (0.5.1 review); hex ids need a letter
    assert _bash_fingerprint("wc -c f", "exit code: 0\n1234567 f") != _bash_fingerprint("wc -c f", "exit code: 0\n7654321 f")
    assert _bash_fingerprint("git rev-parse", "exit code: 0\n3b8d019a2f20") == _bash_fingerprint("git rev-parse", "exit code: 0\ne312a5d88bd8")


def test_progress_tracker_pytest_rerun_with_new_timing_is_idle():
    t = ProgressTracker(stall_turns=4)
    t.note_call("bash", {"command": "pytest -q"}, "exit code: 0\n5 passed in 24.51s")
    assert t.end_turn() is None and t.idle_turns == 0
    t.note_call("bash", {"command": "pytest -q"}, "exit code: 0\n5 passed in 25.02s")
    assert t.end_turn() is None and t.idle_turns == 1
    t.note_call("bash", {"command": "pytest -q"}, "exit code: 1\n4 passed, 1 failed in 25.02s")
    assert t.end_turn() is None and t.idle_turns == 0


def test_progress_tracker_nudge_then_stalled():
    t = ProgressTracker(stall_turns=4)
    assert t.end_turn() is None          # idle 1
    assert t.end_turn() == "nudge"       # idle 2 == 4 // 2
    assert t.end_turn() is None          # idle 3
    assert t.end_turn() == "stalled"     # idle 4


def test_progress_tracker_nudges_once_per_idle_streak():
    t = ProgressTracker(stall_turns=4)
    t.end_turn(); assert t.end_turn() == "nudge"
    t.note_call("write_file", {"path": "a", "content": "x"}, "ok"); assert t.end_turn() is None
    t.end_turn(); assert t.end_turn() == "nudge"   # a new streak nudges again


def test_progress_tracker_disabled_when_zero():
    t = ProgressTracker(stall_turns=0)
    for _ in range(50):
        assert t.end_turn() is None


def test_runner_stalled_status_after_idle_turns(parts):
    wt, registry, sandbox, transcript, tmp = parts
    loop = _resp(tool_calls=[_call("c", "read_file", {"path": "f.txt"})])
    provider = FakeProvider([loop] * 10)
    r = Runner(provider, registry, sandbox, transcript, model="m", max_turns=50, stall_turns=4)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "stalled"
    assert result.turns == 5            # turn 1 read f.txt for the first time (progress); 4 repeats
    assert result.final_message == "no progress in 4 consecutive turns"
    # the nudge went to the model after 2 idle turns (turn 3) and was transcribed
    fourth = provider.requests[3]
    assert fourth[-1]["role"] == "user" and fourth[-1]["content"] == STALL_NUDGE.format(n=2)
    nudges = [e for e in _events(tmp) if e["event"] == "nudge"]
    assert len(nudges) == 1 and nudges[0]["kind"] == "stall" and nudges[0]["turn"] == 3


def test_runner_empty_replies_count_as_idle_turns(parts):
    wt, registry, sandbox, transcript, tmp = parts
    # two empty replies with stall_turns=2: idle 1 (nudge), idle 2 → stalled (before the 3-strike abort)
    provider = FakeProvider([_resp(content=""), _resp(content=""), _resp(content="")])
    r = Runner(provider, registry, sandbox, transcript, model="m", stall_turns=2)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "stalled" and result.turns == 2


def test_runner_default_stall_turns_is_twelve(parts):
    wt, registry, sandbox, transcript, tmp = parts
    r = Runner(FakeProvider([]), registry, sandbox, transcript, model="m")
    assert r.stall_turns == DEFAULT_STALL_TURNS == 12


@pytest.mark.parametrize("model,flag,env,window,expected", [
    ("qwen/qwen3-coder-next", None, None, 65536, (65536, "provider:fake")),
    ("unknown/model", None, None, None, (DEFAULT_WINDOW, "default")),
    ("qwen/qwen3-coder-next", 8000, None, 65536, (8000, "flag")),
    ("qwen/qwen3-coder-next", None, "9000", 65536, (9000, "env")),
    ("unknown/model", 8000, "9000", None, (8000, "flag")),
    ("unknown/model", None, "", None, (DEFAULT_WINDOW, "default")),
])
def test_resolve_context_window(model, flag, env, window, expected):
    provider = FakeProvider([], context_window=window)
    assert resolve_context_window(model, flag, env, provider) == expected


def test_resolve_context_window_without_a_provider_falls_back_to_default():
    assert resolve_context_window("qwen/qwen3-coder-next", None, None) == (DEFAULT_WINDOW, "default")


def test_resolve_context_window_uses_the_real_openai_table():
    from dirtywork.providers.openai_compat import OpenAICompatClient
    provider = OpenAICompatClient(base_url="http://fake/v1")
    assert resolve_context_window("qwen/qwen3-coder-next", None, None, provider) == \
        (CONTEXT_WINDOWS["qwen/qwen3-coder-next"], "provider:openai")


@pytest.mark.parametrize("env", ["abc", "0", "-5", "1.5"])
def test_resolve_context_window_rejects_bad_env(env):
    with pytest.raises(ValueError):
        resolve_context_window("m", None, env, FakeProvider([]))


def test_runner_context_window_param_sets_budget_and_run_start(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="unknown/model", context_window=1000)
    assert r.context_window == 1000
    assert r.char_budget == int(1000 * 0.75 * 4)
    r.run("s", "t")
    transcript.close()
    start = next(e for e in _events(tmp) if e["event"] == "run_start")
    assert start["context_window"] == 1000


def test_finish_after_idle_turns_completes_not_stalled(parts):
    wt, registry, sandbox, transcript, tmp = parts
    idle = _resp(tool_calls=[_call("c", "read_file", {"path": "f.txt"})])
    done = _resp(tool_calls=[_call("f", "finish", {"summary": "all done"})])
    provider = FakeProvider([idle, idle, idle, done])
    r = Runner(provider, registry, sandbox, transcript, model="m", stall_turns=4)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.final_message == "all done"
    assert result.turns == 4


def _no_consecutive_user_messages(requests):
    for req in requests:
        roles = [m["role"] for m in req]
        assert all(not (a == "user" and b == "user") for a, b in zip(roles, roles[1:])), roles


def test_empty_reply_on_stall_nudge_turn_sends_one_merged_user_message(parts):
    wt, registry, sandbox, transcript, tmp = parts
    idle = _resp(tool_calls=[_call("c", "read_file", {"path": "f.txt"})])
    # stall_turns=4 → the stall nudge fires when idle_turns reaches 2: turn 1 reads f.txt (new →
    # progress), turn 2 repeats it (idle 1), turn 3 is an empty reply (idle 2 → stall nudge)
    provider = FakeProvider([idle, idle, _resp(content=""), _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m", stall_turns=4)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    fourth = provider.requests[3]
    assert fourth[-1]["role"] == "user"
    assert fourth[-1]["content"] == NUDGES["empty"] + "\n\n" + STALL_NUDGE.format(n=2)
    assert fourth[-2]["role"] == "assistant"
    _no_consecutive_user_messages(provider.requests)
    kinds = [e["kind"] for e in _events(tmp) if e["event"] == "nudge"]
    assert kinds == ["empty", "stall"]


def test_malformed_entries_on_stall_nudge_turn_send_one_merged_user_message(parts):
    wt, registry, sandbox, transcript, tmp = parts
    idle = _resp(tool_calls=[_call("c", "read_file", {"path": "f.txt"})])
    bad_entry = _resp(tool_calls=[_bad_entry()],
                      usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([idle, idle, bad_entry, _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m", stall_turns=4)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    fourth = provider.requests[3]
    assert fourth[-1]["role"] == "user"
    assert "were malformed" in fourth[-1]["content"]
    assert STALL_NUDGE.format(n=2) in fourth[-1]["content"]
    _no_consecutive_user_messages(provider.requests)


def test_malformed_entry_abort_reports_first_threshold(parts):
    wt, registry, sandbox, transcript, tmp = parts
    six_bad = _resp(tool_calls=[_bad_entry() for _ in range(6)],
                    usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([six_bad])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    assert result.final_message == "aborted after 3 consecutive malformed_entry failures"


def test_runner_exploring_new_files_is_not_a_stall(parts):
    wt, registry, sandbox, transcript, tmp = parts
    for i in range(20):
        (wt / f"m{i}.py").write_text(f"# {i}\n")
    reads = [_resp(tool_calls=[_call(f"c{i}", "read_file", {"path": f"m{i}.py"})]) for i in range(20)]
    provider = FakeProvider(reads + [_resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m", max_turns=50, stall_turns=4)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed" and result.turns == 21


def test_runner_argument_noise_does_not_count_as_progress(parts):
    wt, registry, sandbox, transcript, tmp = parts
    # the same read spelled four ways: foo / ./foo / with an ignored key / with an explicit default
    variants = [
        _call("c1", "read_file", {"path": "f.txt"}),
        _call("c2", "read_file", {"path": "./f.txt"}),
        _call("c3", "read_file", {"path": "f.txt", "description": "look again"}),
        _call("c4", "read_file", {"path": "f.txt", "offset": 0, "limit": 400}),
        _call("c5", "read_file", {"path": "f.txt/"}),
    ]
    provider = FakeProvider([_resp(tool_calls=[v]) for v in variants] + [_resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m", stall_turns=4)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "stalled"          # 1 progress + 4 idle repeats
    assert result.turns == 5


def test_plain_llm_error_escapes_the_runner(parts):
    # A transport-level LLMError is NOT caught here: __main__._fail_run handles
    # it so a docker volume's work is exported before the sandbox is stopped.
    wt, registry, sandbox, transcript, tmp = parts

    class DeadProvider(FakeProvider):
        def chat(self, *a, **k):
            raise LLMError("connection dropped")

    r = Runner(DeadProvider([]), registry, sandbox, transcript, model="m")
    with pytest.raises(LLMError):
        r.run("s", "t")
    transcript.close()


def test_runner_context_window_defaults_from_the_provider(parts):
    wt, registry, sandbox, transcript, tmp = parts
    r = Runner(FakeProvider([], context_window=65536), registry, sandbox, transcript,
               model="qwen/qwen3-coder-next")
    assert r.context_window == 65536
    r2 = Runner(FakeProvider([], context_window=None), registry, sandbox, transcript, model="m")
    assert r2.context_window == DEFAULT_WINDOW


def test_runner_context_window_zero_is_not_replaced_by_the_provider(parts):
    wt, registry, sandbox, transcript, tmp = parts
    r = Runner(FakeProvider([], context_window=65536), registry, sandbox, transcript,
               model="qwen/qwen3-coder-next", context_window=0)
    assert r.context_window == 0


def test_repeat_tracker_counts_only_identical_failures():
    from dirtywork.runner import RepeatTracker
    t = RepeatTracker(limit=3)
    assert t.note_bash("pytest", "exit code: 1\n1 failed in 2.10s") is None
    # a timing-only difference is the SAME failure (existing _bash_fingerprint)
    assert t.note_bash("pytest", "exit code: 1\n1 failed in 2.44s") is None
    assert t.repeats == 2
    assert t.note_bash("pytest", "exit code: 1\n1 failed in 2.99s") == "stuck"
    assert t.stuck_on() == {"command": "pytest",
                            "output": "exit code: 1\n1 failed in 2.99s",
                            "repeats": 3}
    # a different failure restarts the streak at 1
    assert t.note_bash("pytest", "exit code: 2\ncollection error") is None
    assert t.repeats == 1
    # ERROR: and BLOCKED: results are failures too
    t2 = RepeatTracker(limit=2)
    assert t2.note_bash("sleep 999", "ERROR: command timed out after 120s.") is None
    assert t2.note_bash("sleep 999", "ERROR: command timed out after 120s.") == "stuck"


def test_only_the_same_command_passing_resets_the_stuck_streak():
    from dirtywork.runner import RepeatTracker
    t = RepeatTracker(limit=3)
    assert t.note_bash("pytest", "exit code: 1\nfailed") is None
    # a passing run of ANOTHER command (git status, cat, ls ...) neither counts
    # nor resets -- exactly like a non-bash tool call in between
    assert t.note_bash("git status", "exit code: 0\nclean") is None
    assert t.repeats == 1
    assert t.note_bash("pytest", "exit code: 1\nfailed") is None
    assert t.repeats == 2
    # the SAME command going green ends the episode: the streak restarts
    assert t.note_bash("pytest", "exit code: 0\n3 passed") is None
    assert t.repeats == 0
    assert t.note_bash("pytest", "exit code: 1\nfailed") is None
    assert t.note_bash("pytest", "exit code: 1\nfailed") is None
    assert t.note_bash("pytest", "exit code: 1\nfailed") == "stuck"


def test_repeat_tracker_limit_zero_disables():
    from dirtywork.runner import RepeatTracker
    t = RepeatTracker(limit=0)
    for _ in range(10):
        assert t.note_bash("pytest", "exit code: 1\nfailed") is None
    assert t.repeats == 0


def test_stuck_status_ends_the_run_and_reports_stuck_on(parts):
    wt, registry, sandbox, transcript, tmp = parts
    failing = _resp(tool_calls=[_call("c", "bash", {"command": "exit 7"})])
    provider = FakeProvider([failing] * 5)
    r = Runner(provider, registry, sandbox, transcript, model="m", max_turns=10,
               stuck_repeats=3)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "stuck"
    assert result.turns == 3
    assert result.extra["stuck_on"]["command"] == "exit 7"
    assert result.extra["stuck_on"]["repeats"] == 3
    assert result.extra["stuck_on"]["output"].startswith("exit code: 7")
    end = [e for e in _events(tmp) if e["event"] == "run_end"][-1]
    assert end["status"] == "stuck"
    assert end["stuck_on"]["command"] == "exit 7"


def test_stuck_on_is_null_on_every_other_status(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="all done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.extra["stuck_on"] is None


def test_last_tool_result_and_assistant_text_ride_on_every_result(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(content="looking now", tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
        _resp(content="", tool_calls=[_call("c2", "list_dir", {"path": "."})]),
        _resp(content="all done"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    last = result.extra["last_tool_result"]
    assert last["tool"] == "list_dir"
    assert '"path": "."' in last["args"]
    assert "f.txt" in last["result"]
    # the empty second reply must not overwrite the last non-empty text, and the
    # plain answer that ended the run is the newest non-empty one
    assert result.extra["last_assistant_text"] == "all done"


def test_last_tool_result_ignores_finish_and_is_null_when_no_tool_ran(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="nothing to do")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    assert result.extra["last_tool_result"] is None

    transcript2 = Transcript(tmp / "t2.jsonl")
    registry2 = default_registry(transcript=transcript2)
    provider2 = FakeProvider([
        _resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
    ])
    r2 = Runner(provider2, registry2, HostSandbox(wt), transcript2, model="m")
    result2 = r2.run("s", "t")
    transcript2.close()
    assert result2.status == "completed"
    assert result2.extra["last_tool_result"]["tool"] == "read_file"   # finish is skipped
    assert result2.extra["last_assistant_text"] is None               # both replies were empty
