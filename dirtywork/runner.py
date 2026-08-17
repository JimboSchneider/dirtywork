from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from .budget import BudgetExceeded
from .llm import LLMTimeout, MalformedResponse
from .providers import assistant_message, tool_message
from .sandbox import SandboxError

MAX_ASSISTANT_TEXT_CHARS = 64_000

# The terminal tool's NAME. The runner branches on ToolSpec.terminal, not on
# this constant; it is kept because the system prompt, the docs and the bench
# scoreboard all refer to the tool by name.
FINISH_TOOL = "finish"

# The per-model context-window table moved to the provider that serves those
# models (providers/openai_compat.py): resolve_context_window asks the provider.
DEFAULT_WINDOW = 32768
TRIM_MARKER = "[result trimmed — re-run the tool if needed]"
CHARS_PER_TOKEN = 4
BUDGET_FRACTION = 0.75
MAX_CONSECUTIVE_FAILURES = 3
FAILURE_KINDS = ("malformed_entry", "malformed_args", "unknown_tool", "bad_args", "empty_reply")
MAX_TOTAL_CONSECUTIVE_FAILURES = 6


class FailureTracker:
    """Consecutive model failures, counted per kind and in total. Any
    successful tool execution resets everything (spec §2)."""

    def __init__(self):
        self.counts = {kind: 0 for kind in FAILURE_KINDS}
        self.total = 0

    def record(self, kind: str) -> str | None:
        if kind not in self.counts:
            raise ValueError(f"unknown failure kind {kind!r}")
        self.counts[kind] += 1
        self.total += 1
        if self.counts[kind] >= MAX_CONSECUTIVE_FAILURES:
            return f"aborted after {MAX_CONSECUTIVE_FAILURES} consecutive {kind} failures"
        if self.total >= MAX_TOTAL_CONSECUTIVE_FAILURES:
            return f"aborted after {MAX_TOTAL_CONSECUTIVE_FAILURES} consecutive tool failures"
        return None

    def reset(self) -> None:
        for kind in self.counts:
            self.counts[kind] = 0
        self.total = 0


# These tags are built by concatenation ON PURPOSE: several local models' chat
# templates parse these exact tags in their own output (Qwen3-coder's tool-call
# XML, think-tag stripping), so a worker model editing this file through its
# tool channel could not emit them literally. Keep them concatenated.
_THINK_OPEN = "<" + "think>"
_THINK_CLOSE = "</" + "think>"
# Aliases for backwards compatibility with tests
_THINK = _THINK_OPEN
_THINK_END = _THINK_CLOSE
_THINK_RE = re.compile(re.escape(_THINK_OPEN) + r".*?(?:" + re.escape(_THINK_CLOSE) + r"|\Z)",
                       re.DOTALL)
_TEXT_TOOL_MARKERS = tuple("<" + m for m in ("tool_call>", "function=", "function_call>", "|tool_call|>"))

NUDGES = {
    "truncated": ("Your reply was cut off at the token limit. Continue with smaller steps — "
                  "emit one tool call at a time and write large files in pieces."),
    "empty": ("Your reply contained no tool call and no answer. Continue the task with a "
              "tool call, or call finish(summary=...) if the task is complete."),
    "text_tool_call": ("Your reply contained a tool call written as text; the harness only "
                       "executes tool calls made through the tools API. Re-issue it as a "
                       "real tool call."),
}


def _join_nudges(*parts) -> str:
    """One user message per turn: merge whichever nudge texts apply."""
    return "\n\n".join(p for p in parts if p)


def strip_think(text) -> str:
    """Drop every think block (an unterminated opening tag drops to the end)."""
    if not isinstance(text, str):
        return ""
    return _THINK_RE.sub("", text).strip()


def _looks_like_tool_json(text: str) -> bool:
    """A JSON object with a string "name" and an "arguments" key anywhere in
    the text — a tool call the model wrote as prose instead of calling."""
    if '"name"' not in text or '"arguments"' not in text:
        return False
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text[start:])
        except ValueError:
            obj = None
        if isinstance(obj, dict) and isinstance(obj.get("name"), str) and "arguments" in obj:
            return True
        start = text.find("{", start + 1)
    return False


def classify_text_reply(content, finish_reason) -> str:
    """Spec §1.2: what a reply with no tool calls means."""
    if finish_reason == "length":
        return "truncated"
    text = strip_think(content)
    if not text:
        return "empty"
    if any(marker in text for marker in _TEXT_TOOL_MARKERS) or _looks_like_tool_json(text):
        return "text_tool_call"
    return "answer"


