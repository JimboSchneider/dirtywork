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
from .tools import is_timeout_result

MAX_ASSISTANT_TEXT_CHARS = 64_000
# Spec §2: end-of-run evidence caps. These match the transcript's own preview
# caps on purpose — the values are taken from the very same variables the
# transcript records, so a payload and a transcript can never disagree.
LAST_ARGS_CHARS = 500
LAST_RESULT_CHARS = 2000
LAST_TEXT_CHARS = 2000

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
# Spec §1: independent of the stall detector. The stall detector never fires on
# edit -> test -> edit -> test (every edit_file counts as progress), so a worker
# grinding on a check it cannot pass burns every remaining turn. This ends the
# run instead. No nudge: the point is to stop paying for turns.
DEFAULT_STUCK_REPEATS = 4
STUCK_OUTPUT_CHARS = 4000
# Spec §4: the operator's own gate, run inside the sandbox on the completion
# path. `verify_rounds` is how many FIX ROUNDS follow a failed verify — the
# command may run verify_rounds + 1 times. The default 1 hands the first failure
# back to the worker once; 0 verifies once and ends the run either way.
DEFAULT_VERIFY_ROUNDS = 1
DEFAULT_VERIFY_TIMEOUT = 600
VERIFY_OUTPUT_CHARS = 4000
VERIFY_FEEDBACK = (
    "VERIFY FAILED (round {round} of {rounds}). The verification command\n"
    "  {command}\n"
    "exited with code {exit_code}. Output tail:\n"
    "{output}\n"
    "Fix the problem, then call finish(summary=...) again."
)
STALL_NUDGE = ("No progress in the last {n} turns: no file changed and no command produced "
               "new output. If the task is complete, commit (if asked) and call "
               "finish(summary=...); otherwise change your approach.")
# Spec §4.3: a timed-out command is not a model mistake -- it never reaches
# FailureTracker and it RESETS the consecutive-failure streak like any other
# non-failing execution. This exists only so the model is told, in words, that
# the result it just got is not a result.
TIMEOUT_NUDGE = ("A command timed out and did not finish; its result is unknown. Re-run it "
                 "with a larger timeout (up to 600 seconds) or split it into smaller "
                 "commands. Do not report it as passed.")
_MUTATING_TOOLS = ("write_file", "edit_file", "apply_edits", "insert_before", "insert_after")
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


def parse_exit_code(result):
    """The integer after 'exit code: ' on a bash result's first line, or None
    for an ERROR:/BLOCKED: result that never produced an exit status at all.
    RepeatTracker.note_bash calls this to tell a passing rerun from a failing
    one."""
    if not isinstance(result, str):
        return None
    head = result.split("\n", 1)[0]
    prefix = "exit code: "
    if not head.startswith(prefix):
        return None
    try:
        return int(head[len(prefix):].strip())
    except ValueError:
        return None


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


class RepeatTracker:
    """Spec §1.1: the same FAILING bash call, N times in a row.

    Fed only bash calls, from the same place ProgressTracker.note_call is fed.
    A non-bash call neither counts nor resets: edit -> test -> edit -> test
    with an unchanged failure is exactly the loop this catches. Identity is the
    EXISTING _bash_fingerprint (command + volatile-token-stripped output), so a
    timing-only difference is not a different result — but a changed test
    count, a new line, or a changed exit status is. A passing result (first
    line exactly 'exit code: 0') of the SAME command resets the streak to zero
    -- so a diligent worker re-running a green typecheck after every edit is
    never 'stuck' -- while passing runs of other commands neither count nor
    reset. limit <= 0 disables the tracker entirely."""

    def __init__(self, limit: int):
        self.limit = limit
        self.repeats = 0
        self.command = None
        self.output = None
        self._fingerprint = None

    def reset(self) -> None:
        """Start a new episode: the same field resets a passing rerun of the
        current command applies, also used when a verify feedback round
        begins (spec §4.2's retry is a fresh start, not a continuation of
        whatever the worker was stuck on before calling finish/answering)."""
        self.repeats = 0
        self._fingerprint = None
        self.command = None
        self.output = None

    def note_bash(self, command, result):
        if self.limit <= 0:
            return None
        if parse_exit_code(result) == 0:
            # Only the SAME command going green ends the episode. A passing run
            # of some other command (git status, cat, ls ...) neither counts nor
            # resets -- exactly like a non-bash tool call in between -- so the
            # reads a model interleaves with its edit->test loop cannot hide an
            # unchanged failure.
            if command == self.command:
                self.reset()
            return None
        text = result if isinstance(result, str) else ""
        fingerprint = _bash_fingerprint(command, text)
        if fingerprint == self._fingerprint:
            self.repeats += 1
        else:
            self._fingerprint = fingerprint
            self.repeats = 1
        self.command = command
        self.output = text
        return "stuck" if self.repeats >= self.limit else None

    def stuck_on(self) -> dict:
        return {"command": self.command,
                "output": (self.output or "")[:STUCK_OUTPUT_CHARS],
                "repeats": self.repeats}


