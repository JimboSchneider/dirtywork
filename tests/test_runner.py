from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from dirtywork.llm import LLMTimeout
from dirtywork.runner import (
    DEFAULT_WINDOW,
    FailureTracker,
    MAX_TOTAL_CONSECUTIVE_FAILURES,
    NUDGES,
    RunResult,
    Runner,
    TRIM_MARKER,
    _valid_tool_call,
    classify_text_reply,
    strip_think,
    trim_messages,
)
from dirtywork.sandbox.host import HostSandbox
from dirtywork.tools import ToolExecutor
from dirtywork.transcript import Transcript


def _resp(content=None, tool_calls=None, usage=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}],
            "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5}}


def _call(call_id, name, args: dict):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.timeouts = []

    def chat(self, model, messages, tools, temperature=None, max_tokens=4096, timeout=None):
        self.requests.append([json.loads(json.dumps(m)) for m in messages])
        self.timeouts.append(timeout)
        return self.responses.pop(0)


@pytest.fixture()
def parts(tmp_path: Path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "f.txt").write_text("data\n")
    transcript = Transcript(tmp_path / "t.jsonl")
    executor = ToolExecutor(HostSandbox(wt), transcript=transcript)
    return wt, executor, transcript, tmp_path


def _events(tmp_path: Path):
    return [json.loads(l) for l in (tmp_path / "t.jsonl").read_text().splitlines()]


def test_two_turn_run(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([
        _resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
        _resp(content="Done: file says data"),
    ])
    r = Runner(client, executor, transcript, model="qwen/qwen3-coder-next")
    result = r.run("sysprompt", "read the file")
    transcript.close()

    assert result.status == "completed"
    assert result.turns == 2
    assert "Done" in result.final_message
    assert result.usage == {"prompt_tokens": 20, "completion_tokens": 10}

    # second request must include the tool result message with matching id
    second = client.requests[1]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "c1"
    assert "data" in tool_msgs[0]["content"]

    kinds = [e["event"] for e in _events(tmp)]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end"
    assert "assistant" in kinds and "tool_result" in kinds


def test_max_turns(parts):
    wt, executor, transcript, tmp = parts
    loop_resp = _resp(tool_calls=[_call("c", "list_dir", {"path": "."})])
    client = FakeClient([loop_resp] * 3)
    r = Runner(client, executor, transcript, model="m", max_turns=3)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "max_turns"
    assert result.turns == 3


def test_malformed_args_three_strikes(parts):
    wt, executor, transcript, tmp = parts
    bad = _resp(tool_calls=[{"id": "x", "type": "function",
                             "function": {"name": "read_file", "arguments": "{not json"}}])
    client = FakeClient([bad, bad, bad])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"


def test_unknown_tool_counts_as_strike_but_recovers(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([
        _resp(tool_calls=[_call("c1", "no_such_tool", {})]),
        _resp(content="ok done"),
    ])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    # the model got an error message back as the tool result
    second = client.requests[1]
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
         "tool_calls": [{"id": "1", "type": "function",
                         "function": {"name": "write_file", "arguments": "a" * 1000}}]},
    ]
    # No role=="tool" messages exist to trim, so this only passes if the
    # tool_call arguments are counted toward the budget in the first place.
    assert trim_messages(msgs, char_budget=500) is False


