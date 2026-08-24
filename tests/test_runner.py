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
    EMPTY_REPLY_PLACEHOLDER,
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
    fits, newly_trimmed = trim_messages(msgs, char_budget=1300)
    assert fits is True
    assert newly_trimmed == 1                      # spec §2.2: replaced ON THIS CALL
    assert msgs[1]["content"] == TRIM_MARKER      # oldest trimmed first
    assert msgs[3]["content"] == "y" * 1000        # newer kept
    assert msgs[0]["content"] == "s" * 100         # system never trimmed


def test_trim_does_not_recount_a_result_it_already_trimmed():
    # The count is what makes `trimmed_turns` mean "turns on which trimming
    # happened" rather than "markers currently in the history".
    msgs = [
        {"role": "system", "content": "s" * 100},
        {"role": "tool", "tool_call_id": "1", "content": "x" * 1000},
        {"role": "assistant", "content": "a" * 100},
        {"role": "tool", "tool_call_id": "2", "content": "y" * 1000},
    ]
    assert trim_messages(msgs, char_budget=1300) == (True, 1)
    assert trim_messages(msgs, char_budget=1300) == (True, 0)


def test_trim_cannot_fit():
    msgs = [{"role": "system", "content": "s" * 5000}]
    assert trim_messages(msgs, char_budget=100) == (False, 0)


def test_trim_counts_tool_call_arguments():
    msgs = [
        {"role": "assistant", "content": None,
         "tool_calls": [ToolCall(id="1", name="write_file", arguments=None,
                                 error=None, raw_arguments="a" * 1000)]},
    ]
    # No role=="tool" messages exist to trim, so this only passes if the
    # tool_call arguments are counted toward the budget in the first place.
    assert trim_messages(msgs, char_budget=500) == (False, 0)


def _scripted_trim(monkeypatch, script):
    """Drive Runner.run's trim bookkeeping with a scripted trim_messages, so
    the counting rule is tested without also re-testing the trim arithmetic
    (which the four unit tests above already pin). The runner looks the name up
    on the module at call time, so patching the module attribute is enough."""
    import dirtywork.runner as runner_mod
    steps = iter(script)
    monkeypatch.setattr(runner_mod, "trim_messages",
                        lambda messages, char_budget: next(steps))


def test_trimmed_turns_counts_the_final_failing_call_when_it_trimmed(parts, monkeypatch):
    wt, registry, sandbox, transcript, tmp = parts
    _scripted_trim(monkeypatch, [(True, 0), (True, 2), (True, 1), (False, 3)])
    provider = FakeProvider([_resp(tool_calls=[_call(f"c{i}", "read_file", {"path": "f.txt"})])
                             for i in range(3)])
    r = Runner(provider, registry, sandbox, transcript, model="m", max_turns=10)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "context_exhausted"
    # turns 2 and 3 trimmed, and so did the call that then gave up
    assert result.extra["trimmed_turns"] == 3
    end = [e for e in _events(tmp) if e["event"] == "run_end"][-1]
    assert end["trimmed_turns"] == 3


def test_trimmed_turns_ignores_a_final_failing_call_that_trimmed_nothing(parts, monkeypatch):
    wt, registry, sandbox, transcript, tmp = parts
    _scripted_trim(monkeypatch, [(True, 0), (True, 1), (True, 1), (False, 0)])
    provider = FakeProvider([_resp(tool_calls=[_call(f"c{i}", "read_file", {"path": "f.txt"})])
                             for i in range(3)])
    r = Runner(provider, registry, sandbox, transcript, model="m", max_turns=10)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "context_exhausted"
    assert result.extra["trimmed_turns"] == 2


