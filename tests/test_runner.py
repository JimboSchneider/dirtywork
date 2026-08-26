from __future__ import annotations

import json
import shutil
import subprocess
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
    FINISH_DONE,
    FINISH_PROVISIONAL,
    MAX_TOTAL_CONSECUTIVE_FAILURES,
    NUDGES,
    ProgressTracker,
    RunResult,
    Runner,
    STALL_NUDGE,
    TIMEOUT_NUDGE,
    TRIM_MARKER,
    TRUNCATION_ABORT,
    VERIFY_FEEDBACK,
    _bash_fingerprint,
    classify_text_reply,
    resolve_context_window,
    strip_think,
    trim_messages,
    truncated_call_result,
)
from dirtywork.sandbox.host import HostSandbox
from dirtywork.builtin_tools import default_registry
from dirtywork.budget import BudgetExceeded
from dirtywork.sandbox import SandboxError
from dirtywork.changes import FINGERPRINT_SCRIPT, UNCHANGED_PLAIN, UNCHANGED_REQUIRED
from dirtywork.transcript import Transcript

from .provider_doubles import FingerprintSandbox
from .provider_doubles import assert_strict_template_legal
from .provider_doubles import TimeoutThenFailingVerifySandbox as _TimeoutThenFailingVerifySandbox


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


def _trunc_dict(tc=None, text=""):
    """The dict the runner builds for a truncation on a turn with default
    max_tokens (8192): the cut call's own size when there is one."""
    from dirtywork.runner import call_size, chunk_target
    cut_chars, cut_lines = call_size(tc) if tc is not None else (0, 0)
    target_chars, target_lines = chunk_target(8192, cut_chars, cut_lines)
    return dict(cap=8192, cap_chars=32768, received=len(text) + cut_chars,
                cut_chars=cut_chars, cut_lines=cut_lines,
                target_chars=target_chars, target_lines=target_lines, n=1, max=6)


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
        # Spec #60 §7: every request the runner makes is legal for strict templates.
        assert_strict_template_legal(history)
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


@pytest.fixture()
def git_parts(tmp_path: Path):
    if shutil.which("git") is None:   # a mark on a fixture is a no-op (PytestRemovedIn9Warning)
        pytest.skip("git not on PATH")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "f.txt").write_text("data\n")
    subprocess.run(["git", "-C", str(wt), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(wt), "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(wt), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], check=True)
    transcript = Transcript(tmp_path / "t.jsonl")
    registry = default_registry(transcript=transcript)
    sandbox = FingerprintSandbox(wt, hashes=None)
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
    assert third[-1]["role"] == "user" and third[-1]["content"] == NUDGES["truncated"].format(**_trunc_dict(text=""))
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
    assert [(e["kind"], e["via"]) for e in events if e["event"] == "nudge"] == [("malformed_entry", "user")]


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
    assert "cut off at the --max-tokens cap" not in second[-1]["content"]


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
    tc = _bad_args("c", "write_file", '{"path": "x", "content": "abc')
    assert tool_msgs[0]["content"] == truncated_call_result("write_file", tc.raw_arguments, _trunc_dict(tc))


def _generic_truncation(tool, tc):
    return truncated_call_result(tool, tc.raw_arguments, _trunc_dict(tc))


def test_length_truncation_of_a_non_write_file_tool_gives_the_generic_form(parts):
    wt, registry, sandbox, transcript, tmp = parts
    tc = _bad_args("c", "edit_file", '{"path": "x", "old_string": "a')
    truncated = _resp(tool_calls=[tc], finish_reason="length",
                      usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([truncated, _resp(content="done")])
    Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == _generic_truncation("edit_file", tc)


def test_length_truncation_with_no_raw_arguments_gives_the_generic_form(parts):
    # The Anthropic shape: its error branches never set raw_arguments, so path
    # recovery has nothing to scan and degrades to the generic sentence.
    wt, registry, sandbox, transcript, tmp = parts
    tc = _bad_args("c", "write_file", "")
    truncated = _resp(tool_calls=[tc], finish_reason="length",
                      usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([truncated, _resp(content="done")])
    Runner(provider, registry, sandbox, transcript, model="m").run("s", "t")
    transcript.close()
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert tool_msgs[0]["content"] == _generic_truncation("write_file", tc)


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
    tc = _bad_args("c", "write_file", '{"path": "a\\qb", "content": "z')
    assert tool_msgs[0]["content"] == _generic_truncation("write_file", tc)


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
    tc = _call("c", "write_file", {})
    assert results[0] == _generic_truncation("write_file", tc)
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
    t = NUDGES["truncated"]
    assert all(f in t for f in ("{cap}", "{cap_chars}", "{received}", "{target_chars}", "{target_lines}",
                                "cut-off reply {n} of {max}",
                                "write_file the first part and append_file the rest"))


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
    assert result.extra.pop("changed_reason").startswith("fatal: not a git repository")
    assert result.extra == {"stuck_on": None, "last_tool_result": None,
                            "last_assistant_text": "done", "verify": None,
                            "trimmed_turns": 0, "timeouts": 0, "truncations": 0,
                            "context_window_source": None,
                            "changed": None,
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
    assert provider.requests[1][-1]["content"] == NUDGES["truncated"].format(**_trunc_dict(text="I will now"))


def test_three_empty_replies_abort_as_model_error(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content=""), _resp(content=""), _resp(content="")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    assert result.final_message == "aborted after 3 consecutive empty_reply failures"


def test_nudge_via_is_absent_on_the_turn_that_aborts_as_model_error(parts):
    # Spec #60 §6.2 (review fix): the nudge event on the 3rd empty reply is
    # written BEFORE the abort check returns, so the run ends before deliver()
    # ever runs -- `via` is sparse, not defaulted, on that one event. Earlier
    # turns in the same run DID reach deliver() and carry `via`.
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content=""), _resp(content=""), _resp(content="")])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "model_error"
    nudges = [e for e in _events(tmp) if e["event"] == "nudge"]
    assert len(nudges) == 3
    assert nudges[0]["via"] == "user" and nudges[1]["via"] == "user"
    assert "via" not in nudges[2]


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


def test_progress_tracker_ignores_noop_writes():
    """Spec #66 §4.2: a write that changed nothing (+0 -0) is not progress."""
    t = ProgressTracker(stall_turns=4)
    
    # +0 -0 write is not progress
    t.note_call("write_file", {"path": "a"}, "Wrote a: +0 -0")
    assert t.end_turn() is None
    assert t.idle_turns == 1
    
    # +1 -0 write is progress (resets idle)
    t.note_call("write_file", {"path": "a"}, "Wrote a: +1 -0")
    assert t.end_turn() is None
    assert t.idle_turns == 0
    
    # new file form (which should be progress)
    t.note_call("write_file", {"path": "b"}, "Wrote 1 bytes to b (new file, 1 line)")
    assert t.end_turn() is None
    assert t.idle_turns == 0
    
    # unknown result shape (fail open - should be progress)
    t.note_call("write_file", {"path": "c"}, "weird")
    assert t.end_turn() is None
    assert t.idle_turns == 0


def test_identical_rewrites_stall(parts):
    """Spec #66 §4.2: a runner with a provider that keeps rewriting the same file
    with identical content should end status 'stalled'."""
    wt, registry, sandbox, transcript, tmp = parts
    
    # Create an initial file
    (wt / "f.txt").write_text("data\n")
    
    # Provider that keeps rewriting f.txt with the same content
    loop = _resp(tool_calls=[_call("c", "write_file", {"path": "f.txt", "content": "data\n"})])
    provider = FakeProvider([loop] * 10)
    
    r = Runner(provider, registry, sandbox, transcript, model="m", stall_turns=4)
    result = r.run("s", "t")
    transcript.close()
    
    # Should stall because writes are +0 -0 (no progress)
    assert result.status == "stalled"
    # Turn 1: first write (+0 -0, not progress, idle=1)
    # Turns 2-3: repeats (idle increases)
    # Turn 4: idle reaches 4, returns "stalled"
    assert result.turns == 4
    assert result.final_message == "no progress in 4 consecutive turns"


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
    # Spec #60 §3: on a tool turn the nudge rides on the turn's last tool result
    fourth = provider.requests[3]
    assert fourth[-1]["role"] == "tool"
    assert fourth[-1]["content"].endswith("\n\n" + STALL_NUDGE.format(n=2))
    events = _events(tmp)
    nudges = [e for e in events if e["event"] == "nudge"]
    assert len(nudges) == 1 and nudges[0]["kind"] == "stall" and nudges[0]["turn"] == 3
    assert nudges[0]["via"] == "tool_result"
    carrier = [e for e in events if e["event"] == "tool_result"][2]     # turn 3's read_file
    assert carrier["follow_up"] == STALL_NUDGE.format(n=2)
    assert fourth[-1]["content"] == carrier["result"] + "\n\n" + carrier["follow_up"]


def test_runner_empty_replies_count_as_idle_turns(parts):
    wt, registry, sandbox, transcript, tmp = parts
    # two empty replies with stall_turns=2: idle 1 (nudge), idle 2 → stalled (before the 3-strike abort)
    provider = FakeProvider([_resp(content=""), _resp(content=""), _resp(content="")])
    r = Runner(provider, registry, sandbox, transcript, model="m", stall_turns=2)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "stalled" and result.turns == 2


def test_nudge_via_is_absent_on_the_turn_that_ends_stalled(parts):
    # Spec #60 §6.2 (review fix): turn 2's "empty" nudge is written before
    # check_progress() returns "stalled", which ends the run before deliver()
    # ever runs -- `via` is sparse on that one event. Turn 1's two nudges
    # ("empty" then "stall") both reached deliver() and carry `via`.
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content=""), _resp(content=""), _resp(content="")])
    r = Runner(provider, registry, sandbox, transcript, model="m", stall_turns=2)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "stalled"
    nudges = [e for e in _events(tmp) if e["event"] == "nudge"]
    assert len(nudges) == 3
    assert [n["kind"] for n in nudges] == ["empty", "stall", "empty"]
    assert nudges[0]["turn"] == 1 and nudges[1]["turn"] == 1 and nudges[2]["turn"] == 2
    assert nudges[0]["via"] == "user" and nudges[1]["via"] == "user"
    assert "via" not in nudges[2]


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
    kinds = [(e["kind"], e["via"]) for e in _events(tmp) if e["event"] == "nudge"]
    assert kinds == [("stall", "user"), ("malformed_entry", "user")]


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
    # Spec #60 §4: the failed round IS the finish call's result -- no user message
    last = provider.requests[-1]
    f1 = next(m for m in last if m["role"] == "tool" and m["tool_call_id"] == "f1")
    assert f1["content"] == VERIFY_FEEDBACK.format(
        round=1, rounds=2, command="test -e fixed", exit_code=1,
        output=f1["content"].split("Output tail:\n", 1)[1].rsplit("\nFix the problem", 1)[0])
    assert not any(m["role"] == "user" and "VERIFY FAILED" in m["content"] for m in last)
    # the last request was captured BEFORE f2 (called in that very reply) existed at all
    assert not any(m["role"] == "tool" and m["tool_call_id"] == "f2" for m in last)
    events = _events(tmp)
    finish_events = [e for e in events if e["event"] == "tool_result" and e["tool"] == "finish"]
    assert finish_events[0]["result"] == f1["content"]
    assert finish_events[1]["result"] == FINISH_DONE
    verify_events = [e for e in events if e["event"] == "verify"]
    assert verify_events[0]["via"] == "finish_result" and "via" not in verify_events[1]
    # the transcript shows the resolved finish result BEFORE its verify event
    assert events.index(finish_events[0]) < events.index(verify_events[0])
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
        if command == FINGERPRINT_SCRIPT:
            return "exit code: 1\nerror: test double"
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
    # Spec #60 §3: the nudge rides on the turn's LAST tool result (b2), not b1
    second_request = provider.requests[1]
    assert second_request[-1]["role"] == "tool" and second_request[-1]["tool_call_id"] == "b2"
    # both calls got the default 120 s timeout, so both results are the same text;
    # only the LAST one carries the nudge
    assert second_request[-1]["content"] == second_request[-2]["content"] + "\n\n" + TIMEOUT_NUDGE
    events = [e for e in _events(tmp) if e["event"] == "tool_result"]
    assert "follow_up" not in events[0] and events[1]["follow_up"] == TIMEOUT_NUDGE
    assert nudges[0]["via"] == "tool_result"


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
    last = provider.requests[1][-1]
    assert last["role"] == "tool"
    carrier = [e for e in _events(tmp) if e["event"] == "tool_result"][-1]
    text = carrier["follow_up"]
    assert last["content"] == carrier["result"] + "\n\n" + text
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

    # Spec #60 §4: the feedback is the finish result; the timeout nudge is (Task 5)
    # the follow_up on the turn's last tool result, which here is finish itself.
    second_request = provider.requests[1]
    f1 = next(m for m in second_request if m["role"] == "tool" and m["tool_call_id"] == "f1")
    assert f1["content"].startswith("VERIFY FAILED (round 1 of 2)")
    assert second_request[-1]["role"] == "tool"                     # no user message follows
    assert f1["content"].endswith("\n\n" + TIMEOUT_NUDGE)            # finish is the turn's last call
    events = _events(tmp)
    finish_event = next(e for e in events if e["event"] == "tool_result" and e["tool"] == "finish")
    assert finish_event["result"].startswith("VERIFY FAILED (round 1 of 2)")
    assert finish_event["follow_up"] == TIMEOUT_NUDGE
    assert nudges[0]["via"] == "tool_result"