def resolve_context_window(model: str, flag_value, env_value, provider=None) -> tuple:
    """Precedence: --context-window > DIRTYWORK_CONTEXT_WINDOW > what the SERVER
    reports it actually loaded the model with > the provider's own static table
    for this model > DEFAULT_WINDOW. Returns (tokens, source) with source in
    flag|env|provider:<name>:server|provider:<name>|default -- the two provider
    sources are deliberately distinct strings, so a run record says which one
    answered. Raises ValueError for an env value that is not a positive integer."""
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
        name = getattr(provider, "name", "provider")
        # Spec §3.1: loaded_context_window is OPTIONAL. Third-party providers and
        # every existing test double simply do not have it; one that raises (an
        # endpoint behaving unexpectedly, a transport bug) must never fail a run.
        # Both cases fall through to the static table, exactly as before 0.9.
        probe = getattr(provider, "loaded_context_window", None)
        if probe is not None:
            try:
                loaded = probe(model)
            except Exception:
                loaded = None
            if isinstance(loaded, int) and not isinstance(loaded, bool) and loaded > 0:
                return loaded, f"provider:{name}:server"
        window = provider.context_window(model)
        if window:
            return int(window), f"provider:{name}"
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


def trim_messages(messages: list, char_budget: int) -> tuple:
    """Replace oldest tool results with TRIM_MARKER until under budget.

    Returns (fits, newly_trimmed) (spec §2.2). `newly_trimmed` counts only the
    results replaced ON THIS CALL -- a result already holding the marker is
    never counted twice -- which is what lets the runner report `trimmed_turns`
    as "turns on which trimming happened" instead of "markers in the history".
    Trimming is destructive and cumulative, so a running total of markers would
    say the same thing on every later turn and mean nothing."""
    newly_trimmed = 0
    for m in messages:
        if _total_chars(messages) <= char_budget:
            return True, newly_trimmed
        if m.get("role") == "tool" and m.get("content") != TRIM_MARKER:
            m["content"] = TRIM_MARKER
            newly_trimmed += 1
    return _total_chars(messages) <= char_budget, newly_trimmed


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
                 context_window: int | None = None,
                 context_window_source: str | None = None,
                 stuck_repeats: int = DEFAULT_STUCK_REPEATS,
                 verify: str | None = None,
                 verify_rounds: int = DEFAULT_VERIFY_ROUNDS,
                 verify_timeout: int = DEFAULT_VERIFY_TIMEOUT):
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
        # Spec §3.4: recorded, never used for a decision. The runner already has
        # the NUMBER; this only says where it came from, so a run record can be
        # read without guessing whether anybody chose it. None for a Runner
        # built directly (tests, embedders) that never resolved a source.
        self.context_window_source = context_window_source
        self.stuck_repeats = stuck_repeats
        self.verify = verify
        self.verify_rounds = verify_rounds
        # Clamped to the bash tool's own range so --verify can never ask the
        # sandbox for a timeout the bash path would refuse.
        self.verify_timeout = max(1, min(int(verify_timeout), 600))
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
                              context_window_source=self.context_window_source,
                              schema_version=2, **(self.run_info or {}))
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        turns = 0
        trimmed_turns = 0       # spec §2.2: turns on which trimming happened
        timeouts = 0            # spec §4.3: worker bash calls that timed out
        failures = FailureTracker()
        progress = ProgressTracker(self.stall_turns)
        repeats = RepeatTracker(self.stuck_repeats)
        stuck = None            # spec §1.2: set once, read by finish() below
        last_tool_result = None     # spec §2: the newest non-finish tool call
        last_assistant_text = None  # spec §2: the newest non-empty reply text
        verify_state = None         # spec §4.3: the LAST verify run, or None
        verify_rounds_used = 0
        start = time.monotonic()
        deadline = start + self.timeout

        def note_last_tool_result(tool: str, args: str, result) -> None:
            # spec §2: tracks the SAME values just written to the "tool_result"
            # transcript event, for both a real tool call and a malformed entry
            # (tool="", args="") — one assignment, so a payload and the
            # transcript can never disagree about what ran last.
            nonlocal last_tool_result
            last_tool_result = {
                "tool": tool,
                "args": args[:LAST_ARGS_CHARS],
                "result": result[:LAST_RESULT_CHARS] if isinstance(result, str) else "",
            }

        def finish(status: str, final: str) -> RunResult:
            # This evidence rides on EVERY result (null when there is none), so
            # a consumer never has to branch on status to read the fields. A
            # `max_turns` run with final_message "" is the case that made this
            # necessary: without it there was nothing left to triage from.
            extra: dict = {"stuck_on": stuck,
                           "last_tool_result": last_tool_result,
                           "last_assistant_text": last_assistant_text,
                           "verify": verify_state,
                           "trimmed_turns": trimmed_turns,
                           "timeouts": timeouts,
                           "context_window_source": self.context_window_source}
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

        def run_verify():
            """One execution of the operator's gate (spec §4.2). Runs through
            the same sandbox.bash the tool uses — same guardrails, same budget
            watchdog, same reaper, same environment the worker's bash had — and
            happens BEFORE finalize(), so in docker mode the container is still
            alive and nothing has been exported yet. Returns the feedback text
            for another round, or None when the run may end now (verify_state
            says whether it passed)."""
            nonlocal verify_state, verify_rounds_used
            verify_rounds_used += 1
            result = self.sandbox.bash(self.verify, self.verify_timeout)
            exit_code = parse_exit_code(result)
            passed = exit_code == 0
            tail = result[-VERIFY_OUTPUT_CHARS:] if isinstance(result, str) else ""
            verify_state = {"command": self.verify, "exit_code": exit_code,
                            "output_tail": tail, "rounds": verify_rounds_used,
                            "passed": passed}
            self.transcript.write("verify", round=verify_rounds_used,
                                  exit_code=exit_code, passed=passed)
            if passed or verify_rounds_used > self.verify_rounds:
                return None
            return VERIFY_FEEDBACK.format(round=verify_rounds_used,
                                          rounds=self.verify_rounds + 1,
                                          command=self.verify,
                                          exit_code=exit_code, output=tail)

        def check_verify(final: str):
            """(RunResult to return, or None; feedback to append, or None) for a
            completion path. Both completion paths — the finish tool and a plain
            answer — go through this one function, so they can never disagree
            about what verifying means. BudgetExceeded/SandboxError end the run
            with the same statuses a tool call would."""
            nonlocal stuck
            if not self.verify:
                return finish("completed", final), None
            try:
                feedback = run_verify()
            except BudgetExceeded as e:
                return finish("budget_exceeded", e.reason), None
            except SandboxError as e:
                return finish("sandbox_error", str(e)), None
            if feedback is not None:
                # A feedback round is a fresh episode: the worker is retrying
                # against new instructions, so whatever bash streak was latched
                # (possibly in THIS SAME turn, before it called finish) must not
                # end the next turn as "stuck" for a check that no longer
                # reflects what the worker is doing.
                stuck = None
                repeats.reset()
                return None, feedback
            if verify_state["passed"]:
                return finish("completed", final), None
            return finish("verify_failed", final), None

        try:
            while True:
                if turns >= self.max_turns:
                    return finish("max_turns", "")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return finish("timeout", "")
                fits, newly_trimmed = trim_messages(messages, self.char_budget)
                if newly_trimmed > 0:
                    # Counted BEFORE the fits check, so the final call that
                    # trimmed something and still could not fit counts too
                    # (spec §2.2).
                    trimmed_turns += 1
                if not fits:
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
                    # escapes to __main__._fail_run instead. The request was
                    # made and answered, so it counts as a turn (as it did when
                    # the runner parsed bodies itself).
                    turns += 1
                    return finish("model_error", str(e))
                turns += 1
                finish_reason = resp.finish_reason
                # The adapter already sanitized usage (finite, non-negative).
                for k in usage:
                    usage[k] += resp.usage.get(k, 0)
                # An entry the provider could not address (no id) cannot be
                # answered with a tool result: that is a malformed *entry*. One
                # with an id but undecodable arguments is answerable.
                malformed_entries = [tc for tc in resp.tool_calls
                                     if tc.error is not None and not tc.id]
                malformed_count = len(malformed_entries)
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
                if isinstance(transcript_text, str) and transcript_text.strip():
                    last_assistant_text = transcript_text[:LAST_TEXT_CHARS]
                if resp.tool_calls:
                    # The adapter re-serializes these into whatever wire shape
                    # its protocol needs; the runner keeps neutral objects.
                    messages.append(assistant_message(resp.text, tool_calls))
                else:
                    content = resp.text
                    kind = classify_text_reply(content, finish_reason)
                    if kind == "answer":
                        messages.append(assistant_message(content, None))
                        ended, feedback = check_verify(content)
                        if ended is not None:
                            return ended
                        messages.append({"role": "user", "content": feedback})
                        continue
                    messages.append(assistant_message(content, None))
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
                for entry in malformed_entries:
                    reason = failures.record("malformed_entry")
                    if abort_reason is None:
                        abort_reason = reason
                    # The adapter knows the wire shape it failed to parse; its
                    # error text is what the transcript records.
                    result = f"ERROR: {entry.error}"
                    self.transcript.write("tool_result", tool="", args="", result=result)
                    note_last_tool_result("", "", result)
                if abort_reason is not None:
                    return finish("model_error", abort_reason)

                pending_finish = None
                timed_out_this_turn = False   # spec §4.3: at most ONE nudge per turn
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
                    timed_out_fields = {}
                    if name == "bash":
                        command = args.get("command") if isinstance(args, dict) else None
                        if repeats.note_bash(command, result) == "stuck":
                            stuck = repeats.stuck_on()
                        if is_timeout_result(result):
                            # Spec §4.3: worker bash TOOL CALLS only. The
                            # --verify command goes through sandbox.bash
                            # directly, is never a tool call and is never
                            # transcribed here, so it can never reach this.
                            timeouts += 1
                            timed_out_this_turn = True
                            timed_out_fields["timed_out"] = True
                    self.transcript.write("tool_result", tool=name,
                                          args=raw_args[:500],
                                          result=self.registry.transcript_preview(name, result),
                                          **timed_out_fields)
                    if name != FINISH_TOOL:
                        note_last_tool_result(name, raw_args, result)
                    messages.append(tool_message(tc.id, result))
                    if abort_reason is not None:
                        return finish("model_error", abort_reason)

                if pending_finish is not None:
                    ended, feedback = check_verify(pending_finish)
                    if ended is not None:
                        return ended
                    messages.append({"role": "user", "content": feedback})
                    continue

                # Same rule as `finish` in a mixed turn: the turn's remaining
                # tool calls have already run. `finish` still wins — a worker
                # that declared itself done did so with full knowledge of the
                # failure it had just seen.
                if stuck is not None:
                    return finish("stuck",
                                  f"the same failing command ran {stuck['repeats']} "
                                  f"times in a row")

                stalled, stall_text = check_progress()
                if stalled is not None:
                    return stalled

                malformed_text = None
                if malformed_count > 0:
                    malformed_text = (f"{malformed_count} of your tool calls were malformed "
                                      "(unaddressable: no usable id/name) and were "
                                      "discarded. Re-issue them as valid tool calls.")
                timeout_text = None
                if timed_out_this_turn:
                    # Emitted HERE, past the finish/stuck/abort exits above, so a
                    # turn that ENDS the run never writes a nudge -- the same
                    # place and the same reason as the stall nudge. Once per
                    # turn, however many commands timed out in it.
                    self.transcript.write("nudge", kind="timeout", turn=turns)
                    timeout_text = TIMEOUT_NUDGE
                nudge_text = _join_nudges(malformed_text, timeout_text, stall_text)
                if nudge_text:
                    messages.append({"role": "user", "content": nudge_text})
        except KeyboardInterrupt:
            return finish("interrupted", "")