def test_trimmed_turns_is_zero_on_an_ordinary_run(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="all done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert result.extra["trimmed_turns"] == 0


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


def test_empty_reply_after_a_tool_turn_gets_the_placeholder(parts):
    # Spec §5 / probe S16: the F5 truncation shape. LM Studio drops an empty
    # assistant message, which leaves `tool -> user` and a 400 from Mistral.
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
        _resp(content="", finish_reason="length"),
        _resp(content="done"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    third = provider.requests[2]
    assert third[-3]["role"] == "tool"
    assert third[-2] == {"role": "assistant", "content": EMPTY_REPLY_PLACEHOLDER}
    assert third[-1]["role"] == "user" and third[-1]["content"] == NUDGES["truncated"]
    events = _events(tmp)
    assistants = [e for e in events if e["event"] == "assistant"]
    assert "placeholder" not in assistants[0]                # a tool-call turn: never a placeholder
    assert assistants[1]["placeholder"] == EMPTY_REPLY_PLACEHOLDER and assistants[1]["text"] == ""
    assert "placeholder" not in assistants[2]


def test_think_only_and_text_tool_call_replies_get_no_placeholder(parts):
    wt, registry, sandbox, transcript, tmp = parts
    think = "<" + "think>hmm</" + "think>"
    provider = FakeProvider([_resp(content=think),
                             _resp(content="<tool_call>{}</tool_call>"),
                             _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    r.run("s", "t")
    transcript.close()
    assistants = [e for e in _events(tmp) if e["event"] == "assistant"]
    assert all("placeholder" not in e for e in assistants)
    last = provider.requests[2]                              # [system, user(task), assistant, ...]
    assert last[2]["content"] == think                       # the model's own text is what is sent


def test_malformed_only_turn_gets_placeholder_and_a_user_nudge_without_touching_prior_turns(parts):
    # Spec §5 + §9.4: no addressable call and no text -> placeholder; the
    # malformed nudge is a user message; the previous turn's tool message is
    # byte-equal before and after, and its event never grows a follow_up.
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
        _resp(tool_calls=[_bad_entry()]),
        _resp(content="done"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    second, third = provider.requests[1], provider.requests[2]
    prior_tool_before = next(m for m in second if m["role"] == "tool")
    prior_tool_after = next(m for m in third if m["role"] == "tool")
    assert prior_tool_before == prior_tool_after
    assert third[-2] == {"role": "assistant", "content": EMPTY_REPLY_PLACEHOLDER}
    assert third[-1]["role"] == "user" and "were malformed" in third[-1]["content"]
    events = _events(tmp)
    prior_event = next(e for e in events if e["event"] == "tool_result" and e["tool"] == "read_file")
    assert "follow_up" not in prior_event
    assert [e["event"] for e in events if e["event"] == "nudge"] == []   # (Task 5 adds malformed_entry)


def test_malformed_only_turn_with_text_gets_no_placeholder(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="I'll call a tool", tool_calls=[_bad_entry()]),
                             _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    r.run("s", "t")
    transcript.close()
    second = provider.requests[1]
    assert second[-2] == {"role": "assistant", "content": "I'll call a tool"}
    assert second[-1]["role"] == "user"
    assert "placeholder" not in next(e for e in _events(tmp) if e["event"] == "assistant")


def test_malformed_only_length_turn_gets_placeholder_and_no_truncated_nudge(parts):
    # The turn takes the tool path (resp.tool_calls is non-empty) -> malformed
    # nudge only, never `truncated`.
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="", tool_calls=[_bad_entry()], finish_reason="length"),
                             _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    r.run("s", "t")
    transcript.close()
    second = provider.requests[1]
    assert second[-2] == {"role": "assistant", "content": EMPTY_REPLY_PLACEHOLDER}
    assert second[-1]["role"] == "user" and "were malformed" in second[-1]["content"]
    assert NUDGES["truncated"] not in second[-1]["content"]


def test_third_malformed_entry_strike_ends_after_recording_the_placeholder(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad = _resp(tool_calls=[_bad_entry()])
    provider = FakeProvider([bad, bad, bad])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    events = _events(tmp)
    assistants = [e for e in events if e["event"] == "assistant"]
    assert len(assistants) == 3 and all(e["placeholder"] == EMPTY_REPLY_PLACEHOLDER for e in assistants)
    assert events[-1]["event"] == "run_end"
    assert events[-2]["event"] == "tool_result"          # the strike itself; no nudge, no user message after it


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
    # Spec §1.3: the fixture already carries a recoverable `path`, so this is
    # now the PATH-RECOVERED case and pins the whole sentence.
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
    assert tool_msgs[0]["content"] == (
        "ERROR: your write_file for 'x' was cut off at the token limit — nothing was "
        "written. Write the file in chunks: write_file with the first part, then "
        "append_file for each following part.")


_GENERIC_TRUNCATION = ("ERROR: your {tool} call was cut off at the token limit before it "
                       "completed. Emit smaller tool calls — for a large file, write_file "
                       "the first part and append_file the rest.")


def test_length_truncation_of_a_non_write_file_tool_gives_the_generic_form(parts):
    wt, registry, sandbox, transcript, tmp = parts
    truncated = _resp(tool_calls=[_bad_args("c", "edit_file", '{"path": "x", "old_string": "a')],
                      finish_reason="length",
                      usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([truncated, _resp(content="done")])
    Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == _GENERIC_TRUNCATION.format(tool="edit_file")


def test_length_truncation_with_no_raw_arguments_gives_the_generic_form(parts):
    # The Anthropic shape: its error branches never set raw_arguments, so path
    # recovery has nothing to scan and degrades to the generic sentence.
    wt, registry, sandbox, transcript, tmp = parts
    truncated = _resp(tool_calls=[_bad_args("c", "write_file", "")],
                      finish_reason="length",
                      usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([truncated, _resp(content="done")])
    Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == _GENERIC_TRUNCATION.format(tool="write_file")


def test_length_truncation_with_an_invalid_escape_degrades_to_generic(parts):
    # A raw fragment whose escape sequence is not valid JSON must not raise
    # inside the turn loop.
    wt, registry, sandbox, transcript, tmp = parts
    truncated = _resp(tool_calls=[_bad_args("c", "write_file", '{"path": "a\\qb", "content": "z')],
                      finish_reason="length",
                      usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([truncated, _resp(content="done")])
    Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == _GENERIC_TRUNCATION.format(tool="write_file")


def test_recovered_path_is_truncated_and_rendered_with_repr(parts):
    wt, registry, sandbox, transcript, tmp = parts
    long_path = "z" * 300
    raw = '{"path": "' + long_path + '", "content": "abc'
    truncated = _resp(tool_calls=[_bad_args("c", "write_file", raw)],
                      finish_reason="length",
                      usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([truncated, _resp(content="done")])
    Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert f"for {'z' * 200!r} was cut off" in tool_msgs[0]["content"]
    assert "z" * 201 not in tool_msgs[0]["content"]


def test_length_truncation_with_empty_args_counts_as_malformed_args_not_bad_args(parts):
    # Spec §1.3 case (b): a truncated Anthropic tool_use whose `input` came
    # back {} PARSES, so tc.error is None -- but a required parameter is
    # missing. It must be caught before dispatch and accounted as
    # malformed_args, so three of them abort on THAT kind rather than bad_args.
    wt, registry, sandbox, transcript, tmp = parts
    empty = _resp(tool_calls=[_call("c", "write_file", {})], finish_reason="length",
                  usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([empty, empty, empty])
    result = Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    assert result.final_message == "aborted after 3 consecutive malformed_args failures"
    results = [e["result"] for e in _events(tmp) if e["event"] == "tool_result"]
    assert results[0] == _GENERIC_TRUNCATION.format(tool="write_file")
    assert "bad arguments" not in results[0]


def test_length_truncated_but_parseable_call_still_dispatches(parts):
    # Controller addition (Task 8 review): a fully parseable tool call with
    # ALL required params present must still dispatch normally under
    # finish_reason == "length" -- truncation recovery is for calls that
    # failed to parse (case a) or are missing a required param (case b), not
    # every "length" turn. Task 8's reviewer verified this manually; this
    # pins it.
    wt, registry, sandbox, transcript, tmp = parts
    call = _resp(tool_calls=[_call("c", "write_file", {"path": "x.txt", "content": "hello\n"})],
                 finish_reason="length",
                 usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([call, _resp(content="done")])
    result = Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert (wt / "x.txt").read_text() == "hello\n"
    results = [e["result"] for e in _events(tmp) if e["event"] == "tool_result"]
    assert "cut off at the token limit" not in results[0]


def test_an_append_only_turn_counts_as_progress_and_does_not_stall(parts):
    # Spec §6: _MUTATING_TOOLS is what ProgressTracker reads, and append_file
    # is in it -- a run whose only work is appending must not be called stalled.
    wt, registry, sandbox, transcript, tmp = parts
    (wt / "notes.md").write_text("one\n")
    calls = [_resp(tool_calls=[_call(f"c{i}", "append_file",
                                     {"path": "notes.md", "text": f"line {i}\n"})])
             for i in range(3)]
    provider = FakeProvider(calls + [_resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m", stall_turns=2)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert (wt / "notes.md").read_text() == "one\nline 0\nline 1\nline 2\n"


def test_truncated_nudge_names_write_file_and_append_file(parts):
    assert NUDGES["truncated"] == (
        "Your reply was cut off at the token limit. Continue with smaller steps — "
        "emit one tool call at a time; for a large file, write_file the first part "
        "and append_file the rest.")


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
                            "last_assistant_text": "done", "verify": None,
                            "trimmed_turns": 0, "timeouts": 0,
                            "context_window_source": None,
                            "diff_stat": " 1 file changed"}
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
    # Over MAX_ASSISTANT_TEXT_CHARS (64_000) but under the default model's
    # char_budget, which since 0.10 is (32768 - 8192) * 0.75 * 4 = 73_728 for
    # the fallback DEFAULT_WINDOW and the default --max-tokens. The whole
    # history here is ~70_050 chars, so trim_messages doesn't ALSO trigger
    # context_exhausted — this test is about the transcript-only cap, not the
    # trim path.
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
    assert second[-2] == {"role": "assistant", "content": EMPTY_REPLY_PLACEHOLDER}
    events = _events(tmp)
    nudges = [e for e in events if e["event"] == "nudge"]
    assert len(nudges) == 1
    assert nudges[0]["kind"] == "empty" and nudges[0]["turn"] == 1
    assistant = next(e for e in events if e["event"] == "assistant")
    assert assistant["text"] == "" and assistant["placeholder"] == EMPTY_REPLY_PLACEHOLDER


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
    # The stub transport keeps this a pure unit test: with 0.9's server probe
    # in front of the table, a real client would otherwise try to GET
    # http://fake/api/v0/models before falling back. LLMError is already
    # imported at module scope (tests/test_runner.py:9).
    from dirtywork.providers.openai_compat import OpenAICompatClient

    def no_server(url, payload, headers, timeout, *, method="POST"):
        raise LLMError(f"cannot reach {url}")

    provider = OpenAICompatClient(base_url="http://fake/v1", http_json=no_server)
    assert resolve_context_window("qwen/qwen3-coder-next", None, None, provider) == \
        (CONTEXT_WINDOWS["qwen/qwen3-coder-next"], "provider:openai")


@pytest.mark.parametrize("env", ["abc", "0", "-5", "1.5"])
def test_resolve_context_window_rejects_bad_env(env):
    with pytest.raises(ValueError):
        resolve_context_window("m", None, env, FakeProvider([]))


def test_runner_context_window_param_sets_budget_and_run_start(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="unknown/model",
               context_window=1000, max_tokens=200)
    assert r.context_window == 1000
    # Spec §1.4: the prompt budget is what is left AFTER the output cap.
    assert r.char_budget == int((1000 - 200) * 0.75 * 4)
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


def test_last_tool_result_reflects_a_malformed_entry(parts):
    wt, registry, sandbox, transcript, tmp = parts
    bad = _resp(tool_calls=[_bad_entry()])
    provider = FakeProvider([bad, bad, bad])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    last_transcript_result = [e for e in _events(tmp) if e["event"] == "tool_result"][-1]
    assert result.extra["last_tool_result"] == {
        "tool": last_transcript_result["tool"],
        "args": last_transcript_result["args"],
        "result": last_transcript_result["result"],
    }
    assert result.extra["last_tool_result"]["tool"] == ""
    assert result.extra["last_tool_result"]["args"] == ""
    assert result.extra["last_tool_result"]["result"].startswith("ERROR: ")


def test_verify_timeout_is_clamped_to_the_bash_tools_range(parts):
    wt, registry, sandbox, transcript, tmp = parts
    over = Runner(FakeProvider([]), registry, sandbox, transcript, model="m",
                  verify_timeout=99999)
    assert over.verify_timeout == 600
    under = Runner(FakeProvider([]), registry, sandbox, transcript, model="m",
                   verify_timeout=0)
    assert under.verify_timeout == 1


def test_verify_passes_and_the_run_completes(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "done"})])])
    r = Runner(provider, registry, sandbox, transcript, model="m", verify="true")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    verify = result.extra["verify"]
    assert verify["command"] == "true"
    assert verify["exit_code"] == 0
    assert verify["passed"] is True
    assert verify["rounds"] == 1
    # tools.bash returns "exit code: 0\n" for a command with no output
    assert verify["output_tail"].startswith("exit code: 0")
    event = next(e for e in _events(tmp) if e["event"] == "verify")
    assert event == {"ts": event["ts"], "event": "verify", "round": 1,
                     "exit_code": 0, "passed": True}


def test_verify_failure_with_no_round_left_is_verify_failed(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "done"})])])
    r = Runner(provider, registry, sandbox, transcript, model="m",
               verify="echo boom; exit 3", verify_rounds=0)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "verify_failed"
    assert result.final_message == "done"          # the worker's own summary is kept
    assert result.extra["verify"]["passed"] is False
    assert result.extra["verify"]["exit_code"] == 3
    assert "boom" in result.extra["verify"]["output_tail"]
    assert result.extra["verify"]["rounds"] == 1


def test_verify_failure_with_a_round_left_feeds_back_and_retries(parts, tmp_path):
    wt, registry, sandbox, transcript, tmp = parts
    marker = wt / "fixed"
    provider = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "first try"})]),
        _resp(tool_calls=[_call("w1", "write_file", {"path": "fixed", "content": "y"})]),
        _resp(tool_calls=[_call("f2", "finish", {"summary": "second try"})]),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m",
               verify="test -e fixed", verify_rounds=1)
    result = r.run("s", "t")
    transcript.close()
    assert marker.is_file()
    assert result.status == "completed"
    assert result.final_message == "second try"
    assert result.extra["verify"]["rounds"] == 2 and result.extra["verify"]["passed"] is True
    # the failed round was fed back as a user message naming the command
    feedback = [m for m in provider.requests[-1] if m["role"] == "user"]
    assert any("VERIFY FAILED (round 1 of 2)" in m["content"] for m in feedback)
    assert any("test -e fixed" in m["content"] for m in feedback)
    verify_events = [e for e in _events(tmp) if e["event"] == "verify"]
    assert [e["passed"] for e in verify_events] == [False, True]