def test_a_verify_timeout_is_not_counted(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(content="all done")])
    box = _TimeoutSandbox()
    r = Runner(provider, registry, box, transcript, model="m",
               verify="npm test", verify_rounds=0)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "verify_failed"
    assert [c for c in box.commands if c != FINGERPRINT_SCRIPT] == ["npm test"]        # it DID run, and it DID time out
    assert result.extra["timeouts"] == 0       # spec §4.3: worker tool calls only
    assert [e for e in _events(tmp) if e["event"] == "nudge"] == []


def _finish_results(events):
    return [e["result"] for e in events if e["event"] == "tool_result" and e["tool"] == "finish"]


def test_finish_result_is_the_full_verify_feedback_even_past_the_preview_cap(parts):
    # Spec #60 §4 "Transcript cap": FINISH_SPEC is transcript="full", so a
    # 3000-char verify tail is recorded byte-for-byte.
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "first"})]),
        _resp(content="ok"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m",
               verify="python3 -c \"print('x' * 3000)\"; exit 1", verify_rounds=1)
    r.run("s", "t")
    transcript.close()
    f1 = next(m for m in provider.requests[1] if m["role"] == "tool")
    assert len(f1["content"]) > 3000
    assert _finish_results(_events(tmp)) == [f1["content"]]
    assert provider.requests[1][-1]["role"] == "tool"          # no user message follows


def test_verify_rounds_zero_leaves_an_honest_finish_result(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "done"})])])
    r = Runner(provider, registry, sandbox, transcript, model="m",
               verify="echo boom; exit 3", verify_rounds=0)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "verify_failed"
    events = _events(tmp)
    assert _finish_results(events) == ["run not finished: verify failed (exit 3); no fix rounds remain"]
    assert "via" not in next(e for e in events if e["event"] == "verify")


def test_last_round_failure_leaves_an_honest_finish_result(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "a"})]),
                             _resp(tool_calls=[_call("f2", "finish", {"summary": "b"})])])
    r = Runner(provider, registry, sandbox, transcript, model="m", verify="exit 2", verify_rounds=1)
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "verify_failed"
    results = _finish_results(_events(tmp))
    assert results[0].startswith("VERIFY FAILED (round 1 of 2)")
    assert results[1] == "run not finished: verify failed (exit 2); no fix rounds remain"


def test_verify_that_cannot_run_leaves_an_honest_finish_result(parts):
    from dirtywork.budget import BudgetExceeded
    from dirtywork.sandbox import SandboxError          # the same import runner.py uses

    class Raising:
        def __init__(self, exc):
            self.exc = exc

        def bash(self, command, timeout=120):
            if command == FINGERPRINT_SCRIPT:
                return "exit code: 1\nerror: test double"
            raise self.exc

    for exc, status, reason in ((BudgetExceeded("worktree exceeds 2048 MB"), "budget_exceeded",
                                 "worktree exceeds 2048 MB"),
                                (SandboxError("container gone"), "sandbox_error", "container gone")):
        wt, registry, sandbox, transcript, tmp = parts
        transcript_i = Transcript(tmp / f"t-{status}.jsonl")
        provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "done"})])])
        r = Runner(provider, registry, Raising(exc), transcript_i, model="m", verify="true")
        result = r.run("s", "t")
        transcript_i.close()
        assert result.status == status
        events = [json.loads(l) for l in (tmp / f"t-{status}.jsonl").read_text().splitlines()]
        assert _finish_results(events) == [f"run not finished: verify could not run ({reason})"]


def test_terminal_exits_before_verify_never_leave_run_finished(parts):
    # Spec #60 §4 row 5 + §9.9: a later call ends the run before check_verify;
    # finish() resolves the still-provisional record from the status.
    from dirtywork.budget import BudgetExceeded

    class BudgetBustingSandbox:
        def write_file(self, path, content):
            raise BudgetExceeded("worktree exceeds 2048 MB")

    class InterruptingSandbox:
        def bash(self, command, timeout=120):
            if command == FINGERPRINT_SCRIPT:
                return "exit code: 1\nerror: test double"
            raise KeyboardInterrupt

    cases = [
        ([_call("f1", "finish", {"summary": "s"}), _call("w1", "write_file", {"path": "x", "content": "y"})],
         BudgetBustingSandbox(), "budget_exceeded"),
        ([_call("f1", "finish", {"summary": "s"}), _bash_call("b1")],
         InterruptingSandbox(), "interrupted"),
        ([_call("f1", "finish", {"summary": "s"})] + [_call(f"u{i}", "no_such_tool", {}) for i in range(3)],
         None, "model_error"),
    ]
    for calls, box, status in cases:
        wt, registry, sandbox, transcript, tmp = parts
        transcript_i = Transcript(tmp / f"t-{status}.jsonl")
        provider = FakeProvider([_resp(tool_calls=calls)])
        r = Runner(provider, registry, box or sandbox, transcript_i, model="m")
        result = r.run("s", "t")
        transcript_i.close()
        assert result.status == status
        events = [json.loads(l) for l in (tmp / f"t-{status}.jsonl").read_text().splitlines()]
        assert _finish_results(events) == [f"run not finished: {status}"]
        assert not any(e.get("result") == FINISH_DONE for e in events)
        assert events[-1]["event"] == "run_end" and events[-1]["status"] == status


def test_interrupt_inside_verify_resolves_the_finish_result_before_the_flush(parts):
    # Spec #60 §4 (v3): KeyboardInterrupt is caught INSIDE the turn block.
    wt, registry, sandbox, transcript, tmp = parts

    class InterruptingVerify:
        def bash(self, command, timeout=120):
            if command == FINGERPRINT_SCRIPT:
                return "exit code: 1\nerror: test double"
            raise KeyboardInterrupt

    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "s"})])])
    r = Runner(provider, registry, InterruptingVerify(), transcript, model="m", verify="npm test")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "interrupted"
    events = _events(tmp)
    assert _finish_results(events) == ["run not finished: interrupted"]
    assert events[-1]["event"] == "run_end"


def test_unhandled_exception_after_finish_leaves_the_provisional_result_on_disk(parts):
    # Spec #60 §4 last row: the ONLY way the provisional string is written --
    # an exception the runner does not handle leaves the turn; turn() flushes
    # the buffered records as they stand and no runner run_end is written
    # (the CLI's _fail_run supplies one).
    wt, registry, sandbox, transcript, tmp = parts

    class Exploding:
        def bash(self, command, timeout=120):
            if command == FINGERPRINT_SCRIPT:
                return "exit code: 1\nerror: test double"
            raise RuntimeError("disk on fire")

    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "s"}), _bash_call("b1")])])
    r = Runner(provider, registry, Exploding(), transcript, model="m")
    with pytest.raises(RuntimeError):
        r.run("s", "t")
    transcript.close()
    events = _events(tmp)
    assert _finish_results(events) == [FINISH_PROVISIONAL]
    assert [e["event"] for e in events if e["event"] == "run_end"] == []


def test_multiple_finish_calls_in_one_turn_resolve_to_the_same_string(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "a"}), _call("f2", "finish", {"summary": "b"})]),
        _resp(content="ok"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m", verify="exit 1", verify_rounds=1)
    result = r.run("s", "t")
    transcript.close()
    assert result.final_message == "ok"
    second = provider.requests[1]
    tools = [m for m in second if m["role"] == "tool"]
    assert len(tools) == 2 and tools[0]["content"] == tools[1]["content"]
    assert tools[0]["content"].startswith("VERIFY FAILED (round 1 of 2)")
    assert _finish_results(_events(tmp)) == [tools[0]["content"], tools[1]["content"]]
    # (Task 5 adds the [finish, bash(timeout), finish] variant: follow_up on f2 only)


def test_a_malformed_finish_is_not_terminal_and_is_never_rewritten(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(tool_calls=[_bad_args(call_id="f1", name="finish")]),
                             _resp(tool_calls=[_call("f2", "finish", {"summary": "ok"})])])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    results = _finish_results(_events(tmp))
    assert results[0].startswith("ERROR:") and results[1] == FINISH_DONE


def test_finish_flushes_the_turn_before_finalize(parts):
    # Spec #60 §6.1 (v2): the turn's evidence is on disk before the export runs.
    wt, registry, sandbox, transcript, tmp = parts
    seen = {}

    def finalize():
        seen["events"] = [e["event"] for e in _events(tmp)]
        return {}

    provider = FakeProvider([_resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"}),
                                               _call("f1", "finish", {"summary": "s"})])])
    r = Runner(provider, registry, sandbox, transcript, model="m", finalize=finalize)
    r.run("s", "t")
    transcript.close()
    assert seen["events"] == ["run_start", "assistant", "tool_result", "tool_result"]
    assert _events(tmp)[-1]["event"] == "run_end"


