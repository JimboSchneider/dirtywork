from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from .budget import BudgetExceeded
from .llm import LLMTimeout
from .sandbox import SandboxError

MAX_ASSISTANT_TEXT_CHARS = 64_000

# The terminal tool's NAME. The runner branches on ToolSpec.terminal, not on
# this constant; it is kept because the system prompt, the docs and the bench
# scoreboard all refer to the tool by name.
FINISH_TOOL = "finish"

CONTEXT_WINDOWS = {
    "qwen/qwen3-coder-next": 65536,
    "mistralai/devstral-small-2-2512": 32768,
}
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


def resolve_context_window(model: str, flag_value, env_value) -> tuple[int, str]:
    """Spec §4 precedence: --context-window > DIRTYWORK_CONTEXT_WINDOW > table > default.
    Returns (tokens, source) with source in flag|env|table|default. Raises
    ValueError for an env value that is not a positive integer."""
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
    if model in CONTEXT_WINDOWS:
        return CONTEXT_WINDOWS[model], "table"
    return DEFAULT_WINDOW, "default"


def _valid_tool_call(tc) -> bool:
    """Structurally valid OpenAI tool call: non-empty string id, function object
    with non-empty string name, arguments absent/None or a string."""
    if not isinstance(tc, dict):
        return False
    if not isinstance(tc.get("id"), str) or not tc["id"]:
        return False
    fn = tc.get("function")
    if not isinstance(fn, dict):
        return False
    if not isinstance(fn.get("name"), str) or not fn["name"]:
        return False
    args = fn.get("arguments")
    return args is None or isinstance(args, str)


def _canonical_tool_call(tc: dict) -> dict:
    """Rebuild an accepted call in canonical OpenAI wire shape."""
    fn = tc["function"]
    return {"id": tc["id"], "type": "function",
            "function": {"name": fn["name"], "arguments": fn.get("arguments") or "{}"}}


def _total_chars(messages: list) -> int:
    total = 0
    for m in messages:
        total += len(m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            total += len((tc.get("function") or {}).get("arguments") or "")
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
    def __init__(self, client, registry, sandbox, transcript, model,
                 max_turns: int = 40, timeout: int = 1800,
                 temperature: float | None = None,
                 run_info: dict | None = None,
                 finalize: Callable[[], dict] | None = None,
                 stall_turns: int = DEFAULT_STALL_TURNS,
                 context_window: int | None = None):
        self.client = client
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
        self.context_window = context_window if context_window is not None else CONTEXT_WINDOWS.get(model, DEFAULT_WINDOW)
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
                    resp = self.client.chat(self.model, messages, tools=self.registry.schemas(),
                                            temperature=self.temperature,
                                            timeout=max(1.0, remaining))
                except LLMTimeout:
                    if time.monotonic() >= deadline - 0.5:
                        return finish("timeout", "")
                    else:
                        return finish("model_error",
                                      "model request timed out before the run deadline")
                turns += 1
                try:
                    msg = resp["choices"][0]["message"]
                    if not isinstance(msg, dict):
                        raise TypeError("message is not an object")
                except (KeyError, IndexError, TypeError):
                    return finish("model_error",
                                  "malformed response from server (no choices[0].message)")
                finish_reason = resp["choices"][0].get("finish_reason")
                resp_usage = resp.get("usage") or {}
                for k in usage:
                    # usage is server-controlled: NaN/Infinity would survive
                    # json.loads and later emit invalid JSON on our stdout/transcript
                    # contract. Accept only finite, non-negative numbers.
                    v = resp_usage.get(k, 0)
                    if isinstance(v, (int, float)) and not isinstance(v, bool) \
                            and math.isfinite(v) and v >= 0:
                        usage[k] += int(v)
                raw = msg.get("tool_calls") or []
                if not isinstance(raw, list):
                    raw = []
                tool_calls = [_canonical_tool_call(tc) for tc in raw if _valid_tool_call(tc)]
                malformed_count = len(raw) - len(tool_calls)
                transcript_text = msg.get("content")
                if isinstance(transcript_text, str) and len(transcript_text) > MAX_ASSISTANT_TEXT_CHARS:
                    transcript_text = (
                        transcript_text[:MAX_ASSISTANT_TEXT_CHARS]
                        + f"\n[truncated at {MAX_ASSISTANT_TEXT_CHARS} chars in the transcript "
                          f"only — the full text was sent to the model]"
                    )
                self.transcript.write(
                    "assistant", text=transcript_text,
                    tool_calls=[{"name": (tc.get("function") or {}).get("name"),
                                 "arguments": ((tc.get("function") or {}).get("arguments") or "")[:2000]}
                                for tc in tool_calls])
                if raw:
                    # Rebuild in canonical wire shape (id, type: "function", function
                    # with a string arguments) so the history we send back stays
                    # protocol-valid for strict OpenAI-compatible servers, on every
                    # path that resends tool calls -- not just when malformed.
                    clean_msg = {"role": "assistant", "content": msg.get("content") or ""}
                    if tool_calls:
                        clean_msg["tool_calls"] = tool_calls
                    messages.append(clean_msg)
                else:
                    content = msg.get("content") if isinstance(msg.get("content"), str) else ""
                    kind = classify_text_reply(msg.get("content"), finish_reason)
                    if kind == "answer":
                        messages.append(msg)
                        return finish("completed", content)
                    messages.append({"role": "assistant", "content": content})
                    self.transcript.write("nudge", kind=kind, turn=turns)
                    abort_reason = failures.record("empty_reply")
                    if abort_reason is not None:
                        return finish("model_error", abort_reason)
                    stalled, stall_text = check_progress()
                    if stalled is not None:
                        return stalled
                    messages.append({"role": "user", "content": _join_nudges(NUDGES[kind], stall_text)})
                    continue

                abort_reason = None
                for _ in range(malformed_count):
                    reason = failures.record("malformed_entry")
                    if abort_reason is None:
                        abort_reason = reason
                    result = "ERROR: malformed tool call entry (missing or invalid id/function fields)"
                    self.transcript.write("tool_result", tool="", args="", result=result)
                if abort_reason is not None:
                    return finish("model_error", abort_reason)

                pending_finish = None
                for tc in tool_calls:
                    fn_info = tc.get("function") or {}
                    name = fn_info.get("name") or ""
                    raw_args = fn_info.get("arguments") or "{}"
                    call_id = tc.get("id", "")
                    abort_reason = None
                    args = None
                    try:
                        args = json.loads(raw_args)
                        if not isinstance(args, dict):
                            raise ValueError("arguments must be a JSON object")
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
                                # A blocked command and a deadline refusal both
                                # reset the counter, exactly as ToolExecutor's
                                # non-raising return did.
                                failures.reset()
                    except BudgetExceeded as e:
                        return finish("budget_exceeded", e.reason)
                    except SandboxError as e:
                        return finish("sandbox_error", str(e))
                    except (json.JSONDecodeError, ValueError) as e:
                        abort_reason = failures.record("malformed_args")
                        if finish_reason == "length":
                            result = (
                                "ERROR: your reply was cut off at the token limit before "
                                "the tool call completed. Emit smaller tool calls — e.g. "
                                "write the file in pieces using multiple write_file/"
                                "edit_file calls."
                            )
                        else:
                            result = f"ERROR: malformed tool arguments: {e}"
                    progress.note_call(name, self.registry.canonical_args(name, args), result)
                    self.transcript.write("tool_result", tool=name,
                                          args=raw_args[:500],
                                          result=self.registry.transcript_preview(name, result))
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "content": result})
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