def test_a_verify_feedback_round_clears_a_stuck_latch_from_the_same_turn(parts, tmp_path):
    # Regression for the `stuck` latch outliving the turn it was set in: a
    # worker whose bash retries went stuck IN THE SAME TURN it also called
    # finish must get the verify feedback round it earned, not have the NEXT
    # turn's unrelated work summarily ended as "stuck" on stale state.
    wt, registry, sandbox, transcript, tmp = parts
    marker = wt / "fixed"
    provider = FakeProvider([
        _resp(tool_calls=[
            _call("b1", "bash", {"command": "false"}),
            _call("b2", "bash", {"command": "false"}),
            _call("b3", "bash", {"command": "false"}),
            _call("b4", "bash", {"command": "false"}),      # latches `stuck` mid-turn
            _call("f1", "finish", {"summary": "first try"}),
        ]),
        _resp(tool_calls=[_call("w1", "write_file", {"path": "fixed", "content": "y"})]),
        _resp(tool_calls=[_call("f2", "finish", {"summary": "second try"})]),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m",
               verify="test -e fixed", verify_rounds=1, stuck_repeats=4)
    result = r.run("s", "t")
    transcript.close()
    assert marker.is_file()
    assert result.status == "completed"
    assert result.final_message == "second try"
    assert result.extra["stuck_on"] is None
    assert result.extra["verify"]["rounds"] == 2
    assert result.extra["verify"]["passed"] is True


def test_verify_on_a_plain_answer_completion_and_error_passthrough(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="I am done")])
    r = Runner(provider, registry, sandbox, transcript, model="m", verify="exit 1",
              verify_rounds=0)
    result = r.run("s", "t")
    assert result.status == "verify_failed"
    assert result.final_message == "I am done"

    class ExplodingSandbox:
        def bash(self, command, timeout=120):
            from dirtywork.budget import BudgetExceeded
            raise BudgetExceeded("worktree over budget")

    transcript2 = Transcript(tmp / "t2.jsonl")
    provider2 = FakeProvider([_resp(content="I am done")])
    r2 = Runner(provider2, default_registry(transcript=transcript2), ExplodingSandbox(),
                transcript2, model="m", verify="true")
    result2 = r2.run("s", "t")
    transcript2.close()
    assert result2.status == "budget_exceeded"
    assert result2.extra["verify"] is None