def test_interrupt_inside_finalize_does_not_run_the_export_twice(parts):
    # Owner-found P2 on PR #68: finalize() (the docker export) is not
    # idempotent. A Ctrl-C landing inside it propagates out of finish() into
    # the turn's interrupt handler, which calls finish("interrupted"); that
    # second call must not export again, must say what happened, and must
    # write exactly one run_end.
    wt, registry, sandbox, transcript, tmp = parts
    calls = []

    def finalize():
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return {"export_status": "ok"}

    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "s"})])])
    r = Runner(provider, registry, sandbox, transcript, model="m", finalize=finalize)
    result = r.run("s", "t")
    transcript.close()
    assert calls == [1]                                    # exported once
    assert result.status == "interrupted"
    assert result.extra["finalize_error"] == "KeyboardInterrupt: interrupted during finalize"
    assert "export_status" not in result.extra
    events = _events(tmp)
    assert [e["event"] for e in events].count("run_end") == 1
    assert events[-1]["status"] == "interrupted"
    assert events[-1]["finalize_error"] == "KeyboardInterrupt: interrupted during finalize"
    # the finish result stays `run finished`: the agent loop DID end completed
    # (contract: an interrupt after that point is reported in run_end.status)
    assert _finish_results(events) == [FINISH_DONE]


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


# ---- Spec #60 shared scenarios: (provider, sandbox, runner kwargs). Test 12 iterates them.

def _scenario_verify_feedback_on_finish():
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "first"})]),
                             _resp(content="ok")])
    return provider, None, {"verify": "exit 1", "verify_rounds": 1}


def _scenario_finish_first_then_timeout():
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "s"}), _bash_call("b1")]),
                             _resp(content="ok")])
    return provider, _TimeoutThenFailingVerifySandbox("npm test"), {"verify": "npm test", "verify_rounds": 1}


def _scenario_malformed_only_turn():
    provider = FakeProvider([_resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
                             _resp(tool_calls=[_bad_entry()]),
                             _resp(content="done")])
    return provider, None, {}


def _scenario_empty_reply_after_tool_turn():
    provider = FakeProvider([_resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
                             _resp(content="", finish_reason="length"),
                             _resp(content="done")])
    return provider, None, {}


SCENARIOS = [_scenario_verify_feedback_on_finish, _scenario_finish_first_then_timeout,
             _scenario_malformed_only_turn, _scenario_empty_reply_after_tool_turn]


def _run_scenario(parts, build):
    wt, registry, sandbox, transcript, tmp = parts
    provider, box, kwargs = build()
    r = Runner(provider, registry, box or sandbox, transcript, model="m", **kwargs)
    result = r.run("s", "t")
    transcript.close()
    return provider, result, _events(tmp)


def _tool_events(events):
    return [e for e in events if e["event"] == "tool_result"]


def test_mixed_turn_finish_first_then_timeout(parts):
    # Spec §3 example table row 3: feedback on finish, TIMEOUT_NUDGE on bash.
    provider, result, events = _run_scenario(parts, _scenario_finish_first_then_timeout)
    second = provider.requests[1]
    tools = [m for m in second if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["f1", "b1"]           # wire order == call order
    assert tools[0]["content"].startswith("VERIFY FAILED (round 1 of 2)")
    assert TIMEOUT_NUDGE not in tools[0]["content"]
    assert tools[1]["content"].endswith("\n\n" + TIMEOUT_NUDGE)
    f1, b1 = _tool_events(events)
    assert "follow_up" not in f1 and b1["follow_up"] == TIMEOUT_NUDGE
    # the verify command fails on the plain-answer round too and no round is left
    assert result.status == "verify_failed"


def test_two_finish_calls_around_a_timeout_put_the_follow_up_on_the_last_call_only(parts):
    # Spec §9.3: both terminal results resolve to the same string; the follow_up
    # attaches only to the turn's last addressable call, which is f2 here.
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "a"}), _bash_call("b1"),
                          _call("f2", "finish", {"summary": "b"})]),
        _resp(content="ok"),
    ])
    r = Runner(provider, registry, _TimeoutThenFailingVerifySandbox("npm test"), transcript,
               model="m", verify="npm test", verify_rounds=1)
    r.run("s", "t")
    transcript.close()
    f1, b1, f2 = _tool_events(_events(tmp))
    assert f1["result"] == f2["result"] and f1["result"].startswith("VERIFY FAILED")
    assert "follow_up" not in f1 and "follow_up" not in b1 and f2["follow_up"] == TIMEOUT_NUDGE
    tools = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert tools[2]["content"] == f2["result"] + "\n\n" + TIMEOUT_NUDGE


def test_mixed_turn_timeout_finish_timeout_carrier_is_the_last_call(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([
        _resp(tool_calls=[_bash_call("b1"), _call("f1", "finish", {"summary": "s"}), _bash_call("b2")]),
        _resp(content="ok"),
    ])
    r = Runner(provider, registry, _TimeoutThenFailingVerifySandbox("npm test"), transcript,
               model="m", verify="npm test", verify_rounds=1)
    r.run("s", "t")
    transcript.close()
    tools = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["b1", "f1", "b2"]
    assert tools[1]["content"].startswith("VERIFY FAILED") and TIMEOUT_NUDGE not in tools[1]["content"]
    assert tools[2]["content"].endswith("\n\n" + TIMEOUT_NUDGE) and TIMEOUT_NUDGE not in tools[0]["content"]
    b1, f1, b2 = _tool_events(_events(tmp))
    assert "follow_up" not in b1 and "follow_up" not in f1 and b2["follow_up"] == TIMEOUT_NUDGE
    assert [e["kind"] for e in _events(tmp) if e["event"] == "nudge"] == ["timeout"]


class _TimeoutThenPassingVerifySandbox(_TimeoutThenFailingVerifySandbox):
    """Worker bash calls time out; the --verify command passes."""

    def bash(self, command, timeout=120):
        if command == self.verify_command:
            return "exit code: 0\n"
        return super().bash(command, timeout)


def test_mixed_turn_finish_first_then_timeout_with_passing_verify_ends_clean(parts):
    wt, registry, sandbox, transcript, tmp = parts
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "s"}), _bash_call("b1")])])
    r = Runner(provider, registry, _TimeoutThenPassingVerifySandbox("npm test"), transcript,
               model="m", verify="npm test")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    events = _events(tmp)
    assert all("follow_up" not in e for e in _tool_events(events))
    assert _tool_events(events)[0]["result"] == FINISH_DONE
    assert [e for e in events if e["event"] == "nudge"] == []


def test_stall_and_malformed_nudges_share_one_follow_up_on_a_tool_turn(parts):
    # stall_turns=2 -> stall nudge at idle 1 (turn 2); that turn also carries a
    # malformed entry alongside an addressable read_file -> one follow_up
    # holding both texts, in the documented order (malformed, timeout, stall).
    wt, registry, sandbox, transcript, tmp = parts
    idle = _resp(tool_calls=[_call("c", "read_file", {"path": "f.txt"})])
    mixed = _resp(tool_calls=[_bad_entry(), _call("c2", "read_file", {"path": "f.txt"})])
    provider = FakeProvider([idle, mixed, _resp(content="done")])
    r = Runner(provider, registry, sandbox, transcript, model="m", stall_turns=2)
    r.run("s", "t")
    transcript.close()
    events = _events(tmp)
    carrier = [e for e in _tool_events(events) if e["tool"] == "read_file"][-1]
    assert carrier["follow_up"].startswith("1 of your tool calls were malformed")
    assert carrier["follow_up"].endswith(STALL_NUDGE.format(n=1))
    assert "\n\n" in carrier["follow_up"]
    nudges = [(e["kind"], e["via"]) for e in events if e["event"] == "nudge"]
    assert sorted(nudges) == [("malformed_entry", "tool_result"), ("stall", "tool_result")]
    third = provider.requests[2]
    assert third[-1]["role"] == "tool" and third[-1]["content"] == carrier["result"] + "\n\n" + carrier["follow_up"]


def test_last_tool_result_excludes_the_follow_up(parts):
    provider, result, events = _run_scenario(parts, _scenario_finish_first_then_timeout)
    last = result.extra["last_tool_result"]
    assert last["tool"] == "bash" and TIMEOUT_NUDGE not in last["result"]


def test_transcript_equals_wire_for_every_tool_and_assistant_message(parts):
    # Spec §9.10. Results kept under the preview cap except the finish result,
    # which is recorded in full (a 3000-char verify tail).
    wt, registry, sandbox, transcript, tmp = parts

    class Box(_TimeoutThenFailingVerifySandbox):
        def bash(self, command, timeout=120):
            if command == self.verify_command:
                return "exit code: 1\n" + "y" * 3000
            return super().bash(command, timeout)

        def read_file(self, path, offset=0, limit=400):   # turn 1 reads f.txt; keep it under the preview cap
            return "data\n"

    provider = FakeProvider([
        _resp(tool_calls=[_bash_call("b1"), _call("c1", "read_file", {"path": "f.txt"})]),
        _resp(content="", finish_reason="length"),
        _resp(tool_calls=[_call("f1", "finish", {"summary": "s"})]),
        _resp(content="ok"),
    ])
    r = Runner(provider, registry, Box("npm test"), transcript, model="m",
               verify="npm test", verify_rounds=1, stall_turns=0)
    r.run("s", "t")
    transcript.close()
    events = _events(tmp)
    tool_events = _tool_events(events)
    assistant_events = [e for e in events if e["event"] == "assistant"]
    final = provider.requests[-1]
    wire_tools = [m for m in final if m["role"] == "tool"]
    wire_assistants = [m for m in final if m["role"] == "assistant"]
    assert len(tool_events) == len(wire_tools) == 3
    # 4 turns run (verify fails on the finish call, feeds back, the plain "ok"
    # reply on turn 4 fails verify again and ends the run) -- turn 4's own
    # assistant reply is appended to history but never sent back to the model
    # in a further request, so the transcript has one more than the wire
    # ever carries.
    assert len(assistant_events) == 4
    assert len(wire_assistants) == 3
    # every request the model actually received is a prefix of the final one,
    # and every message in it matches the transcript record it came from
    for request in provider.requests:
        req_tools = [m for m in request if m["role"] == "tool"]
        req_assistants = [m for m in request if m["role"] == "assistant"]
        for msg, ev in zip(req_tools, tool_events[:len(req_tools)]):
            expected = ev["result"] + ("\n\n" + ev["follow_up"] if "follow_up" in ev else "")
            assert msg["content"] == expected, (msg, ev)
        for msg, ev in zip(req_assistants, assistant_events[:len(req_assistants)]):
            assert msg["content"] == ev.get("placeholder", ev["text"])
    assert len(next(e for e in tool_events if e["tool"] == "finish")["result"]) > 3000