def test_arguments_null_treated_as_empty(parts):
    wt, executor, transcript, tmp = parts
    call = {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": None}}
    client = FakeClient([_resp(tool_calls=[call]), _resp(content="done")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    tool_msgs = [m for m in client.requests[1] if m["role"] == "tool"]
    assert "f.txt" in tool_msgs[0]["content"]

    # The resent history must carry the canonical wire shape: type "function" and
    # arguments coerced to a string, even though the original entry had arguments:
    # None.
    second = client.requests[1]
    assistant_msg = next(m for m in second
                          if m["role"] == "assistant" and m.get("tool_calls"))
    assert assistant_msg["tool_calls"][0]["type"] == "function"
    assert assistant_msg["tool_calls"][0]["function"]["arguments"] == "{}"


def test_valid_call_missing_type_field_canonicalized_on_resend(parts):
    # No malformed siblings -- this is the previously-verbatim path. The call is
    # otherwise valid (non-empty id, function.name) but omits "type" entirely,
    # which a strict server still requires on resend.
    wt, executor, transcript, tmp = parts
    call = {"id": "c1", "function": {"name": "list_dir", "arguments": json.dumps({"path": "."})}}
    client = FakeClient([_resp(tool_calls=[call]), _resp(content="done")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"

    second = client.requests[1]
    assistant_msg = next(m for m in second
                          if m["role"] == "assistant" and m.get("tool_calls"))
    assert assistant_msg["tool_calls"][0]["type"] == "function"


def test_malformed_tool_call_entry_recovers(parts):
    # Missing "function" entirely is structurally invalid (not just an unknown
    # tool name) and now routes through the malformed-tool-call recovery path.
    wt, executor, transcript, tmp = parts
    bad = {"id": "c1", "type": "function"}
    client = FakeClient([_resp(tool_calls=[bad]), _resp(content="ok done")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    second = client.requests[1]
    assert not [m for m in second if m["role"] == "tool"]
    user_msgs = [m for m in second if m["role"] == "user"]
    assert any("malformed" in (m.get("content") or "").lower() for m in user_msgs)


def test_malformed_response_is_model_error(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([{"choices": []}])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"


def test_null_message_is_model_error(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([{"choices": [{"message": None}]}])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"


def test_null_usage_tolerated(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([{"choices": [{"message": {"role": "assistant", "content": "hi"}}], "usage": None}])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.usage == {"prompt_tokens": 0, "completion_tokens": 0}


def test_strike_counter_resets_on_success(parts):
    wt, executor, transcript, tmp = parts
    bad = _resp(tool_calls=[{"id": "x", "type": "function",
                             "function": {"name": "read_file", "arguments": "{not json"}}])
    good = _resp(tool_calls=[_call("g", "list_dir", {"path": "."})])
    client = FakeClient([bad, bad, good, bad, bad, _resp(content="done")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"


def test_timeout_status(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([_resp(content="never reached")])
    r = Runner(client, executor, transcript, model="m", timeout=-1)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "timeout"
    assert result.turns == 0


def test_context_exhausted_status(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([])
    r = Runner(client, executor, transcript, model="m")
    r.char_budget = 10
    result = r.run("s" * 100, "t")
    transcript.close()
    assert result.status == "context_exhausted"


def test_length_finish_reason_gives_helpful_hint(parts):
    wt, executor, transcript, tmp = parts
    truncated = {
        "choices": [{
            "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "c", "type": "function",
                    "function": {"name": "write_file",
                                 "arguments": "{\"path\": \"x\", \"content\": \"abc"},
                }],
            },
            "finish_reason": "length",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    client = FakeClient([truncated, _resp(content="done")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    tool_msgs = [m for m in client.requests[1] if m["role"] == "tool"]
    assert "cut off at the token limit" in tool_msgs[0]["content"]


def test_run_start_includes_run_info(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([_resp(content="done")])
    r = Runner(client, executor, transcript, model="m", run_info={"repo": "/r"})
    result = r.run("s", "t")
    transcript.close()
    events = _events(tmp)
    run_start = next(e for e in events if e["event"] == "run_start")
    assert run_start["repo"] == "/r"


def test_chat_receives_bounded_timeout(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([_resp(content="done")])
    r = Runner(client, executor, transcript, model="m", timeout=30)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert client.timeouts and client.timeouts[0] is not None
    assert 0 < client.timeouts[0] <= 30


def test_llm_timeout_near_deadline_gives_timeout_status(parts):
    wt, executor, transcript, tmp = parts

    class SlowTimeoutClient:
        def chat(self, *a, **k):
            time.sleep(0.3)
            raise LLMTimeout("request timed out")

    r = Runner(SlowTimeoutClient(), executor, transcript, model="m", timeout=0.2)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "timeout"


def test_llm_timeout_far_from_deadline_gives_model_error(parts):
    wt, executor, transcript, tmp = parts

    class ImmediateTimeoutClient:
        def chat(self, *a, **k):
            raise LLMTimeout("request timed out")

    r = Runner(ImmediateTimeoutClient(), executor, transcript, model="m", timeout=1800)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"


def test_malformed_tool_call_null_entry_recovers(parts):
    wt, executor, transcript, tmp = parts
    bad = {"choices": [{"message": {"role": "assistant", "content": None,
                                     "tool_calls": [None]}}]}
    client = FakeClient([bad, _resp(content="ok done")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"

    second = client.requests[1]
    # No protocol-invalid tool result with an unmatched empty tool_call_id.
    assert not any(m["role"] == "tool" and m["tool_call_id"] == "" for m in second)
    # No None entries survive inside any assistant message's tool_calls.
    for m in second:
        if m["role"] == "assistant":
            assert None not in (m.get("tool_calls") or [])
    # A protocol-valid user message calls out the malformed tool call instead.
    user_msgs = [m for m in second if m["role"] == "user"]
    assert any("malformed" in (m.get("content") or "").lower() for m in user_msgs)


def test_mixed_null_and_valid_tool_call_recovers(parts):
    wt, executor, transcript, tmp = parts
    valid_call = _call("c1", "list_dir", {"path": "."})
    bad = {"choices": [{"message": {"role": "assistant", "content": None,
                                     "tool_calls": [None, valid_call]}}]}
    client = FakeClient([bad, _resp(content="ok done")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"

    second = client.requests[1]
    assistant_idx = next(i for i, m in enumerate(second)
                          if m["role"] == "assistant" and m.get("tool_calls"))
    assert second[assistant_idx]["tool_calls"] == [valid_call]
    assert None not in second[assistant_idx]["tool_calls"]

    # The valid call's tool result must directly follow the assistant message.
    tool_result = second[assistant_idx + 1]
    assert tool_result["role"] == "tool"
    assert tool_result["tool_call_id"] == "c1"
    assert "f.txt" in tool_result["content"]

    # The user correction comes after the valid tool result(s).
    correction_idx = next(i for i, m in enumerate(second)
                           if m["role"] == "user"
                           and "malformed" in (m.get("content") or "").lower())
    assert correction_idx > assistant_idx + 1


def test_empty_object_tool_call_recovers(parts):
    # {} is dict-shaped but has no id/function — must not pass the validity filter.
    wt, executor, transcript, tmp = parts
    client = FakeClient([_resp(tool_calls=[{}]), _resp(content="ok done")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"

    second = client.requests[1]
    assert not any(m["role"] == "tool" and m.get("tool_call_id") == "" for m in second)
    user_msgs = [m for m in second if m["role"] == "user"]
    assert any("malformed" in (m.get("content") or "").lower() for m in user_msgs)


def test_empty_id_tool_call_recovers(parts):
    # Valid function, but an empty string id can't be matched to a tool result.
    wt, executor, transcript, tmp = parts
    bad = {"id": "", "type": "function",
           "function": {"name": "list_dir", "arguments": json.dumps({"path": "."})}}
    client = FakeClient([_resp(tool_calls=[bad]), _resp(content="ok done")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"

    second = client.requests[1]
    assert not any(m["role"] == "tool" and m.get("tool_call_id") == "" for m in second)
    user_msgs = [m for m in second if m["role"] == "user"]
    assert any("malformed" in (m.get("content") or "").lower() for m in user_msgs)


def test_missing_function_name_tool_call_recovers(parts):
    # Non-empty id, but the function object has no name field.
    wt, executor, transcript, tmp = parts
    bad = {"id": "c1", "type": "function", "function": {"arguments": "{}"}}
    client = FakeClient([_resp(tool_calls=[bad]), _resp(content="ok done")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"

    second = client.requests[1]
    assert not any(m["role"] == "tool" for m in second)
    user_msgs = [m for m in second if m["role"] == "user"]
    assert any("malformed" in (m.get("content") or "").lower() for m in user_msgs)


def test_valid_tool_call_predicate():
    valid = _call("c1", "list_dir", {"path": "."})
    assert _valid_tool_call(valid) is True

    assert _valid_tool_call(None) is False  # not a dict
    assert _valid_tool_call({}) is False  # no id, no function
    assert _valid_tool_call({"id": "", "function": {"name": "list_dir"}}) is False  # empty id
    assert _valid_tool_call({"id": "c1", "function": {"arguments": "{}"}}) is False  # no name
    assert _valid_tool_call({"id": "c1", "function": {"name": "list_dir",
                                                        "arguments": 123}}) is False  # bad args type


def test_tool_calls_non_list_treated_as_absent(parts):
    wt, executor, transcript, tmp = parts
    resp = {"choices": [{"message": {"role": "assistant", "content": "done",
                                      "tool_calls": "notalist"}}]}
    client = FakeClient([resp])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.turns == 1


def test_three_consecutive_malformed_tool_calls_aborts(parts):
    wt, executor, transcript, tmp = parts
    bad = {"choices": [{"message": {"role": "assistant", "content": None,
                                     "tool_calls": [None]}}]}
    client = FakeClient([bad, bad, bad])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"


def test_interrupted_status(parts):
    wt, executor, transcript, tmp = parts
    class InterruptingClient:
        def chat(self, *a, **k):
            raise KeyboardInterrupt
    r = Runner(InterruptingClient(), executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "interrupted"
    events = _events(tmp)
    assert events[-1]["event"] == "run_end"


def test_usage_ignores_non_finite_from_server(parts):
    wt, executor, transcript, tmp_path = parts
    client = FakeClient([_resp(content="done",
                               usage={"prompt_tokens": float("nan"),
                                      "completion_tokens": float("inf")})])
    r = Runner(client, executor, transcript, "qwen/qwen3-coder-next")
    result = r.run("sys", "task")
    assert result.usage == {"prompt_tokens": 0, "completion_tokens": 0}
    json.dumps(result.usage, allow_nan=False)  # stdout contract stays valid JSON
    transcript.close()
    text = (tmp_path / "t.jsonl").read_text()
    assert "NaN" not in text and "Infinity" not in text


def test_finalize_merges_into_run_end_and_result_extra(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([_resp(content="done")])
    r = Runner(client, executor, transcript, model="m",
              finalize=lambda: {"diff_stat": " 1 file changed"})
    result = r.run("s", "t")
    transcript.close()
    assert result.extra == {"diff_stat": " 1 file changed"}
    events = _events(tmp)
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end["diff_stat"] == " 1 file changed"


def test_finalize_exception_recorded_status_preserved(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([_resp(content="done")])

    def boom():
        raise RuntimeError("disk gone")

    r = Runner(client, executor, transcript, model="m", finalize=boom)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    events = _events(tmp)
    run_end = next(e for e in events if e["event"] == "run_end")
    assert "disk gone" in run_end["finalize_error"]


def test_assistant_text_capped_in_transcript_full_text_resent(parts):
    wt, executor, transcript, tmp = parts
    # Over MAX_ASSISTANT_TEXT_CHARS (64_000) but comfortably under the
    # default model's char_budget (~98_304 for the fallback DEFAULT_WINDOW),
    # so trim_messages doesn't ALSO trigger context_exhausted — this test is
    # about the transcript-only cap, not the trim path.
    huge_text = "y" * 70_000
    client = FakeClient([
        _resp(tool_calls=[_call("c1", "list_dir", {"path": "."})], content=huge_text),
        _resp(content="done"),
    ])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"

    events = _events(tmp)
    assistant_event = next(e for e in events if e["event"] == "assistant")
    assert len(assistant_event["text"]) < 70_000
    assert "truncated" in assistant_event["text"]

    # the resent history to the model must keep the FULL text
    second = client.requests[1]
    assistant_msg = next(m for m in second if m["role"] == "assistant" and m.get("tool_calls"))
    assert assistant_msg["content"] == huge_text


def test_budget_exceeded_from_executor_ends_run(parts):
    wt, executor, transcript, tmp = parts
    from dirtywork.budget import BudgetExceeded

    class BudgetBustingExecutor:
        def execute(self, name, args):
            raise BudgetExceeded("worktree exceeds 2048 MB")

    client = FakeClient([_resp(tool_calls=[_call("c1", "write_file", {"path": "x", "content": "y"})])])
    r = Runner(client, BudgetBustingExecutor(), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "budget_exceeded"
    assert "2048 MB" in result.final_message
    events = _events(tmp)
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end["status"] == "budget_exceeded"


def test_finish_tool_ends_run_after_other_calls_in_turn(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([
        _resp(tool_calls=[
            _call("f1", "finish", {"summary": "wrote g.txt"}),
            _call("w1", "write_file", {"path": "g.txt", "content": "hi\n"}),
        ]),
    ])
    r = Runner(client, executor, transcript, model="m")
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
    wt, executor, transcript, tmp = parts
    client = FakeClient([_resp(tool_calls=[_call("f1", "finish", {})])])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.final_message == ""


def test_finish_with_malformed_args_does_not_end_run(parts):
    wt, executor, transcript, tmp = parts
    bad = _resp(tool_calls=[{"id": "f1", "type": "function",
                             "function": {"name": "finish", "arguments": "{not json"}}])
    client = FakeClient([bad, _resp(tool_calls=[_call("f2", "finish", {"summary": "ok"})])])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.turns == 2
    tool_msgs = [m for m in client.requests[1] if m["role"] == "tool"]
    assert "malformed tool arguments" in tool_msgs[0]["content"]


def test_unknown_tool_error_mentions_finish(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([
        _resp(tool_calls=[_call("c1", "no_such_tool", {})]),
        _resp(content="ok done"),
    ])
    r = Runner(client, executor, transcript, model="m")
    r.run("s", "t")
    transcript.close()
    tool_msgs = [m for m in client.requests[1] if m["role"] == "tool"]
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
    wt, executor, transcript, tmp = parts
    bad_args = _resp(tool_calls=[{"id": "x", "type": "function",
                                  "function": {"name": "read_file", "arguments": "{not json"}}])
    unknown = _resp(tool_calls=[_call("u", "no_such_tool", {})])
    wrong_type = _resp(tool_calls=[_call("t", "read_file", {})])   # missing required arg → TypeError → bad_args
    client = FakeClient([bad_args, unknown, wrong_type, _resp(content="ok done")])
    r = Runner(client, executor, transcript, model="m")
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
    wt, executor, transcript, tmp = parts
    client = FakeClient([_resp(content=""), _resp(content="done for real")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.turns == 2
    assert result.final_message == "done for real"
    second = client.requests[1]
    assert second[-1]["role"] == "user" and second[-1]["content"] == NUDGES["empty"]
    assert second[-2] == {"role": "assistant", "content": ""}
    events = _events(tmp)
    nudges = [e for e in events if e["event"] == "nudge"]
    assert len(nudges) == 1
    assert nudges[0]["kind"] == "empty" and nudges[0]["turn"] == 1


def test_think_only_reply_is_nudged(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([_resp(content=_THINK + "hmm" + _THINK_END), _resp(content="ok")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed" and result.turns == 2


def test_text_tool_call_reply_is_nudged(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([_resp(content=_tag("tool_call") + '{"name":"bash","arguments":{}}' + _tag("/tool_call")),
                         _resp(content="ok")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed" and result.turns == 2
    assert client.requests[1][-1]["content"] == NUDGES["text_tool_call"]


def test_length_cutoff_without_tool_calls_is_not_completed(parts):
    wt, executor, transcript, tmp = parts
    cut = {"choices": [{"message": {"role": "assistant", "content": "I will now"},
                        "finish_reason": "length"}],
           "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    client = FakeClient([cut, _resp(content="ok")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed" and result.turns == 2
    assert client.requests[1][-1]["content"] == NUDGES["truncated"]


def test_three_empty_replies_abort_as_model_error(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([_resp(content=""), _resp(content=""), _resp(content="")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    assert result.final_message == "aborted after 3 consecutive empty_reply failures"


def test_successful_call_resets_empty_reply_count(parts):
    wt, executor, transcript, tmp = parts
    client = FakeClient([_resp(content=""), _resp(content=""),
                         _resp(tool_calls=[_call("c", "read_file", {"path": "f.txt"})]),
                         _resp(content=""), _resp(content=""), _resp(content="done")])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed" and result.turns == 6


def test_abort_message_names_the_kind(parts):
    wt, executor, transcript, tmp = parts
    unknown = _resp(tool_calls=[_call("u", "no_such_tool", {})])
    client = FakeClient([unknown, unknown, unknown])
    r = Runner(client, executor, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    assert result.final_message == "aborted after 3 consecutive unknown_tool failures"