class _ServerProvider(FakeProvider):
    """A provider that also implements the optional loaded_context_window hook
    (spec §3.1). `loaded` may be an int, None, or an Exception to raise."""

    def __init__(self, loaded, context_window=65536):
        super().__init__([], context_window=context_window)
        self._loaded = loaded

    def loaded_context_window(self, model):
        if isinstance(self._loaded, Exception):
            raise self._loaded
        return self._loaded


def test_resolve_context_window_prefers_what_the_server_loaded():
    provider = _ServerProvider(131072)
    assert resolve_context_window("qwen/qwen3-coder-next", None, None, provider) == \
        (131072, "provider:fake:server")


@pytest.mark.parametrize("loaded", [None, 0, -1, True, "65536", RuntimeError("boom")])
def test_resolve_context_window_falls_back_to_the_table_when_the_probe_says_nothing(loaded):
    provider = _ServerProvider(loaded)
    assert resolve_context_window("qwen/qwen3-coder-next", None, None, provider) == \
        (65536, "provider:fake")


def test_resolve_context_window_without_the_hook_uses_the_table():
    # Every existing double and every third-party provider is this case.
    provider = FakeProvider([], context_window=65536)
    assert not hasattr(provider, "loaded_context_window")
    assert resolve_context_window("qwen/qwen3-coder-next", None, None, provider) == \
        (65536, "provider:fake")