@pytest.mark.parametrize("build", SCENARIOS)
def test_scenarios_are_legal_for_every_provider(parts, build):
    # Spec §9.12: the last request of each scenario through both serializers.
    from dirtywork.providers.anthropic import _to_anthropic_messages
    from dirtywork.providers.openai_compat import _to_openai_messages
    from .provider_doubles import assert_strict_template_legal
    from .test_provider_anthropic import _assert_alternating
    provider, _result, _events_ = _run_scenario(parts, build)
    for history in provider.requests:
        assert_strict_template_legal(history)                       # OpenAI and (same serializer) Ollama
        assert _to_openai_messages(history)                          # serializes without error
        _system, messages = _to_anthropic_messages(history)
        _assert_alternating(messages)


def test_anthropic_serializes_a_mixed_turn_with_tool_result_blocks_in_call_order(parts):
    from dirtywork.providers.anthropic import _to_anthropic_messages
    provider, _r, _e = _run_scenario(parts, _scenario_finish_first_then_timeout)
    _system, messages = _to_anthropic_messages(provider.requests[1])
    last_user = [m for m in messages if m["role"] == "user"][-1]
    assert [b["tool_use_id"] for b in last_user["content"] if b.get("type") == "tool_result"] == ["f1", "b1"]

def test_bash_timeout_duration_strings_do_not_strike_issue_64(parts):
    """Regression test for issue #64: duration strings should not cause bad_args strikes."""
    wt, registry, sandbox, transcript, tmp_path = parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "bash", {"command": "echo 1", "timeout": "30s"})]),
        _resp(tool_calls=[_call("c2", "bash", {"command": "echo 2", "timeout": "60s"})]),
        _resp(tool_calls=[_call("c3", "bash", {"command": "echo 3", "timeout": "60s"})]),
        _resp(content="done"),
    ])
    runner = Runner(
        provider=provider,
        registry=default_registry(),
        sandbox=sandbox,
        transcript=transcript,
        model="qwen/qwen3-coder-next",
    )
    result = runner.run("system prompt", "initial task")
    transcript.close()
    # No tool_result text should start with ERROR: bad arguments
    tool_results = [e["result"] for e in _tool_events(_events(tmp_path)) if "result" in e]
    for tr in tool_results:
        assert not tr.startswith("ERROR: bad arguments"), f"Unexpected error in tool result: {tr}"
    assert result.status == "completed"


class _NoticeSandbox(HostSandbox):
    """A real host sandbox that queues a stray_kill notice whenever a bash
    command contains the word NOTICE -- so a notice can be raised by an
    ordinary tool call OR by the --verify command, exactly where the docker
    sandbox would raise it."""

    def __init__(self, worktree):
        super().__init__(worktree)
        self._notices = []

    def bash(self, command, timeout=120):
        out = super().bash(command, timeout)
        if "NOTICE" in command:
            self._notices.append(("stray_kill", "KILLED"))
        return out

    def drain_notices(self):
        q, self._notices = self._notices, []
        return q


# Tests for issue #61: sandbox notices (kinds "stray_kill" and "sandbox_reset")
# should be delivered through existing #60 carriers and recorded as nudge events.

def test_tool_call_turn_with_sandbox_notices(parts):
    # a. tool-call turn with a queued ("stray_kill", "KILLED") and a stall nudge
    # on the same turn -> the last tool_result's follow_up == "KILLED\n\n<stall text>";
    # with a malformed entry as well -> "<malformed text>\n\nKILLED\n\n<stall text>";
    # nudge events kind="stray_kill" with via="tool_result"; the tool result's
    # `result` field unchanged.
    wt, registry, sandbox, transcript, tmp = parts
    # Create a real host sandbox that raises notices when commands contain "NOTICE"
    notices_sandbox = _NoticeSandbox(wt)

    # Write a file so read_file has something to return
    (wt / "f.txt").write_text("data\n")

    # Use a bash command that contains NOTICE to trigger the notice
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "bash", {"command": "echo NOTICE"})]),
        _resp(content="done"),
    ])
    r = Runner(provider, registry, notices_sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()

    assert result.status == "completed"
    # Check the tool result has follow_up with KILLED
    events = _events(tmp)
    tool_events = [e for e in events if e["event"] == "tool_result" and e["tool"] == "bash"]
    assert len(tool_events) >= 1
    # The follow_up should contain KILLED, possibly with stall text if there was one
    assert "KILLED" in tool_events[-1]["follow_up"]

    # Check the nudge event
    nudges = [e for e in events if e["event"] == "nudge"]
    stray_kills = [e for e in nudges if e["kind"] == "stray_kill"]
    assert len(stray_kills) == 1
    assert stray_kills[0]["via"] == "tool_result"

    # The tool result itself should be unchanged (exit code 0)
    assert "exit code: 0" in tool_events[-1]["result"]


def test_tool_call_turn_with_sandbox_notices_and_malformed(parts):
    # Spec #61 §5.2, first row: a mixed turn -- one unaddressable entry plus a real
    # bash call that raises a notice. The follow-up on the turn's last tool result
    # is joined in the documented order: malformed_entry, then the sandbox notice.
    wt, registry, sandbox, transcript, tmp = parts
    notices_sandbox = _NoticeSandbox(wt)
    provider = FakeProvider([
        _resp(tool_calls=[_bad_entry(), _call("c1", "bash", {"command": "echo NOTICE"})]),
        _resp(content="done"),
    ])
    r = Runner(provider, registry, notices_sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    events = _events(tmp)
    bash_result = next(e for e in events if e["event"] == "tool_result" and e["tool"] == "bash")
    malformed_text = ("1 of your tool calls were malformed (unaddressable: no usable id/name) "
                      "and were discarded. Re-issue them as valid tool calls.")
    assert bash_result["follow_up"] == malformed_text + "\n\nKILLED"
    assert bash_result["result"].startswith("exit code: 0")
    nudges = [e for e in events if e["event"] == "nudge"]
    assert [n["kind"] for n in nudges] == ["malformed_entry", "stray_kill"]
    assert all(n["via"] == "tool_result" for n in nudges)
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert tool_msgs[-1]["content"].endswith("\n\n" + malformed_text + "\n\nKILLED")
def test_finish_with_failing_verify_and_sandbox_notices(parts):
    # b. finish + failing verify (feedback continues the run) with a notice
    # queued by the verify bash -> the finish tool_result's result is the feedback
    # text only and its follow_up == "KILLED"; the nudge record has via "tool_result".
    wt, registry, sandbox, transcript, tmp = parts
    notices_sandbox = _NoticeSandbox(wt)

    provider = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
        _resp(content="fixed"),  # Model responds to verify failure with fix
    ])
    r = Runner(provider, registry, notices_sandbox, transcript,
               model="m", verify="echo NOTICE; false", verify_rounds=1)
    result = r.run("s", "t")
    transcript.close()

    assert result.status == "completed" or result.status == "verify_failed"
    events = _events(tmp)

    # Find the finish tool result
    finish_events = [e for e in events if e["event"] == "tool_result" and e["tool"] == "finish"]
    assert len(finish_events) >= 1
    # The finish result should have follow_up with KILLED
    if "follow_up" in finish_events[0]:
        assert "KILLED" in finish_events[0]["follow_up"]
    # The finish result's result field should contain the verify feedback but not KILLED
    if "result" in finish_events[0]:
        assert "VERIFY FAILED" in finish_events[0]["result"]
        assert "KILLED" not in finish_events[0]["result"]


def test_prose_answer_with_sandbox_notices(parts):
    # c. prose answer + failing verify -> the next user message == "<feedback>\n\nKILLED"; nudge via "user".
    wt, registry, sandbox, transcript, tmp = parts
    notices_sandbox = _NoticeSandbox(wt)

    provider = FakeProvider([
        _resp(content="done"),
        _resp(content="fixed"),  # Model responds to verify failure
    ])
    r = Runner(provider, registry, notices_sandbox, transcript,
               model="m", verify="echo NOTICE; false", verify_rounds=1)
    result = r.run("s", "t")
    transcript.close()

    # The run should complete or fail verify
    events = _events(tmp)
    nudges = [e for e in events if e["event"] == "nudge" and e["kind"] == "stray_kill"]
    assert len(nudges) >= 1
    assert nudges[0]["via"] == "user"

    # Check the user message contains both feedback and KILLED
    final_request = provider.requests[-1]
    last_user_msg = [m for m in final_request if m["role"] == "user"][-1]
    assert "KILLED" in last_user_msg["content"]
    # The feedback text should be there too (from verify failure)
    assert "verify failed" in last_user_msg["content"] or "VERIFY FAILED" in last_user_msg["content"]


def test_empty_reply_with_sandbox_notices(parts):
    # Spec #61 §5.2, text-only row: a notice the watchdog thread queued between
    # turns is pending when a text-only turn (an empty reply) drains -- it rides
    # the next user message after the kind's nudge, via "user".
    wt, registry, sandbox, transcript, tmp = parts
    notices_sandbox = _NoticeSandbox(wt)
    notices_sandbox._notices.append(("sandbox_reset", "RESET"))
    provider = FakeProvider([_resp(content=""), _resp(content="done")])
    r = Runner(provider, registry, notices_sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()
    assert result.status == "completed"
    second = provider.requests[1]
    assert second[-1]["role"] == "user"
    assert second[-1]["content"] == NUDGES["empty"] + "\n\nRESET"
    nudges = [e for e in _events(tmp) if e["event"] == "nudge"]
    assert [n["kind"] for n in nudges] == ["empty", "sandbox_reset"]
    assert all(n["via"] == "user" for n in nudges)
def test_run_ending_turns_with_sandbox_notices(parts):
    # e. run-ending turns: verify passes -> completed
    # (a) Runner(verify="echo NOTICE") — verify PASSES; provider = FakeProvider([_resp(tool_calls=[_call("c1", "finish", {"summary": "done"})])]); status "completed"; exactly one nudge event, kind "stray_kill", and "via" NOT in it.
    wt, registry, sandbox, transcript, tmp = parts
    notices_sandbox = _NoticeSandbox(wt)
    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "finish", {"summary": "done"})]),
    ])
    r = Runner(provider, registry, notices_sandbox, transcript, model="m", verify="echo NOTICE")
    result = r.run("s", "t")
    transcript.close()

    assert result.status == "completed"
    events = _events(tmp)
    nudges = [e for e in events if e["event"] == "nudge" and e["kind"] == "stray_kill"]
    assert len(nudges) >= 1
    # stray_kill from verify should have NO "via" key since it's on a run-ending turn
    assert "via" not in nudges[0]