DEFAULT_STALL_TURNS = 12
STALL_NUDGE = ("No progress in the last {n} turns: no file changed and no command produced "
               "new output. If the task is complete, commit (if asked) and call "
               "finish(summary=...); otherwise change your approach.")
_MUTATING_TOOLS = ("write_file", "edit_file")
# Tokens that change between otherwise-identical runs of the same command:
# durations ("in 24.51s", "0.39s", "12 ms"), clock times / ISO timestamps,
# and long hex ids (git shas, container ids — at least one a-f letter, so a
# plain 7+ digit number such as a byte count is NOT normalized). Only these
# are normalized away — a counter, a line number, or a test count that
# changes IS progress and must stay visible to the stall detector.
_VOLATILE_RE = re.compile(
    r"\d+(?:\.\d+)?\s?(?:ms|s|secs?|seconds?|min|mins?|minutes?|h|hours?)\b"
    r"|\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?"
    r"|\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"|\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,64}\b"
)


def _bash_fingerprint(command, result: str) -> str:
    """Identity of a bash call for progress purposes. The first result line
    ('exit code: N') is kept verbatim — a changed exit status is real news —
    but volatile tokens (durations, clock times, timestamps, long ids) are stripped from the rest,
    so re-running the same command whose output differs only in timing
    ('5 passed in 24.51s' vs '5 passed in 25.02s') is not progress.
    Any other change — a count, a counter, a line number, a new word — still is."""
    head, sep, body = result.partition("\n")
    normalized = head + sep + _VOLATILE_RE.sub("", body)
    return hashlib.sha256((str(command) + "\0" + normalized).encode("utf-8", "replace")).hexdigest()


class ProgressTracker:
    """Spec §3: a turn made progress if any tool call was new to this run
    (first time this exact tool + arguments — a file not read before, a new
    grep, a new command), a write/edit succeeded, or a bash call produced
    output not seen before (volatile tokens ignored). Only repeats are idle.
    Nudge once per idle streak at stall_turns // 2; report 'stalled' at
    stall_turns. stall_turns <= 0 disables detection."""

    def __init__(self, stall_turns: int):
        self.stall_turns = stall_turns
        self.idle_turns = 0
        self._progressed = False
        self._nudged = False
        self._seen_bash = set()
        self._seen_calls = set()

    def note_call(self, name: str, args, result: str) -> None:
        if not isinstance(result, str) or result.startswith("ERROR"):
            return
        if name in _MUTATING_TOOLS:
            self._progressed = True          # a successful write/edit is always progress
            return
        call_key = name + "\0" + (json.dumps(args, sort_keys=True, default=str) if isinstance(args, dict) else "")
        if call_key not in self._seen_calls:
            self._seen_calls.add(call_key)   # first time this exact call happened: new ground
            self._progressed = True
        if name == "bash":
            command = args.get("command") if isinstance(args, dict) else None
            key = _bash_fingerprint(command, result)
            if key not in self._seen_bash:
                self._seen_bash.add(key)
                self._progressed = True

    def end_turn(self) -> str | None:
        progressed, self._progressed = self._progressed, False
        if self.stall_turns <= 0:
            return None
        if progressed:
            self.idle_turns = 0
            self._nudged = False
            return None
        self.idle_turns += 1
        if self.idle_turns >= self.stall_turns:
            return "stalled"
        if self.stall_turns >= 2 and self.idle_turns == self.stall_turns // 2 and not self._nudged:
            self._nudged = True
            return "nudge"
        return None


def resolve_context_window(model: str, flag_value, env_value, provider=None) -> tuple:
    """Precedence: --context-window > DIRTYWORK_CONTEXT_WINDOW > the provider's
    own table for this model > DEFAULT_WINDOW. Returns (tokens, source) with
    source in flag|env|provider:<name>|default. Raises ValueError for an env
    value that is not a positive integer."""
    if flag_value is not None:
        return int(flag_value), "flag"
    if env_value not in (None, ""):
        try:
            value = int(env_value)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            raise ValueError(
                f"DIRTYWORK_CONTEXT_WINDOW must be a positive integer, got {env_value!r}")
        return value, "env"
    if provider is not None:
        window = provider.context_window(model)
        if window:
            return int(window), f"provider:{getattr(provider, 'name', 'provider')}"
    return DEFAULT_WINDOW, "default"