def test_flag_and_env_still_beat_the_server_report():
    provider = _ServerProvider(131072)
    assert resolve_context_window("m", 8000, None, provider) == (8000, "flag")
    assert resolve_context_window("m", None, "9000", provider) == (9000, "env")


class _TimeoutSandbox:
    """A sandbox whose bash always times out (or never does), with the canonical
    text the real backends produce. Only `bash` is needed: the registry calls
    exactly the method the tool dispatches to."""

    def __init__(self, timing_out=True):
        self.timing_out = timing_out
        self.commands = []

    def bash(self, command, timeout=120):
        self.commands.append(command)
        if self.timing_out:
            from dirtywork.tools import timeout_result
            return timeout_result(timeout)
        return "exit code: 0\nfine"


def _bash_call(call_id, command="sleep 999"):
    return _call(call_id, "bash", {"command": command})


def test_timed_out_is_flagged_on_the_event_and_absent_otherwise(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_bash_call("b1")]),
        _resp(content="done"),
    ])
    r = Runner(provider, registry, _TimeoutSandbox(), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    events = [e for e in _events(tmp) if e["event"] == "tool_result"]
    assert events[0]["timed_out"] is True
    assert result.extra["timeouts"] == 1

    # and a normal bash result carries no such key at all (sparse, additive)
    transcript2 = Transcript(tmp / "t2.jsonl")
    registry2 = default_registry(transcript=transcript2)
    provider2 = FakeProvider([_resp(tool_calls=[_bash_call("b2")]), _resp(content="ok")])
    r2 = Runner(provider2, registry2, _TimeoutSandbox(timing_out=False), transcript2,
                model="m")
    result2 = r2.run("s", "t")
    transcript2.close()
    events2 = [json.loads(l) for l in (tmp / "t2.jsonl").read_text().splitlines()]
    tool_events = [e for e in events2 if e["event"] == "tool_result"]
    assert "timed_out" not in tool_events[0]
    assert result2.extra["timeouts"] == 0


class _GrepTimeoutSandbox:
    """grep's OWN (unrelated) timeout wording -- spec §4.2: a grep timeout is
    the harness searching on the worker's behalf, not a worker-run command, so
    it must not flag `timed_out` on the tool_result event or count toward the
    run's `timeouts`."""

    def grep(self, pattern, path=".", glob=None, timeout=30):
        from dirtywork.tools import grep_timeout_result
        return grep_timeout_result(timeout)


def test_grep_timeout_is_not_flagged_or_counted(parts):
    # M10: is_timeout_result (and therefore `timed_out`/`timeouts`) keys on
    # TIMEOUT_PREFIX ("ERROR: command timed out after ..."), which grep's own
    # wording never starts with -- a grep timeout must read like any other
    # tool result.
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("g1", "grep", {"pattern": "x"})]),
        _resp(content="done"),
    ])
    r = Runner(provider, registry, _GrepTimeoutSandbox(), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    events = [e for e in _events(tmp) if e["event"] == "tool_result"]
    assert "ERROR: grep timed out after 30s" in events[0]["result"]
    assert "timed_out" not in events[0]
    assert result.extra["timeouts"] == 0


def test_one_timeout_nudge_per_turn_even_with_two_timeouts(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_bash_call("b1", "sleep 1"), _bash_call("b2", "sleep 2")]),
        _resp(content="done"),
    ])
    r = Runner(provider, registry, _TimeoutSandbox(), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    nudges = [e for e in _events(tmp) if e["event"] == "nudge"]
    assert [n["kind"] for n in nudges] == ["timeout"]
    assert nudges[0]["turn"] == 1
    assert result.extra["timeouts"] == 2        # the COUNT is per call, not per turn
    # the nudge text reached the model as the next user message
    second_request = provider.requests[1]
    assert second_request[-1]["role"] == "user"
    assert second_request[-1]["content"] == (
        "A command timed out and did not finish; its result is unknown. Re-run it "
        "with a larger timeout (up to 600 seconds) or split it into smaller "
        "commands. Do not report it as passed.")


def test_timeout_nudge_merges_with_the_stall_nudge(parts):
    wt, registry, sandbox, transcript, tmp = parts
    # stall_turns=2 nudges at turn 1 (2 // 2); the same turn also timed out.
    provider = FakeProvider([_resp(tool_calls=[_bash_call("b1")]),
                             _resp(content="done")])
    r = Runner(provider, registry, _TimeoutSandbox(), transcript, model="m",
               stall_turns=2)
    r.run("s", "t")
    transcript.close()
    kinds = [e["kind"] for e in _events(tmp) if e["event"] == "nudge"]
    # Both events are written; their ORDER follows the code path (check_progress
    # runs first, because it may end the run), while the merged MESSAGE leads
    # with the timeout, which is the more actionable of the two.
    assert sorted(kinds) == ["stall", "timeout"]
    text = provider.requests[1][-1]["content"]
    assert text.startswith("A command timed out and did not finish;")
    assert "No progress in the last 1 turns" in text
    assert "\n\n" in text                      # merged through _join_nudges


def test_no_timeout_nudge_when_the_turn_ends_the_run(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_bash_call("b1"),
                          _call("f1", "finish", {"summary": "done anyway"})]),
    ])
    r = Runner(provider, registry, _TimeoutSandbox(), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    assert [e for e in _events(tmp) if e["event"] == "nudge"] == []
    assert result.extra["timeouts"] == 1       # the COUNT is unaffected by finishing


class _TimeoutThenFailingVerifySandbox:
    """Worker bash calls time out; the --verify command runs "for real" and
    fails with a plain nonzero exit -- distinguished by command so the
    verify-failure text stays clean instead of itself reading as a timeout."""

    def __init__(self, verify_command):
        self.verify_command = verify_command

    def bash(self, command, timeout=120):
        if command == self.verify_command:
            return "exit code: 1\nboom"
        from dirtywork.tools import timeout_result
        return timeout_result(timeout)


def test_verify_feedback_carries_the_timeout_nudge_from_the_same_turn(parts):
    # M4 regression: the verify-feedback `continue` path used to return to the
    # loop top before the timeout-nudge composition ran, so a turn that timed
    # out a worker bash command AND called finish into a FAILING --verify
    # continued without ever telling the model about the timeout. Spec §4.3:
    # the nudge is emitted on turns that continue -- and this turn continues
    # (verify_rounds=1 leaves a round).
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_bash_call("b1"), _call("f1", "finish", {"summary": "done"})]),
        _resp(content="ok now"),
    ])
    box = _TimeoutThenFailingVerifySandbox("npm test")
    r = Runner(provider, registry, box, transcript, model="m",
               verify="npm test", verify_rounds=1)
    r.run("s", "t")
    transcript.close()

    # exactly one nudge{kind:timeout} event, for turn 1
    nudges = [e for e in _events(tmp) if e["event"] == "nudge"]
    assert [n["kind"] for n in nudges] == ["timeout"]
    assert nudges[0]["turn"] == 1

    # the next user message carries BOTH texts, merged into one message
    second_request = provider.requests[1]
    assert second_request[-1]["role"] == "user"
    content = second_request[-1]["content"]
    assert "VERIFY FAILED (round 1 of 2)" in content
    assert "A command timed out and did not finish" in content