def test_sandbox_without_drain_notices_works(parts):
    # g. a sandbox double WITHOUT drain_notices -> runs exactly as before
    # (no AttributeError).
    wt, registry, sandbox, transcript, tmp = parts

    class NoDrainSandbox:
        def bash(self, command, timeout=120):
            return "exit code: 0\ndone"

        def start(self, worktree, repo, slug, base_commit, *, branch=None, seed_from_worktree=False):
            pass

        def read_file(self, path, offset=0, limit=400):
            return ""

        def write_file(self, path, content):
            return "ok"

        def append_file(self, path, text):
            return "ok"

        def edit_file(self, path, old_string, new_string):
            return "ok"

        def apply_edits(self, path, edits):
            return "ok"

        def insert_before(self, path, anchor, text):
            return "ok"

        def insert_after(self, path, anchor, text):
            return "ok"

        def list_dir(self, path="."):
            return ""

        def grep(self, pattern, path=".", glob=None, timeout=30):
            return ""

        def finalize(self):
            from dirtywork.sandbox import RunArtifacts
            return RunArtifacts()

    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "read_file", {"path": "f.txt"})]),
        _resp(content="done"),
    ])
    r = Runner(provider, registry, NoDrainSandbox(), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()

    assert result.status == "completed"
    # No AttributeError should have been raised


def test_exploding_sandbox_still_works(parts):
    # h. ExplodingSandbox / BudgetBustingSandbox paths still end the run
    # with their statuses.
    wt, registry, sandbox, transcript, tmp = parts

    class ExplodingSandbox:
        def write_file(self, path, content):
            from dirtywork.budget import BudgetExceeded
            raise BudgetExceeded("worktree over budget")

    provider = FakeProvider([
        _resp(tool_calls=[_call("c1", "write_file", {"path": "x", "content": "y"})]),
    ])
    r = Runner(provider, registry, ExplodingSandbox(), transcript, model="m")
    result = r.run("s", "t")
    transcript.close()

    assert result.status == "budget_exceeded"

def test_chunk_target_cap_basis_and_floors():
    """Spec §3.1: chunk_target's (chars, lines) are derived from the cap and cut sizes."""
    from dirtywork.runner import chunk_target, MIN_CHUNK_CHARS, MIN_CHUNK_LINES

    # Basis is cap_chars when cut_chars == 0: (1024, 17) since
    # 8192 * 4 = 32768, divided by 4 = 8192, which is >= MIN_CHUNK_CHARS=200
    # and lines = 8192 / 60 = ~136 (but actual formula: 8192 / 4 / 60 = 34 lines)
    assert chunk_target(1024, 0, 0) == (1024, 17)
    assert chunk_target(2048, 0, 0) == (2048, 34)
    assert chunk_target(4096, 0, 0) == (4096, 68)
    assert chunk_target(8192, 0, 0) == (8192, 136)

    # When cut_chars > 0 but smaller than cap, basis is cut_chars
    # (1024, 3000, 55): basis=3000, chars=750 (floored), per_line=3000/55=54.5,
    # lines = 750 / 54.5 = ~13.7 -> floor to 13
    assert chunk_target(1024, 3000, 55) == (750, 13)

    # (1024, 100, 2): basis=100, chars = max(200, 100//4) = max(200, 25) = 200 (floor)
    # per_line = DEFAULT_LINE_CHARS=60, lines = max(5, 200//60) = max(5, 3) = 5
    assert chunk_target(1024, 100, 2) == (200, 5)

    # Per-line calculation from the call: 300/10 = 30 chars/line → 200/30 = 6.67 -> 6
    assert chunk_target(1024, 300, 10) == (200, 6)


def test_reply_size_and_call_size(parts):
    """Spec §3.2: reply_size and call_size measure received chars and one-call sizes."""
    wt, registry, sandbox, transcript, tmp = parts
    from dirtywork.runner import reply_size, call_size

    # Build two calls with escaped newlines
    raw1 = '{"path":"x","content":"a\\nb"}'
    raw2 = "{}"
    call1 = _bad_args("c1", "write_file", raw=raw1)
    call2 = _bad_args("c2", "read_file", raw=raw2)

    # reply_size: (text_chars, raw_chars_of_all_calls)
    resp = _resp(content="abc", tool_calls=[call1, call2])
    text_chars, raw_chars = reply_size(resp)
    assert text_chars == 3
    # raw1 has len('{"path":"x","content":"a\\nb"}') = 28
    # raw2 has len('{}') = 2
    assert raw_chars == len(raw1) + len(raw2)

    # call_size for first call: (raw_chars, lines)
    # raw1 = '{"path":"x","content":"a\\nb"}' has one escaped newline (\n)
    chars, lines = call_size(call1)
    assert chars == len(raw1)
    # escaped newline count: "\\n" appears once, and actual newlines
    assert lines == 2

    # call_size for empty raw
    call3 = _bad_args("c3", "write_file", raw="")
    chars, lines = call_size(call3)
    assert chars == 0
    assert lines == 0

    # Call with no newline in raw → lines == 1
    call_no_newline = _bad_args("c4", "read_file", raw='{"path":"x"}')
    chars, lines = call_size(call_no_newline)
    assert chars == len('{"path":"x"}')
    assert lines == 1


def test_truncated_text_nudge_carries_the_numbers(parts):
    """Spec §3.1: the text nudge for length truncation includes cap and target numbers."""
    import tempfile
    from pathlib import Path

    wt, registry, sandbox, transcript, tmp = parts
    from dirtywork.runner import MAX_TRUNCATED_REPLIES

    # First turn is truncated, second succeeds
    provider = FakeProvider([
        _resp(content="I will now", finish_reason="length",
              usage={"prompt_tokens": 1, "completion_tokens": 5}),
        _resp(content="ok"),
    ])

    r = Runner(provider, registry, sandbox, transcript, model="m", max_tokens=1234)
    result = r.run("s", "t")
    transcript.close()

    assert result.status == "completed"

    # The second request's last user message should have the truncated nudge
    first_req = provider.requests[0]
    second_req = provider.requests[1]

    # Find the last user message in the second request
    last_user_msg = [m for m in second_req if m["role"] == "user"][-1]
    nudge_text = last_user_msg["content"]

    # Check the format string values are present - use 1234 as max_tokens
    from dirtywork.runner import chunk_target, call_size, reply_size
    text_chars, raw_chars = reply_size(_resp())
    target_chars, target_lines = chunk_target(1234, 0, 0)
    trunc_dict = dict(cap=1234, cap_chars=1234*4, received=text_chars + raw_chars,
                      cut_chars=0, cut_lines=0,
                      target_chars=target_chars, target_lines=target_lines,
                      n=1, max=6)
    assert str(trunc_dict["cap"]) in nudge_text
    assert str(trunc_dict["cap_chars"]) in nudge_text
    # "I will now" = 10 characters (actual: 'I will now' without the trailing space)
    assert "received only 10 characters" in nudge_text

    # Second run: empty content (need fresh transcript)
    with tempfile.TemporaryDirectory() as tmp2:
        wt2 = Path(tmp2) / "wt"
        wt2.mkdir()
        transcript2 = Transcript(Path(tmp2) / "t.jsonl")
        registry2 = default_registry(transcript=transcript2)
        sandbox2 = HostSandbox(wt2)

        provider2 = FakeProvider([
            _resp(content="", finish_reason="length",
                  usage={"prompt_tokens": 1, "completion_tokens": 5}),
            _resp(content="ok"),
        ])
        r2 = Runner(provider2, registry2, sandbox2, transcript2, model="m", max_tokens=1234)
        result2 = r2.run("s", "t")
        transcript2.close()

        second_req2 = provider2.requests[1]
        last_user_msg2 = [m for m in second_req2 if m["role"] == "user"][-1]
        nudge_text2 = last_user_msg2["content"]
        assert "received only 0 characters" in nudge_text2


def test_truncated_call_results_carry_the_cut_calls_numbers(parts):
    """Spec §3.2: truncated_call_result includes the cut call's numbers."""
    import tempfile
    from pathlib import Path

    # Single cut write_file call
    wt1, registry1, sandbox1, transcript1, tmp1 = parts
    raw = '{"path": "x", "content": "a\\nb\\nc'
    tc1 = _bad_args("c", "write_file", raw=raw)
    truncated_resp = _resp(tool_calls=[tc1], finish_reason="length",
                          usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider = FakeProvider([truncated_resp, _resp(content="done")])

    r = Runner(provider, registry1, sandbox1, transcript1, model="m", max_tokens=8192)
    result = r.run("s", "t")
    transcript1.close()

    assert result.status == "completed"

    # Check the tool message in the second request
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert len(tool_msgs) == 1

    # Build expected result with _trunc_dict
    from dirtywork.runner import call_size, truncated_call_result
    cut_chars, cut_lines = call_size(tc1)
    trunc_dict = _trunc_dict(tc1)

    # The result should match truncated_call_result
    expected = truncated_call_result("write_file", tc1.raw_arguments, trunc_dict)
    assert tool_msgs[0]["content"] == expected

    # Generic form for malformed JSON (need fresh run)
    with tempfile.TemporaryDirectory() as tmp2:
        wt2 = Path(tmp2) / "wt"
        wt2.mkdir()
        transcript2 = Transcript(Path(tmp2) / "t.jsonl")
        registry2 = default_registry(transcript=transcript2)
        sandbox2 = HostSandbox(wt2)

        raw2 = "{"
        tc2 = _bad_args("c", "read_file", raw=raw2)
        truncated_resp2 = _resp(tool_calls=[tc2], finish_reason="length",
                               usage={"prompt_tokens": 1, "completion_tokens": 1})
        provider2 = FakeProvider([truncated_resp2, _resp(content="done")])

        r2 = Runner(provider2, registry2, sandbox2, transcript2, model="m", max_tokens=8192)
        result2 = r2.run("s", "t")
        transcript2.close()

        tool_msgs2 = [m for m in provider2.requests[1] if m["role"] == "tool"]
        trunc_dict2 = _trunc_dict(tc2)
        expected2 = truncated_call_result("read_file", tc2.raw_arguments, trunc_dict2)
        assert tool_msgs2[0]["content"] == expected2

    # Two cut calls in one turn - both get same text with same n
    with tempfile.TemporaryDirectory() as tmp3:
        wt3 = Path(tmp3) / "wt"
        wt3.mkdir()
        transcript3 = Transcript(Path(tmp3) / "t.jsonl")
        registry3 = default_registry(transcript=transcript3)
        sandbox3 = HostSandbox(wt3)

        tc3a = _bad_args("c1", "write_file", raw='{"path": "a","content": "x')
        tc3b = _bad_args("c2", "read_file", raw='{"path": "b')
        truncated_resp3 = _resp(tool_calls=[tc3a, tc3b], finish_reason="length",
                               usage={"prompt_tokens": 1, "completion_tokens": 1})
        provider3 = FakeProvider([truncated_resp3, _resp(content="done")])

        r3 = Runner(provider3, registry3, sandbox3, transcript3, model="m", max_tokens=8192)
        result3 = r3.run("s", "t")
        transcript3.close()

        tool_msgs3 = [m for m in provider3.requests[1] if m["role"] == "tool"]
        assert len(tool_msgs3) == 2

        # Both use the dict built at FIRST cut call (tc3a)
        d3 = _trunc_dict(tc3a)
        assert tool_msgs3[0]["content"] == truncated_call_result("write_file", tc3a.raw_arguments, d3)
        assert tool_msgs3[1]["content"] == truncated_call_result("read_file", tc3b.raw_arguments, d3)
        assert "cut-off reply 1 of 6" in tool_msgs3[1]["content"]

    # One complete call + one cut call - target from cut call only
    with tempfile.TemporaryDirectory() as tmp4:
        wt4 = Path(tmp4) / "wt"
        wt4.mkdir()
        (wt4 / "f.txt").write_text("data\n")
        transcript4 = Transcript(Path(tmp4) / "t.jsonl")
        registry4 = default_registry(transcript=transcript4)
        sandbox4 = HostSandbox(wt4)

        tc4a = _call("c1", "read_file", {"path": "f.txt"})
        tc4b = _bad_args("c2", "write_file", raw='{"path": "y","content": "z')
        mixed_resp = _resp(tool_calls=[tc4a, tc4b], finish_reason="length",
                          usage={"prompt_tokens": 1, "completion_tokens": 1})
        provider4 = FakeProvider([mixed_resp, _resp(content="done")])

        r4 = Runner(provider4, registry4, sandbox4, transcript4, model="m", max_tokens=8192)
        result4 = r4.run("s", "t")
        transcript4.close()

        tool_msgs4 = [m for m in provider4.requests[1] if m["role"] == "tool"]
        assert len(tool_msgs4) == 2

        # Only the cut call should have truncation text
        # The complete call (c1) executed successfully, so it gets normal result


def test_truncations_counts_once_per_turn(parts):
    """Spec §3.3: truncations counter increments once per turn, not per call."""
    wt, registry, sandbox, transcript, tmp = parts

    # One truncated reply then success
    provider1 = FakeProvider([
        _resp(content="truncated", finish_reason="length",
              usage={"prompt_tokens": 1, "completion_tokens": 5}),
        _resp(content="ok"),
    ])
    r1 = Runner(provider1, registry, sandbox, transcript, model="m", max_tokens=8192)
    result1 = r1.run("s", "t")
    transcript.close()

    assert result1.status == "completed"
    # Check the run_end has truncations=1
    end_events = [e for e in _events(tmp) if e["event"] == "run_end"]
    assert len(end_events) == 1
    assert end_events[0]["truncations"] == 1

    # A turn with two cut calls → +1 only
    (tmp / "f.txt").write_text("data\n")
    tc1 = _bad_args("c1", "write_file", raw='{"path": "a')
    tc2 = _bad_args("c2", "read_file", raw='{"path": "b')
    truncated = _resp(tool_calls=[tc1, tc2], finish_reason="length",
                     usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider2 = FakeProvider([truncated, _resp(content="done")])

    # Need new transcript
    transcript2 = Transcript(tmp / "t2.jsonl")
    r2 = Runner(provider2, registry, sandbox, transcript2, model="m", max_tokens=8192)
    result2 = r2.run("s", "t")
    transcript2.close()

    # Verify the truncations count is 1 in result (it's inside extra)
    assert result2.extra.get("truncations") == 1

    # A length turn with a complete valid call (no truncation nudge, no count)
    (tmp / "f2.txt").write_text("data\n")
    complete_call = _resp(tool_calls=[_call("c", "write_file",
                                            {"path": "f2.txt", "content": "hello\n"})],
                         finish_reason="length",
                         usage={"prompt_tokens": 1, "completion_tokens": 1})
    provider3 = FakeProvider([complete_call, _resp(content="done")])

    transcript3 = Transcript(tmp / "t3.jsonl")
    r3 = Runner(provider3, registry, sandbox, transcript3, model="m", max_tokens=8192)
    result3 = r3.run("s", "t")
    transcript3.close()

    # The turn should complete without incrementing truncations
    assert result3.status == "completed"
    # Verify result.extra["truncations"] equals run_end value
    assert result3.extra.get("truncations") == 0

def test_six_cutoff_replies_end_the_run(parts):
    # Spec #65 S3: a cut-off reply followed by a successful tool call resets
    # FailureTracker's consecutive counters, so a model that alternates
    # cut/write forever would never hit the 3-consecutive-failure abort --
    # the run-level truncation budget (6, never reset) is what ends this run.
    wt, registry, sandbox, transcript, tmp = parts
    cut = _resp(content="header", finish_reason="length")

    def write(i):
        return _resp(tool_calls=[_call(f"w{i}", "write_file",
                                       {"path": "rows.csv", "content": f"row{i}\n"})])

    responses = []
    for i in range(5):
        responses.append(cut)
        responses.append(write(i))
    responses.append(cut)
    assert len(responses) == 11

    provider = FakeProvider(responses)
    r = Runner(provider, registry, sandbox, transcript, model="m", max_tokens=1024, max_turns=20)
    result = r.run("s", "t")
    transcript.close()

    assert result.status == "model_error"
    assert result.final_message == TRUNCATION_ABORT.format(n=6, cap=1024)
    assert result.extra["truncations"] == 6

    events = _events(tmp)
    run_end = [e for e in events if e["event"] == "run_end"][0]
    assert run_end["truncations"] == 6
    truncated_nudges = [e for e in events if e["event"] == "nudge" and e.get("kind") == "truncated"]
    assert len(truncated_nudges) == 6
    assert "via" not in truncated_nudges[-1]


def test_consecutive_rule_wins_over_the_cutoff_budget(parts):
    # Spec #65: FailureTracker's 3-consecutive-empty_reply abort is checked
    # BEFORE the run-level truncation budget on the same turn, so three
    # cut-off replies in a row end the run as a consecutive-failure abort --
    # even though the third of them is also the 6th cut-off reply overall.
    wt, registry, sandbox, transcript, tmp = parts
    cut = _resp(content="header", finish_reason="length")

    def write(i):
        return _resp(tool_calls=[_call(f"w{i}", "write_file",
                                       {"path": "rows.csv", "content": f"row{i}\n"})])

    provider = FakeProvider([cut, write(0), cut, write(1), cut, write(2), cut, cut, cut])
    r = Runner(provider, registry, sandbox, transcript, model="m", max_tokens=1024, max_turns=20)
    result = r.run("s", "t")
    transcript.close()

    assert result.status == "model_error"
    assert result.final_message == "aborted after 3 consecutive empty_reply failures"


def test_sixth_truncation_on_the_tool_path_records_its_result(parts):
    # Spec #65: the run-level truncation budget also ends the run when the
    # 6th cut-off reply is a truncated TOOL CALL (case a: malformed_args with
    # finish_reason=="length"), not a bare text reply -- and the tool_result
    # for that call is still written to the transcript before the run ends.
    wt, registry, sandbox, transcript, tmp = parts
    cut = _resp(content="header", finish_reason="length")

    def write(i):
        return _resp(tool_calls=[_call(f"w{i}", "write_file",
                                       {"path": "rows.csv", "content": f"row{i}\n"})])

    sixth = _resp(tool_calls=[_bad_args("c", "write_file", '{"path": "x", "content": "abc')],
                  finish_reason="length")
    provider = FakeProvider([cut, write(0), cut, write(1), cut, write(2), cut, write(3),
                             cut, write(4), sixth])
    r = Runner(provider, registry, sandbox, transcript, model="m", max_tokens=1024, max_turns=20)
    result = r.run("s", "t")
    transcript.close()

    assert result.status == "model_error"
    assert result.final_message == TRUNCATION_ABORT.format(n=6, cap=1024)

    events = _events(tmp)
    tool_results = [e for e in events if e["event"] == "tool_result"]
    assert "cut off at the --max-tokens cap" in tool_results[-1]["result"]
    run_end = [e for e in events if e["event"] == "run_end"][0]
    assert run_end["status"] == "model_error"


def test_start_fingerprint_before_first_chat(parts):
    # Spec #66 §4.1 (1): the start fingerprint is taken before the first chat
    # call, so it appears at the head of the sandbox's command log.
    wt, registry, sandbox, transcript, tmp = parts
    sandbox = FingerprintSandbox(wt, ["a" * 40])
    provider = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
        _resp(tool_calls=[_call("f2", "finish", {"summary": "done"})]),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()

    assert sandbox.commands[0] == (FINGERPRINT_SCRIPT, 60)
    finish_results = _finish_results(_events(tmp))
    assert finish_results[0] == UNCHANGED_PLAIN
    assert result.status == "completed"
    run_end = next(e for e in _events(tmp) if e["event"] == "run_end")
    assert run_end["changed"] is False

    # A second run whose fingerprint DOES move: a single finish completes
    # right away and run_end reports the change.
    transcript2 = Transcript(tmp / "t2.jsonl")
    registry2 = default_registry(transcript=transcript2)
    sandbox2 = FingerprintSandbox(wt, ["a" * 40, "b" * 40])
    provider2 = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "done"})])])
    r2 = Runner(provider2, registry2, sandbox2, transcript2, model="m")
    result2 = r2.run("s", "t")
    transcript2.close()

    assert result2.status == "completed"
    events2 = [json.loads(l) for l in (tmp / "t2.jsonl").read_text().splitlines()]
    run_end2 = next(e for e in events2 if e["event"] == "run_end")
    assert run_end2["changed"] is True