def _tool_call_arg_chars(tc) -> int:
    if tc.raw_arguments:
        return len(tc.raw_arguments)
    if tc.arguments is None:
        return 0
    try:
        return len(json.dumps(tc.arguments))
    except (TypeError, ValueError):
        return 0


def _total_chars(messages: list) -> int:
    total = 0
    for m in messages:
        total += len(m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            total += _tool_call_arg_chars(tc)
    return total


def trim_messages(messages: list, char_budget: int) -> bool:
    """Replace oldest tool results with TRIM_MARKER until under budget."""
    for m in messages:
        if _total_chars(messages) <= char_budget:
            return True
        if m.get("role") == "tool" and m.get("content") != TRIM_MARKER:
            m["content"] = TRIM_MARKER
    return _total_chars(messages) <= char_budget


@dataclass
class RunResult:
    status: str
    turns: int
    final_message: str
    usage: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


class Runner:
    def __init__(self, provider, registry, sandbox, transcript, model,
                 max_turns: int = 40, timeout: int = 1800,
                 temperature: float | None = None,
                 run_info: dict | None = None,
                 finalize: Callable[[], dict] | None = None,
                 stall_turns: int = DEFAULT_STALL_TURNS,
                 context_window: int | None = None):
        self.provider = provider
        self.registry = registry
        self.sandbox = sandbox
        self.transcript = transcript
        self.model = model
        self.max_turns = max_turns
        self.timeout = timeout
        self.temperature = temperature
        self.run_info = run_info
        self.finalize = finalize
        self.stall_turns = stall_turns
        # An explicit 0 is honoured (it is how a test forces context_exhausted);
        # only None means "ask the provider".
        self.context_window = (context_window if context_window is not None
                               else (provider.context_window(model) or DEFAULT_WINDOW))
        self.char_budget = int(self.context_window * BUDGET_FRACTION * CHARS_PER_TOKEN)

    def run(self, system_prompt: str, task: str) -> RunResult:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]
        self.transcript.write("run_start", task=task, model=self.model,
                              max_turns=self.max_turns, timeout=self.timeout,
                              context_window=self.context_window,
                              schema_version=2, **(self.run_info or {}))
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        turns = 0
        failures = FailureTracker()
        progress = ProgressTracker(self.stall_turns)
        start = time.monotonic()
        deadline = start + self.timeout

        def finish(status: str, final: str) -> RunResult:
            extra: dict = {}
            finalize_error = None
            if self.finalize is not None:
                try:
                    finalize_result = self.finalize()
                    if isinstance(finalize_result, dict):
                        extra.update(finalize_result)
                except Exception as e:
                    finalize_error = f"{type(e).__name__}: {e}"
            if finalize_error is not None:
                extra["finalize_error"] = finalize_error
            self.transcript.write("run_end", status=status, turns=turns,
                                  duration_s=round(time.monotonic() - start, 1),
                                  usage=usage, **extra)
            return RunResult(status, turns, final, usage, extra=extra)

        def check_progress():
            """(RunResult to end the run with, or None; stall-nudge text to
            deliver, or None). The caller merges the nudge text into the one
            user message it is about to append — history must never carry
            two consecutive user messages (strict chat templates reject
            non-alternating roles)."""
            verdict = progress.end_turn()
            if verdict == "stalled":
                return finish("stalled", f"no progress in {self.stall_turns} consecutive turns"), None
            if verdict == "nudge":
                self.transcript.write("nudge", kind="stall", turn=turns)
                return None, STALL_NUDGE.format(n=self.stall_turns // 2)
            return None, None

        try:
            while True:
                if turns >= self.max_turns:
                    return finish("max_turns", "")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return finish("timeout", "")
                if not trim_messages(messages, self.char_budget):
                    return finish("context_exhausted", "")

                try:
                    resp = self.provider.chat(self.model, messages, self.registry.schemas(),
                                              temperature=self.temperature,
                                              timeout=max(1.0, remaining))
                except LLMTimeout:
                    if time.monotonic() >= deadline - 0.5:
                        return finish("timeout", "")
                    else:
                        return finish("model_error",
                                      "model request timed out before the run deadline")
                except MalformedResponse as e:
                    # A body we cannot read is a model failure, not a transport
                    # failure: end through finish() so finalize() runs and a
                    # run_end event is written. A plain LLMError deliberately
                    # escapes to __main__._fail_run instead.
                    return finish("model_error", str(e))
                turns += 1
                finish_reason = resp.finish_reason
                # The adapter already sanitized usage (finite, non-negative).
                for k in usage:
                    usage[k] += resp.usage.get(k, 0)
                # An entry the provider could not address (no id) cannot be
                # answered with a tool result: that is a malformed *entry*. One
                # with an id but undecodable arguments is answerable.
                malformed_count = sum(1 for tc in resp.tool_calls
                                      if tc.error is not None and not tc.id)
                tool_calls = [tc for tc in resp.tool_calls if tc.id]
                transcript_text = resp.text
                if isinstance(transcript_text, str) and len(transcript_text) > MAX_ASSISTANT_TEXT_CHARS:
                    transcript_text = (
                        transcript_text[:MAX_ASSISTANT_TEXT_CHARS]
                        + f"\n[truncated at {MAX_ASSISTANT_TEXT_CHARS} chars in the transcript "
                          f"only — the full text was sent to the model]"
                    )
                self.transcript.write(
                    "assistant", text=transcript_text,
                    tool_calls=[{"name": tc.name, "arguments": (tc.raw_arguments or "")[:2000]}
                                for tc in tool_calls])

                abort_reason = None
                for _ in range(malformed_count):
                    reason = failures.record("malformed_entry")
                    if abort_reason is None:
                        abort_reason = reason
                    result = "ERROR: malformed tool call entry (missing or invalid id/function fields)"
                    self.transcript.write("tool_result", tool="", args="", result=result)
                if abort_reason is not None:
                    return finish("model_error", abort_reason)

                # Append assistant message to history
                if resp.tool_calls:
                    # The adapter re-serializes these into whatever wire shape
                    # its protocol needs; the runner keeps neutral objects.
                    messages.append(assistant_message(resp.text, tool_calls))
                else:
                    content = resp.text
                    kind = classify_text_reply(content, finish_reason)
                    if kind == "answer":
                        messages.append(assistant_message(content, None))
                        return finish("completed", content)
                    messages.append(assistant_message(content, None))

                pending_finish = None
                for tc in tool_calls:
                    name = tc.name
                    raw_args = tc.raw_arguments or "{}"
                    args = tc.arguments
                    abort_reason = None
                    if tc.error is not None:
                        abort_reason = failures.record("malformed_args")
                        if finish_reason == "length":
                            result = (
                                "ERROR: your reply was cut off at the token limit before "
                                "the tool call completed. Emit smaller tool calls — e.g. "
                                "write the file in pieces using multiple write_file/"
                                "edit_file calls."
                            )
                        else:
                            result = f"ERROR: {tc.error}"
                    else:
                        try:
                            spec = self.registry.spec(name)
                            if spec is not None and spec.terminal:
                                summary = args.get("summary")
                                pending_finish = summary if isinstance(summary, str) else ""
                                result = "run finished"
                            else:
                                tool_result = self.registry.execute(
                                    name, args, sandbox=self.sandbox, deadline=deadline)
                                result = tool_result.text
                                if tool_result.failure is not None:
                                    abort_reason = failures.record(tool_result.failure)
                                else:
                                    failures.reset()
                        except BudgetExceeded as e:
                            return finish("budget_exceeded", e.reason)
                        except SandboxError as e:
                            return finish("sandbox_error", str(e))
                    progress.note_call(name, self.registry.canonical_args(name, args), result)
                    self.transcript.write("tool_result", tool=name,
                                          args=raw_args[:500],
                                          result=self.registry.transcript_preview(name, result))
                    messages.append(tool_message(tc.id, result))
                    if abort_reason is not None:
                        return finish("model_error", abort_reason)

                if pending_finish is not None:
                    return finish("completed", pending_finish)

                stalled, stall_text = check_progress()
                if stalled is not None:
                    return stalled

                malformed_text = None
                if malformed_count > 0:
                    malformed_text = (f"{malformed_count} of your tool calls were malformed "
                                      "(missing or invalid id/function fields) and were "
                                      "discarded. Re-issue them as valid tool calls.")
                nudge_text = _join_nudges(malformed_text, stall_text)
                if nudge_text:
                    messages.append({"role": "user", "content": nudge_text})
        except KeyboardInterrupt:
            return finish("interrupted", "")