def test_a_verify_timeout_is_not_counted(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="all done")])
    box = _TimeoutSandbox()
    r = Runner(provider, registry, box, transcript, model="m",
               verify="npm test", verify_rounds=0)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "verify_failed"
    assert box.commands == ["npm test"]        # it DID run, and it DID time out
    assert result.extra["timeouts"] == 0       # spec §4.3: worker tool calls only
    assert [e for e in _events(tmp) if e["event"] == "nudge"] == []


def test_mutating_tools_includes_every_tool_that_changes_a_file():
    # Spec §6: a run whose only progress is inserts/batches/appends must not be
    # called stalled. _MUTATING_TOOLS is what ProgressTracker reads.
    from dirtywork.runner import _MUTATING_TOOLS
    assert set(_MUTATING_TOOLS) == {"write_file", "append_file", "edit_file",
                                    "apply_edits", "insert_before", "insert_after"}


# --- spec §1.4/§1.5: the output cap and the recorded finish reason.


def test_max_tokens_defaults_to_8192_and_reaches_the_provider(parts):
    from dirtywork.runner import DEFAULT_MAX_TOKENS
    wt, registry, sandbox, transcript, tmp = parts

    seen = {}

    class _RecordingProvider(FakeProvider):
        def chat(self, model, history, tools, *, temperature=None, max_tokens=4096,
                 timeout=None):
            seen["max_tokens"] = max_tokens
            return super().chat(model, history, tools, temperature=temperature,
                                max_tokens=max_tokens, timeout=timeout)

    r = Runner(_RecordingProvider([_resp(content="done")]), registry, sandbox, transcript,
               model="m")
    r.run("s", "t")
    transcript.close()
    assert DEFAULT_MAX_TOKENS == 8192
    assert seen["max_tokens"] == 8192