def test_start_fingerprint_failure_turns_guard_off(parts):
    # Spec #66 §4.1 (1)/§4.3: a failed start measurement disables the guard
    # for the whole run (fp_start stays None) -- no rejection, changed=None.
    wt, registry, sandbox, transcript, tmp = parts
    sandbox = FingerprintSandbox(wt, [None])
    provider = FakeProvider([_resp(tool_calls=[_call("f1", "finish", {"summary": "done"})])])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()

    assert result.status == "completed"
    events = _events(tmp)
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end["changed"] is None
    assert run_end["changed_reason"] == "error: boom"
    assert len([c for c in sandbox.commands if c == (FINGERPRINT_SCRIPT, 60)]) == 1


def test_zero_change_finish_rejected_once_then_completed(git_parts):
    # Spec #66 §4.3: the first zero-change finish is rejected once; a second
    # one (still require_changes=False) is accepted and verify runs.
    wt, registry, sandbox, transcript, tmp = git_parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
        _resp(tool_calls=[_call("f2", "finish", {"summary": "done"})]),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m", verify="test -e f.txt")
    result = r.run("s", "t")
    transcript.close()

    events = _events(tmp)
    finish_results = _finish_results(events)
    assert finish_results[0] == UNCHANGED_PLAIN
    nudge = next(e for e in events if e["event"] == "nudge" and e["kind"] == "unchanged_finish")
    assert nudge["via"] == "tool_result"

    commands = [c for c, _ in sandbox.commands]
    fp_indices = [i for i, c in enumerate(commands) if c == FINGERPRINT_SCRIPT]
    verify_idx = commands.index("test -e f.txt")
    assert verify_idx > fp_indices[1]

    assert result.status == "completed"
    assert commands.count("test -e f.txt") == 1
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end["changed"] is False
    assert result.extra["changed"] is False


def test_zero_change_finish_ends_unchanged_when_required(git_parts):
    # Spec #66 §4.3: with require_changes, a second zero-change finish ends
    # the run `unchanged` instead of retrying verify.
    wt, registry, sandbox, transcript, tmp = git_parts
    provider = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
        _resp(tool_calls=[_call("f2", "finish", {"summary": "done"})]),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m",
               require_changes=True, verify="test -e f.txt")
    result = r.run("s", "t")
    transcript.close()

    events = _events(tmp)
    assert _finish_results(events) == [UNCHANGED_REQUIRED, "run not finished: nothing changed"]
    assert result.status == "unchanged"
    assert result.final_message == "done"
    assert "test -e f.txt" not in [c for c, _ in sandbox.commands]
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end["changed"] is False

    # Variant: a write between the two finishes clears the guard -- the run
    # completes and run_end reports the change.
    transcript2 = Transcript(tmp / "t2.jsonl")
    registry2 = default_registry(transcript=transcript2)
    sandbox2 = FingerprintSandbox(wt, hashes=None)
    provider2 = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
        _resp(tool_calls=[_call("w1", "write_file", {"path": "g.txt", "content": "x\n"})]),
        _resp(tool_calls=[_call("f2", "finish", {"summary": "done"})]),
    ])
    r2 = Runner(provider2, registry2, sandbox2, transcript2, model="m",
                require_changes=True, verify="test -e f.txt")
    result2 = r2.run("s", "t")
    transcript2.close()

    assert result2.status == "completed"
    events2 = [json.loads(l) for l in (tmp / "t2.jsonl").read_text().splitlines()]
    run_end2 = next(e for e in events2 if e["event"] == "run_end")
    assert run_end2["changed"] is True

    # Variant: finalize raising does not change the `unchanged` status.
    transcript3 = Transcript(tmp / "t3.jsonl")
    registry3 = default_registry(transcript=transcript3)
    sandbox3 = FingerprintSandbox(wt, hashes=None)
    provider3 = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
        _resp(tool_calls=[_call("f2", "finish", {"summary": "done"})]),
    ])
    r3 = Runner(provider3, registry3, sandbox3, transcript3, model="m",
                require_changes=True,
                finalize=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    result3 = r3.run("s", "t")
    transcript3.close()

    assert result3.status == "unchanged"
    assert isinstance(result3.extra["finalize_error"], str) and result3.extra["finalize_error"]


def test_plain_answer_rejection_is_a_user_message(git_parts):
    # Spec #66 §4.1, §4.3 test 15: plain-answer path rejects as user message
    # (not tool_result); nudge.kind="unchanged_finish" has via="user"
    wt, registry, sandbox, transcript, tmp = git_parts
    provider = FakeProvider([
        _resp(content="all done"),
        _resp(content="all done"),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()

    events = _events(tmp)
    # Second answer should be rejected (provider.requests[1][-1] contains the rejection)
    assert provider.requests[1][-1] == {"role": "user", "content": UNCHANGED_PLAIN}
    nudge = next(e for e in events if e["event"] == "nudge" and e["kind"] == "unchanged_finish")
    assert nudge["via"] == "user"
    assert result.status == "completed"
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end["changed"] is False

    # With require_changes=True, second answer should end with "unchanged" status
    transcript2 = Transcript(tmp / "t2.jsonl")
    registry2 = default_registry(transcript=transcript2)
    provider2 = FakeProvider([
        _resp(content="all done"),
    ])
    r2 = Runner(provider2, registry2, sandbox, transcript2, model="m",
                require_changes=True)
    result2 = r2.run("s", "t")
    transcript2.close()

    events2 = _events(tmp / "t2.jsonl")
    assert result2.status == "unchanged"
    run_end2 = next(e for e in events2 if e["event"] == "run_end")
    assert run_end2["changed"] is False

    # Rejection on the last allowed turn (max_turns=1) ends with "max_turns"
    transcript3 = Transcript(tmp / "t3.jsonl")
    registry3 = default_registry(transcript=transcript3)
    provider3 = FakeProvider([
        _resp(content="all done"),
    ])
    r3 = Runner(provider3, registry3, sandbox, transcript3, model="m",
                max_turns=1, require_changes=True)
    result3 = r3.run("s", "t")
    transcript3.close()

    assert result3.status == "max_turns"
    events3 = _events(tmp / "t3.jsonl")
    run_end3 = next(e for e in events3 if e["event"] == "run_end")
    assert run_end3["status"] == "max_turns"


def test_mixed_turn_rejection(git_parts):
    # Spec #66 §4.3 test 16: mixed turn with finish and other calls
    # Finish is rejected, timeout nudge still delivered in same turn
    wt, registry, sandbox, transcript, tmp = git_parts

    class TimeoutSandbox(FingerprintSandbox):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.timeout_turn = False

        def bash(self, command, timeout=120):
            # Record commands
            self.commands.append((command, timeout))
            if command == FINGERPRINT_SCRIPT or not self.timeout_turn:
                return super().bash(command, timeout)
            # Return timeout for sleep command
            if "sleep" in command:
                from dirtywork.tools import timeout_result
                return timeout_result(timeout)
            return super().bash(command, timeout)

    sandbox = TimeoutSandbox(wt, hashes=["a" * 40])
    provider = FakeProvider([
        _resp(tool_calls=[
            _call("r", "read_file", {"path": "f.txt"}),
            _call("f", "finish", {"summary": "s"}),
        ]),
        _resp(tool_calls=[
            _call("b", "bash", {"command": "sleep 999", "timeout": 1}),
            _call("f2", "finish", {"summary": "s2"}),
        ]),
    ])
    # Remove timeout=1 - instead add a bash call in first turn with small timeout
    r = Runner(provider, registry, sandbox, transcript, model="m")
    result = r.run("s", "t")
    transcript.close()

    # In provider.requests[1], tool messages come in call order (read_file, bash, finish)
    # Check that both read_file and finish are present
    assert len(provider.requests[1]) >= 4  # system + at least 3 messages
    tool_msgs = [m for m in provider.requests[1] if m["role"] == "tool"]
    assert len(tool_msgs) >= 2
    
    # The finish tool message should contain UNCHANGED_PLAIN and TIMEOUT_NUDGE
    finish_msgs = [m for m in provider.requests[1] if m.get("tool_call_id") == "f2"]
    assert len(finish_msgs) >= 1
    finish_content = finish_msgs[0]["content"]
    assert UNCHANGED_PLAIN in finish_content
    assert TIMEOUT_NUDGE in finish_content

    # Second finish should result in completed status
    events = _events(tmp)
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end["status"] == "completed"


def test_fingerprint_exceptions_map_like_verify(parts):
    # Spec #66 §4.3 test 17: exceptions from fingerprints map like verify
    wt, registry, sandbox, transcript, tmp = parts

    # Test BudgetExceeded from start fingerprint
    class RaisingSandbox1(FingerprintSandbox):
        def bash(self, command, timeout=120):
            if command == FINGERPRINT_SCRIPT:
                raise BudgetExceeded("disk")
            return super().bash(command, timeout)

    sandbox1 = RaisingSandbox1(wt, hashes=None)
    provider1 = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
    ])
    r1 = Runner(provider1, registry, sandbox1, transcript, model="m")
    result1 = r1.run("s", "t")
    transcript.close()

    assert result1.status == "budget_exceeded"
    events1 = _events(tmp)
    run_end1 = next(e for e in events1 if e["event"] == "run_end")
    assert run_end1["changed"] is None
    assert "budget: disk" in run_end1.get("changed_reason", "")

    # Test SandboxError
    from dirtywork.sandbox import SandboxError

    class RaisingSandbox2(FingerprintSandbox):
        def bash(self, command, timeout=120):
            if command == FINGERPRINT_SCRIPT:
                raise SandboxError("gone")
            return super().bash(command, timeout)

    transcript2 = Transcript(tmp / "t2.jsonl")
    registry2 = default_registry(transcript=transcript2)
    sandbox2 = RaisingSandbox2(wt, hashes=None)
    provider2 = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
    ])
    r2 = Runner(provider2, registry2, sandbox2, transcript2, model="m")
    result2 = r2.run("s", "t")
    transcript2.close()

    assert result2.status == "sandbox_error"
    events2 = _events(tmp)
    run_end2 = next(e for e in events2 if e["event"] == "run_end")
    assert run_end2["changed"] is None
    assert "sandbox_error" in run_end2.get("changed_reason", "")

    # Test on completion path - BudgetExceeded
    transcript3 = Transcript(tmp / "t3.jsonl")
    registry3 = default_registry(transcript=transcript3)
    sandbox3 = FingerprintSandbox(wt, hashes=["a" * 40, BudgetExceeded("disk")])
    provider3 = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
    ])
    r3 = Runner(provider3, registry3, sandbox3, transcript3, model="m")
    result3 = r3.run("s", "t")
    transcript3.close()

    assert result3.status == "budget_exceeded"
    events3 = _events(tmp)
    finish_results3 = [e for e in events3 if e["event"] == "tool_result" and e["tool"] == "finish"]
    assert len(finish_results3) == 2
    assert finish_results3[-1]["result"] == "run not finished: change check could not run (disk)"
    assert finish_results3[-1]["result"] == "run not finished: change check could not run (disk)"

    # Test KeyboardInterrupt from start
    class InterruptingSandbox(FingerprintSandbox):
        def bash(self, command, timeout=120):
            if command == FINGERPRINT_SCRIPT:
                raise KeyboardInterrupt
            return super().bash(command, timeout)

    transcript4 = Transcript(tmp / "t4.jsonl")
    registry4 = default_registry(transcript=transcript4)
    sandbox4 = InterruptingSandbox(wt, hashes=None)
    provider4 = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
    ])
    r4 = Runner(provider4, registry4, sandbox4, transcript4, model="m")
    result4 = r4.run("s", "t")
    transcript4.close()

    assert result4.status == "interrupted"
    events4 = _events(tmp)
    run_end4 = next(e for e in events4 if e["event"] == "run_end")
    assert run_end4["turns"] == 0
    # Exactly one run_end event (the interrupt handler doesn't write a second one)
    assert len([e for e in events4 if e["event"] == "run_end"]) == 1