def test_char_budget_subtracts_max_tokens_from_the_window(parts):
    # Spec §1.4: the window is SHARED. Budgeting the prompt as if the whole
    # window were available is what made a long reply run off the end.
    wt, registry, sandbox, transcript, tmp = parts
    from dirtywork.runner import BUDGET_FRACTION, CHARS_PER_TOKEN
    r = Runner(FakeProvider([_resp(content="done")]), registry, sandbox, transcript,
               model="m", context_window=32768, max_tokens=8192)
    assert r.char_budget == int((32768 - 8192) * BUDGET_FRACTION * CHARS_PER_TOKEN)
    # A cap larger than the window cannot go negative here (preflight refuses
    # that combination; a directly-built Runner must still not explode).
    r2 = Runner(FakeProvider([_resp(content="done")]), registry, sandbox, transcript,
                model="m", context_window=1000, max_tokens=8192)
    assert r2.char_budget == 0


def test_run_start_records_max_tokens(parts):
    wt, registry, sandbox, transcript, tmp = parts
    r = Runner(FakeProvider([_resp(content="done")]), registry, sandbox, transcript,
               model="m", max_tokens=1234)
    r.run("s", "t")
    transcript.close()
    start = next(e for e in _events(tmp) if e["event"] == "run_start")
    assert start["max_tokens"] == 1234


def test_assistant_event_records_finish_reason(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "list_dir", {"path": "."})],
              finish_reason="tool_calls"),
        _resp(content="done", finish_reason="stop"),
    ])
    Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    reasons = [e["finish_reason"] for e in _events(tmp) if e["event"] == "assistant"]
    assert reasons == ["tool_calls", "stop"]


def test_assistant_event_finish_reason_is_null_for_a_non_string(parts):
    # Adapters do not guarantee a string -- the Anthropic adapter passes an
    # unknown stop reason through raw -- so anything non-str is recorded as
    # null rather than emitted as some other JSON type (spec §1.5).
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="done", finish_reason=17)])
    Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    assistant = next(e for e in _events(tmp) if e["event"] == "assistant")
    assert assistant["finish_reason"] is None