def test_finish_time_fingerprint(parts, FingerprintSandbox):
    # Spec #66 §4.3 test 19: finish-time fingerprint behavior
    from dirtywork.budget import BudgetExceeded
    from dirtywork.changes import FINGERPRINT_SCRIPT

    # (a) max_turns run with changing fingerprints
    wt, registry, sandbox, transcript, tmp = parts

    class TrackingSandbox(FingerprintSandbox):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.commands = []

        def bash(self, command, timeout=120):
            # Record all commands
            self.commands.append((command, timeout))
            return super().bash(command, timeout)

    sandbox = TrackingSandbox(wt, hashes=["a" * 40, "b" * 40])
    provider = FakeProvider([
        _resp(tool_calls=[_call("r1", "read_file", {"path": "f.txt"})]),
        _resp(tool_calls=[_call("r2", "read_file", {"path": "f.txt"})]),
    ])
    r = Runner(provider, registry, sandbox, transcript, model="m", max_turns=2)
    result = r.run("s", "t")
    transcript.close()

    # finish-time measurement should show change=True
    events = _events(tmp)
    run_end = next(e for e in events if e["event"] == "run_end")
    assert run_end["changed"] is True

    # The fingerprint script should have been called after last chat and before drain_notices
    fp_commands = [i for i, (c, _) in enumerate(sandbox.commands) if c == FINGERPRINT_SCRIPT]
    assert len(fp_commands) == 2  # start + finish
    last_chat_idx = max(i for i, (c, _) in enumerate(sandbox.commands)
                       if "read_file" in c or "finish" in str(c))
    drain_idx = next((i for i, (c, _) in enumerate(sandbox.commands) if c == "drain_notices"),
                    len(sandbox.commands))
    # Check that finish-time fingerprint is after last chat
    assert fp_commands[-1] > last_chat_idx
    # And before drain_notices (or if drain is present)
    assert fp_commands[-1] < drain_idx

    # (b) subclass whose finalize() flips a flag
    class FlagSandbox(FingerprintSandbox):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.finalized = False

        def finalize(self):
            self.finalized = True
            return super().finalize()

        def bash(self, command, timeout=120):
            # On finalize call, return error to test that finish-time comes before finalize
            if command == FINGERPRINT_SCRIPT and self.finalized:
                return "ERROR: bash failed: gone"
            return super().bash(command, timeout)

    transcript2 = Transcript(tmp / "t2.jsonl")
    registry2 = default_registry(transcript=transcript2)
    sandbox2 = FlagSandbox(wt, hashes=["a" * 40])
    provider2 = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
    ])
    r2 = Runner(provider2, registry2, sandbox2, transcript2, model="m",
                finalize=lambda: {"flag": "set"})
    result2 = r2.run("s", "t")
    transcript2.close()

    # changed should not be None (measurement happened before finalize)
    events2 = _events(tmp)
    run_end2 = next(e for e in events2 if e["event"] == "run_end")
    assert run_end2["changed"] is not None

    # (c) subclass whose fingerprint bash queues a notice
    class NoticeSandbox(FingerprintSandbox):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.notices = []

        def drain_notices(self):
            notices = self.notices
            self.notices = []
            return notices

        def bash(self, command, timeout=120):
            if command == FINGERPRINT_SCRIPT:
                # Queue a notice from the fingerprint
                self.notices.append(("stray_kill", "text"))
            return super().bash(command, timeout)

    transcript3 = Transcript(tmp / "t3.jsonl")
    registry3 = default_registry(transcript=transcript3)
    sandbox3 = NoticeSandbox(wt, hashes=["a" * 40])
    provider3 = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
    ])
    r3 = Runner(provider3, registry3, sandbox3, transcript3, model="m")
    result3 = r3.run("s", "t")
    transcript3.close()

    # The notice should precede run_end
    events3 = _events(tmp)
    nudge_idx = next((i for i, e in enumerate(events3) if e["event"] == "nudge"), -1)
    run_end_idx = next((i for i, e in enumerate(events3) if e["event"] == "run_end"), -1)
    assert nudge_idx >= 0
    assert run_end_idx >= 0
    assert nudge_idx < run_end_idx

    # (d) rejection on turn 1, finish-time fails on turn 2
    transcript4 = Transcript(tmp / "t4.jsonl")
    registry4 = default_registry(transcript=transcript4)
    # First fingerprint succeeds (a*40), second fails (None)
    sandbox4 = FingerprintSandbox(wt, hashes=["a" * 40, "a" * 40, None])
    provider4 = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
        _resp(tool_calls=[_call("r1", "read_file", {"path": "f.txt"})]),
    ])
    r4 = Runner(provider4, registry4, sandbox4, transcript4, model="m", max_turns=2)
    result4 = r4.run("s", "t")
    transcript4.close()

    # finish on turn 1 is rejected (changed=False), turn 2 ends max_turns
    # finish-time measurement fails, so changed=None (not stale False)
    events4 = _events(tmp)
    run_end4 = next(e for e in events4 if e["event"] == "run_end")
    assert run_end4["changed"] is None
    assert "error: boom" in run_end4.get("changed_reason", "")

    # (e) BudgetExceeded as finish-time entry of max_turns run
    class WatchdogSandbox(FingerprintSandbox):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.finalized = False

        def finalize(self):
            # Return watchdog violation info
            return {"watchdog_violation": "other", "watchdog_violation_kind": "sandbox_error"}

    transcript5 = Transcript(tmp / "t5.jsonl")
    registry5 = default_registry(transcript=transcript5)
    # Finish-time fingerprint fails with BudgetExceeded
    sandbox5 = WatchdogSandbox(wt, hashes=["a" * 40, BudgetExceeded("disk")])
    provider5 = FakeProvider([
        _resp(tool_calls=[_call("r1", "read_file", {"path": "f.txt"})]),
        _resp(tool_calls=[_call("r2", "read_file", {"path": "f.txt"})]),
    ])
    r5 = Runner(provider5, registry5, sandbox5, transcript5, model="m", max_turns=2)
    result5 = r5.run("s", "t")
    transcript5.close()

    # status should still be max_turns
    assert result5.status == "max_turns"
    events5 = _events(tmp)
    run_end5 = next(e for e in events5 if e["event"] == "run_end")
    # changed should be None (measurement failed)
    assert run_end5["changed"] is None
    # changed_reason should start with budget:
    assert "budget: disk" in run_end5.get("changed_reason", "")
    # watchdog_violation should be "disk" (from fingerprint), not "other" from finalize
    assert run_end5["watchdog_violation"] == "disk"
    assert run_end5["watchdog_violation_kind"] == "budget"

    # (f) rejection on turn 1 then KeyboardInterrupt on turn 2
    class InterruptingSandbox2(FingerprintSandbox):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.turn_count = 0

        def bash(self, command, timeout=120):
            self.turn_count += 1
            if command == FINGERPRINT_SCRIPT:
                # Start fingerprint is a*40, turn 2's finish-time should raise
                if self.turn_count == 3:  # After start + finish-time for turn 1
                    raise KeyboardInterrupt
                return "exit code: 0\na" * 40 + "\n" + "0" * 40
            return super().bash(command, timeout)

    transcript6 = Transcript(tmp / "t6.jsonl")
    registry6 = default_registry(transcript=transcript6)
    sandbox6 = InterruptingSandbox2(wt, hashes=["a" * 40])
    provider6 = FakeProvider([
        _resp(tool_calls=[_call("f1", "finish", {"summary": "done"})]),
        _resp(tool_calls=[_call("r1", "read_file", {"path": "f.txt"})]),
    ])
    r6 = Runner(provider6, registry6, sandbox6, transcript6, model="m", max_turns=2)
    result6 = r6.run("s", "t")
    transcript6.close()

    assert result6.status == "interrupted"
    events6 = _events(tmp)
    run_end6 = next(e for e in events6 if e["event"] == "run_end")
    # changed should be False (from rejection on turn 1)
    assert run_end6["changed"] is False
    # No changed_reason key (interrupted doesn't add one)
    assert "changed_reason" not in run_end6

    # (g) count FINGERPRINT_SCRIPT commands for various statuses
    # interrupted: no finish-time fingerprint (no turn 2 in this case)
    transcript7 = Transcript(tmp / "t7.jsonl")
    registry7 = default_registry(transcript=transcript7)

    class InterruptingSandbox3(FingerprintSandbox):
        def bash(self, command, timeout=120):
            self.commands.append((command, timeout))
            if command == FINGERPRINT_SCRIPT:
                # Only start fingerprint, then interrupt on tool call
                return "exit code: 0\na" * 40 + "\n" + "0" * 40
            raise KeyboardInterrupt

    sandbox7 = InterruptingSandbox3(wt, hashes=["a" * 40])
    provider7 = FakeProvider([
        _resp(tool_calls=[_call("r1", "read_file", {"path": "f.txt"})]),
    ])
    r7 = Runner(provider7, registry7, sandbox7, transcript7, model="m")
    result7 = r7.run("s", "t")
    transcript7.close()

    assert result7.status == "interrupted"
    events7 = _events(tmp)
    fp_count = sum(1 for c, _ in sandbox7.commands if c == FINGERPRINT_SCRIPT)
    # Should only have start fingerprint, no finish-time
    assert fp_count == 1

    # timeout: Runner(timeout=0) with one read turn
    transcript8 = Transcript(tmp / "t8.jsonl")
    registry8 = default_registry(transcript=transcript8)
    sandbox8 = FingerprintSandbox(wt, hashes=["a" * 40])
    provider8 = FakeProvider([
        _resp(tool_calls=[_call("r1", "read_file", {"path": "f.txt"})]),
    ])
    r8 = Runner(provider8, registry8, sandbox8, transcript8, model="m", timeout=0)
    result8 = r8.run("s", "t")
    transcript8.close()

    assert result8.status == "timeout"
    events8 = _events(tmp)
    fp_count8 = sum(1 for c, _ in sandbox8.commands if c == FINGERPRINT_SCRIPT)
    # timeout has no finish-time fingerprint
    assert fp_count8 == 1

    # budget_exceeded from TOOL call via sandbox bash
    class BudgetBustingSandbox(FingerprintSandbox):
        def write_file(self, path, content):
            raise BudgetExceeded("worktree exceeds 2048 MB")

    transcript9 = Transcript(tmp / "t9.jsonl")
    registry9 = default_registry(transcript=transcript9)
    sandbox9 = BudgetBustingSandbox(wt, hashes=["a" * 40])
    provider9 = FakeProvider([
        _resp(tool_calls=[_call("w1", "write_file", {"path": "x", "content": "y"})]),
    ])
    r9 = Runner(provider9, registry9, sandbox9, transcript9, model="m")
    result9 = r9.run("s", "t")
    transcript9.close()

    assert result9.status == "budget_exceeded"
    events9 = _events(tmp)
    fp_count9 = sum(1 for c, _ in sandbox9.commands if c == FINGERPRINT_SCRIPT)
    # budget_exceeded has no finish-time fingerprint
    assert fp_count9 == 1

    # sandbox_error from TOOL call
    class SandboxErrorSandbox(FingerprintSandbox):
        def write_file(self, path, content):
            raise SandboxError("container gone")

    transcript10 = Transcript(tmp / "t10.jsonl")
    registry10 = default_registry(transcript=transcript10)
    sandbox10 = SandboxErrorSandbox(wt, hashes=["a" * 40])
    provider10 = FakeProvider([
        _resp(tool_calls=[_call("w1", "write_file", {"path": "x", "content": "y"})]),
    ])
    r10 = Runner(provider10, registry10, sandbox10, transcript10, model="m")
    result10 = r10.run("s", "t")
    transcript10.close()

    assert result10.status == "sandbox_error"
    events10 = _events(tmp)
    fp_count10 = sum(1 for c, _ in sandbox10.commands if c == FINGERPRINT_SCRIPT)
    # sandbox_error has no finish-time fingerprint
    assert fp_count10 == 1
