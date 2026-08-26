from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from .budget import BudgetExceeded
from .changes import (UNCHANGED_PLAIN, UNCHANGED_REQUIRED,
                       DEFAULT_NO_CHANGE_TURNS,
                       NO_CHANGE_SINCE_START_REQUIRED,
                       NO_CHANGE_SINCE_START_PLAIN,
                       NO_CHANGE_RECENT,
                       fingerprint as _fingerprint)
from .llm import LLMTimeout, MalformedResponse
from .providers import assistant_message, tool_message
from .sandbox import SandboxError
from .tools import is_timeout_result, net_change, parse_exit_code
from .toolspec import TOOL_CALL_MARKERS as _TEXT_TOOL_MARKERS

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

# Spec #60 §4. `finish` is executed by verifying, so its result is resolved
# when the turn's verify outcome is known. Until then the history message and
# the buffered tool_result record hold FINISH_PROVISIONAL -- which reaches disk
# only when an exception the runner does not handle leaves the turn (true, and
# the only case it is written). FINISH_DONE is the only value on a run that
# actually finished; every other exit resolves to "run not finished: <why>".
FINISH_DONE = "run finished"
FINISH_PROVISIONAL = "run not finished: verify did not run"

# The per-model context-window table moved to the provider that serves those
# models (providers/openai_compat.py): resolve_context_window asks the provider.
DEFAULT_WINDOW = 32768
# Spec §1.4: the per-reply output cap. Both adapters default to 4096 for direct
# callers; the runner now always passes this explicitly, so a large write_file
# has room to finish instead of being cut off mid-JSON.
DEFAULT_MAX_TOKENS = 8192
TRIM_MARKER = "[result trimmed — re-run the tool if needed]"
CHARS_PER_TOKEN = 4
MIN_CHUNK_CHARS = 200
MIN_CHUNK_LINES = 5
CHUNK_DIVISOR = 4
DEFAULT_LINE_CHARS = 60
MAX_TRUNCATED_REPLIES = 6
TRUNCATION_ABORT = ("aborted after {n} cut-off replies at --max-tokens {cap}: raise --max-tokens "
                    "or split the writes")
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


# Concatenated ON PURPOSE — see TOOL_CALL_MARKERS in toolspec.py
_THINK_OPEN = "<" + "think>"
_THINK_CLOSE = "</" + "think>"
# Aliases for backwards compatibility with tests
_THINK_RE = re.compile(re.escape(_THINK_OPEN) + r".*?(?:" + re.escape(_THINK_CLOSE) + r"|\Z)",
                       re.DOTALL)

NUDGES = {
    "truncated": ("Your reply was cut off at the --max-tokens cap of {cap} tokens (about "
                  "{cap_chars} characters); the harness received only {received} characters of "
                  "it — cut-off reply {n} of {max}. Keep each tool call's content under about "
                  "{target_chars} characters (about {target_lines} lines; split long lines if "
                  "you must) and emit one tool call at a time; for a large file, write_file the "
                  "first part and append_file the rest."),
    "empty": ("Your reply contained no tool call and no answer. Continue the task with a "
              "tool call, or call finish(summary=...) if the task is complete."),
    "text_tool_call": ("Your reply contained a tool call written as text; the harness only "
                       "executes tool calls made through the tools API. Re-issue it as a "
                       "real tool call."),
}

# Spec #60 §5 (R2): an assistant history entry with no addressable tool call
# and no non-whitespace text is stored with this content, so no chat template
# or server preprocessing (LM Studio drops empty assistant messages) can delete
# it and pull the following user message up against a tool result. The
# transcript keeps the model's real text ("") and records the substitution in
# `assistant.placeholder`. One constant: the wording can change without a
# schema change.
EMPTY_REPLY_PLACEHOLDER = "[empty reply]"


def _join_nudges(*parts) -> str:
    """One user message per turn: merge whichever nudge texts apply."""
    return "\n\n".join(p for p in parts if p)


# Spec §1.3. The fragment is the MODEL's own bytes and may be arbitrarily
# malformed, so it is scanned, never parsed: one bounded regex over the first
# TRUNCATED_ARGS_SCAN_CHARS characters, and the capture is unescaped through
# json.loads inside a try. Any failure degrades to the generic sentence.
_TRUNCATED_PATH_RE = re.compile(r'"path"\s*:\s*"((?:[^"\\]|\\.)*)"')
TRUNCATED_ARGS_SCAN_CHARS = 8192
TRUNCATED_PATH_CHARS = 200


def _recovered_path(raw_arguments):
    """The `path` a length-truncated tool call was building, or None.

    Returns at most TRUNCATED_PATH_CHARS characters: the value is
    model-controlled and goes straight into a tool result the model reads back,
    so a 40 KB "path" must not become a 40 KB error message. On Anthropic
    `raw_arguments` is "" (its error branches never set it) and this returns
    None, which is exactly the degradation spec §1.3 asks for."""
    if not isinstance(raw_arguments, str) or not raw_arguments:
        return None
    m = _TRUNCATED_PATH_RE.search(raw_arguments[:TRUNCATED_ARGS_SCAN_CHARS])
    if m is None:
        return None
    try:
        value = json.loads('"' + m.group(1) + '"')
    except ValueError:
        return None            # json.JSONDecodeError subclasses ValueError
    if not isinstance(value, str):
        return None
    return value[:TRUNCATED_PATH_CHARS]


def truncated_call_result(tool: str, raw_arguments, trunc: dict) -> str:
    """Spec §1.3: the tool result a call cut off at the token limit gets.

    A write_file whose path can be recovered is told exactly which write to
    redo and how; anything else gets the generic form. Both name append_file,
    because before 0.10 "write it in pieces" was not honest advice for a NEW
    large file -- no tool could add to one."""
    if tool == "write_file":
        path = _recovered_path(raw_arguments)
        if path is not None:
            return (f"ERROR: your write_file for {path!r} was cut off at the --max-tokens cap of "
                    f"{trunc['cap']} tokens after about {trunc['cut_chars']} characters "
                    f"(~{trunc['cut_lines']} lines) — nothing was written; cut-off reply "
                    f"{trunc['n']} of {trunc['max']}. Write the file in chunks of at most about "
                    f"{trunc['target_chars']} characters (about {trunc['target_lines']} lines): "
                    f"write_file with the first part, then append_file for each following part.")
    return (f"ERROR: your {tool} call was cut off at the --max-tokens cap of {trunc['cap']} tokens "
            f"after about {trunc['cut_chars']} characters (~{trunc['cut_lines']} lines) before it "
            f"completed — cut-off reply {trunc['n']} of {trunc['max']}. Keep each tool call under "
            f"about {trunc['target_chars']} characters (about {trunc['target_lines']} lines); for "
            f"a large file, write_file the first part and append_file the rest.")


def reply_size(resp) -> tuple[int, int]:
    """(text_chars, raw_chars): what the harness RECEIVED -- the reply's prose
    and the raw argument strings of every addressable tool call
    (`_tool_call_arg_chars`). Reported to the model as `received`; never the
    target's basis."""
    return (len(resp.text or ""),
            sum(_tool_call_arg_chars(tc) for tc in resp.tool_calls))


def call_size(tc) -> tuple[int, int]:
    """(chars, lines) of ONE tool call's raw arguments -- the call that was
    cut (cases a/b). Newlines inside JSON string arguments arrive escaped
    (`\\n`), so both forms are counted. (0, 0) when the adapter kept nothing
    (Anthropic's error branch); the text path has no call and passes (0, 0)."""
    raw = tc.raw_arguments or ""
    chars = _tool_call_arg_chars(tc)
    return chars, (raw.count("\\n") + raw.count("\n") + 1) if chars else 0


def chunk_target(max_tokens: int, cut_chars: int, cut_lines: int) -> tuple[int, int]:
    """(characters, lines) a single tool call's content must stay under.
    Basis: the cap's character capacity (max_tokens * CHARS_PER_TOKEN), or the
    smaller of that and what the CUT CALL actually got out when its raw
    arguments are present (cut_chars > 0) -- that call's own ratio reflects
    how densely THIS model tokenizes THIS content. A quarter of the basis
    leaves room for JSON escaping, the call's other fields and any prose
    around it. Lines come from the cut call's own characters-per-line when it
    had enough lines to measure, else DEFAULT_LINE_CHARS."""
    cap_chars = max_tokens * CHARS_PER_TOKEN
    basis = min(cap_chars, cut_chars) if cut_chars > 0 else cap_chars
    chars = max(MIN_CHUNK_CHARS, basis // CHUNK_DIVISOR)
    per_line = (cut_chars / cut_lines
                if cut_chars > 0 and cut_lines >= 3 else DEFAULT_LINE_CHARS)
    return chars, max(MIN_CHUNK_LINES, int(chars / per_line))


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
_MUTATING_TOOLS = ("write_file", "append_file", "edit_file", "apply_edits",
                   "insert_before", "insert_after")
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
    stall_turns. stall_turns <= 0 disables detection.

    Spec #66 §4.2: a write that changed nothing (+0 -0) is not progress;
    an unknown result shape still is (fail open)."""

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
            # Spec #66 §4.2: a write that changed nothing (`+0 -0`) is not
            # progress; an unknown result shape still is (fail open).
            if net_change(result) is not False:
                self._progressed = True
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
                 max_tokens: int = DEFAULT_MAX_TOKENS,
                 temperature: float | None = None,
                 run_info: dict | None = None,
                 finalize: Callable[[], dict] | None = None,
                 stall_turns: int = DEFAULT_STALL_TURNS,
                 context_window: int | None = None,
                 context_window_source: str | None = None,
                 stuck_repeats: int = DEFAULT_STUCK_REPEATS,
                 verify: str | None = None,
                 verify_rounds: int = DEFAULT_VERIFY_ROUNDS,
                 verify_timeout: int = DEFAULT_VERIFY_TIMEOUT,
                 require_changes: bool = False,
                 no_change_turns: int = DEFAULT_NO_CHANGE_TURNS):
        self.provider = provider
        self.registry = registry
        self.sandbox = sandbox
        self.transcript = transcript
        self.model = model
        self.max_turns = max_turns
        self.timeout = timeout
        # Spec §1.4: passed explicitly on every chat call. The adapters keep
        # their own 4096 defaults for direct callers.
        self.max_tokens = max_tokens
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
        # Spec #66 §4.3: a completion that changed nothing ends the run `unchanged`
        # on the second attempt (the CLI sets this for resume --feedback)
        self.require_changes = require_changes
        # Spec #66 §4.4: every K turns a fingerprint; equal to the last check's -> a nudge, never an abort; 0 disables. Not a CLI flag.
        self.no_change_turns = no_change_turns
        # An explicit 0 is honoured (it is how a test forces context_exhausted);
        # only None means "ask the provider".
        self.context_window = (context_window if context_window is not None
                               else (provider.context_window(model) or DEFAULT_WINDOW))
        # Spec §1.4: prompt and reply share ONE window, so the prompt budget is
        # what is left after the output cap. max(0, …) keeps a directly-built
        # Runner with a cap larger than its window from going negative;
        # preflight refuses that combination for a real run.
        self.char_budget = int(max(0, self.context_window - self.max_tokens)
                               * BUDGET_FRACTION * CHARS_PER_TOKEN)

    def _missing_required(self, name: str, args) -> bool:
        """Spec §1.3 case (b): True when `args` parsed but is missing a
        required parameter of tool `name`. An unknown tool and a non-dict
        `args` are False -- those have their own accounting (`unknown_tool`,
        `bad_args`) and are not truncations."""
        spec = self.registry.spec(name)
        if spec is None or not isinstance(args, dict):
            return False
        return any(p not in args for p in spec.required)

    def run(self, system_prompt: str, task: str) -> RunResult:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]
        self.transcript.write("run_start", task=task, model=self.model,
                              max_turns=self.max_turns, timeout=self.timeout,
                              max_tokens=self.max_tokens,
                              context_window=self.context_window,
                              context_window_source=self.context_window_source,
                              schema_version=2, **(self.run_info or {}))
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        turns = 0
        trimmed_turns = 0       # spec §2.2: turns on which trimming happened
        timeouts = 0            # spec §4.3: worker bash calls that timed out
        truncations = 0         # spec #65 §3.3: turns with a truncation nudge or truncated_call_result; never reset
        failures = FailureTracker()
        progress = ProgressTracker(self.stall_turns)
        repeats = RepeatTracker(self.stuck_repeats)
        stuck = None            # spec §1.2: set once, read by finish() below
        last_tool_result = None     # spec §2: the newest non-finish tool call
        last_assistant_text = None  # spec §2: the newest non-empty reply text
        verify_state = None         # spec §4.3: the LAST verify run, or None
        verify_rounds_used = 0
        # Spec #66 §4.1: the worktree fingerprint at run start (None = guard off)
        fp_start = None             # measurement before first turn
        fp_check = None             # §4.4: baseline of the every-K check (W3b)
        fp_turn = -1                # turn of the newest measurement
        fp_value = None
        changed = None              # §4.3 one rule: newest measurement != fp_start; null = unknown
        changed_reason = None       # why the newest measurement failed (sparse on run_end)
        unchanged_finishes = 0      # count of zero-change finishes
        start = time.monotonic()
        deadline = start + self.timeout

        # Spec #60 §3/§4: the current turn's tool messages, in call order, each
        # paired with its buffered transcript record so a follow-up or the
        # finish resolution rewrites BOTH (transcript == wire). Cleared at the
        # start of every turn; a carrier is only ever chosen from here.
        turn_tool_msgs = []     # [(history message, tool_result record)]
        turn_terminal = []      # the subset that were terminal (finish) calls

        def resolve_finish(text: str) -> None:
            """Spec #60 §4: rewrite EVERY terminal record and history message of
            the current turn to `text`."""
            # Written uncapped by design: the finish result is harness-authored
            # and bounded (FINISH_SPEC declares transcript="full"); registry.
            # transcript_preview is bypassed here.
            for msg, record in turn_terminal:
                msg["content"] = text
                record["result"] = text

        def deliver(text, nudge_records) -> None:
            """Spec #60 §3 (R1): the ONE place harness text enters history. On a
            turn with addressable tool calls it rides on the LAST tool result of
            this turn (wire = result + "\\n\\n" + text; the transcript record
            gets `follow_up`); otherwise it is the next user message, legal
            because the preceding assistant entry is counted and non-empty (R2).
            Every nudge record handed in is stamped with the carrier (`via`)."""
            if not text:
                return
            if turn_tool_msgs:
                msg, record = turn_tool_msgs[-1]
                msg["content"] = f"{msg['content']}\n\n{text}"
                record["follow_up"] = text
                via = "tool_result"
            else:
                messages.append({"role": "user", "content": text})
                via = "user"
            for record in nudge_records:
                if record is not None:
                    record["via"] = via

        def drain_sandbox():
            """(joined text, nudge records) for every notice the sandbox queued since the last drain; ("", []) when none or when the sandbox has no drain_notices (host mode, test doubles)."""
            drain = getattr(self.sandbox, "drain_notices", None)
            notices = drain() if drain is not None else []
            records = [self.transcript.write("nudge", kind=kind, turn=turns) for kind, _text in notices]
            return _join_nudges(*(text for _kind, text in notices)), records

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

        def append_assistant(text, tool_calls, finish_reason) -> None:
            """Spec #60 §5: the ONE place the model's turn enters history and
            the transcript. `tool_calls` are the addressable calls (id-bearing);
            with none of them and no non-whitespace text the history entry
            carries EMPTY_REPLY_PLACEHOLDER (R2) and the event says so."""
            nonlocal last_assistant_text
            transcript_text = text
            if isinstance(transcript_text, str) and len(transcript_text) > MAX_ASSISTANT_TEXT_CHARS:
                transcript_text = (
                    transcript_text[:MAX_ASSISTANT_TEXT_CHARS]
                    + f"\n[truncated at {MAX_ASSISTANT_TEXT_CHARS} chars in the transcript "
                      f"only — the full text was sent to the model]"
                )
            has_text = isinstance(text, str) and bool(text.strip())
            fields = {}
            if not tool_calls and not has_text:
                fields["placeholder"] = EMPTY_REPLY_PLACEHOLDER
            self.transcript.write(
                "assistant", text=transcript_text,
                tool_calls=[{"name": tc.name, "arguments": (tc.raw_arguments or "")[:2000]}
                            for tc in tool_calls],
                # Spec §1.5: an OPEN enum. Adapters do not guarantee a
                # string (Anthropic passes an unknown stop reason through
                # raw), so anything else is recorded as null rather than
                # emitted as some other JSON type.
                finish_reason=finish_reason if isinstance(finish_reason, str) else None,
                **fields)
            if has_text:
                last_assistant_text = transcript_text[:LAST_TEXT_CHARS]
            messages.append(assistant_message(fields.get("placeholder", text), tool_calls))

        # A run finishes once. finalize() (docker mode: the export -- git add,
        # patch, worktree sampling) is not idempotent, but a KeyboardInterrupt
        # landing inside it propagates out of finish() (BaseException; the
        # except below only catches Exception) into the turn's interrupt
        # handler, which calls finish("interrupted") again. This state makes
        # the second call skip the export it already started and say so, and
        # keeps run_end to a single record.
        finalize_state = {"attempted": False, "done": False, "result": None, "error": None}
        run_end_written = False

        def run_finalize() -> None:
            if finalize_state["attempted"]:
                if not finalize_state["done"]:
                    finalize_state["error"] = "KeyboardInterrupt: interrupted during finalize"
                    finalize_state["done"] = True
                return
            finalize_state["attempted"] = True
            try:
                finalize_state["result"] = self.finalize()
            except Exception as e:
                finalize_state["error"] = f"{type(e).__name__}: {e}"
            finalize_state["done"] = True

        def finish(status: str, final: str) -> RunResult:
            nonlocal run_end_written
            # Spec #66 §4.1 (4): take a final fingerprint at run_end for the
            # change guard (except for statuses where it's already been measured)
            if (status not in ("interrupted", "timeout", "budget_exceeded", "sandbox_error")
                    and fp_start is not None and fp_turn != turns):
                try:
                    take_fingerprint()              # §4.1 (4): run_end.changed for max_turns/stalled/stuck/model_error/verify_failed/context_exhausted; a failure sets changed None + reason
                except (BudgetExceeded, SandboxError):
                    pass                            # reason and changed=None already stored by take_fingerprint
            # Spec #60 §4(c): the single exit point resolves any terminal record
            # the verify path never reached (a later call raised, the failure
            # tracker aborted, Ctrl-C) -- then flushes the turn so its evidence
            # is on disk BEFORE finalize() (docker export) runs (§6.1).
            drain_sandbox()
            if any(record.get("result") == FINISH_PROVISIONAL for _, record in turn_terminal):
                resolve_finish(FINISH_DONE if status == "completed"
                               else f"run not finished: {status}")
            self.transcript.flush()
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
                           "truncations": truncations,
                           "context_window_source": self.context_window_source,
                           "changed": changed}
            if changed_reason is not None:
                extra["changed_reason"] = changed_reason
            if self.finalize is not None:
                run_finalize()
                if isinstance(finalize_state["result"], dict):
                    extra.update(finalize_state["result"])
                if finalize_state["error"] is not None:
                    extra["finalize_error"] = finalize_state["error"]
            # Spec #66: budget watchdog violation (only if not set by finalize)
            if changed_reason is not None and changed_reason.startswith("budget: ") \
                    and not extra.get("watchdog_violation"):
                extra["watchdog_violation"] = changed_reason[len("budget: "):]
                extra["watchdog_violation_kind"] = "budget"
            if not run_end_written:
                self.transcript.write("run_end", status=status, turns=turns,
                                      duration_s=round(time.monotonic() - start, 1),
                                      usage=usage, **extra)
                run_end_written = True
            return RunResult(status, turns, final, usage, extra=extra)

        def check_progress():
            """(RunResult to end the run with, or None; stall-nudge text, or
            None; the buffered stall-nudge record, or None). The caller hands
            the text and record to deliver(), which picks the carrier (spec #60
            §3) -- history never carries a harness message after a tool result
            nor two consecutive user messages."""
            verdict = progress.end_turn()
            if verdict == "stalled":
                return finish("stalled", f"no progress in {self.stall_turns} consecutive turns"), None, None
            if verdict == "nudge":
                record = self.transcript.write("nudge", kind="stall", turn=turns)
                return None, STALL_NUDGE.format(n=self.stall_turns // 2), record
            return None, None, None

        def check_no_change():
            """(RunResult to end the run with, or None; nudge text, or None;
            the nudge record, or None) -- spec #66 §4.4. Fires on turns that
            are a multiple of no_change_turns when the guard is on. Equal to
            the last check's fingerprint -> nudge and reset the baseline;
            different -> reset the baseline silently; unmeasurable -> keep
            the baseline (take_fingerprint stored the reason)."""
            nonlocal fp_check
            if (fp_start is None or self.no_change_turns <= 0
                    or turns % self.no_change_turns != 0):
                return None, None, None
            try:
                fp = take_fingerprint()
            except BudgetExceeded as e:
                return finish("budget_exceeded", e.reason), None, None
            except SandboxError as e:
                return finish("sandbox_error", str(e)), None, None
            if fp is None:
                return None, None, None
            same = fp == fp_check
            fp_check = fp
            if not same:
                return None, None, None
            record = self.transcript.write("nudge", kind="no_change", turn=turns)
            if fp == fp_start:
                text = (NO_CHANGE_SINCE_START_REQUIRED if self.require_changes
                        else NO_CHANGE_SINCE_START_PLAIN)
            else:
                text = NO_CHANGE_RECENT
            return None, text.format(k=self.no_change_turns), record

        def run_verify():
            """One execution of the operator's gate (spec §4.2). Runs through
            the same sandbox.bash the tool uses — same guardrails, same budget
            watchdog, same reaper, same environment the worker's bash had — and
            happens BEFORE finalize(), so in docker mode the container is still
            alive and nothing has been exported yet. Returns (feedback text for
            another round or None when the run may end now, the buffered
            `verify` record) -- verify_state says whether it passed."""
            nonlocal verify_state, verify_rounds_used
            verify_rounds_used += 1
            result = self.sandbox.bash(self.verify, self.verify_timeout)
            exit_code = parse_exit_code(result)
            passed = exit_code == 0
            tail = result[-VERIFY_OUTPUT_CHARS:] if isinstance(result, str) else ""
            verify_state = {"command": self.verify, "exit_code": exit_code,
                            "output_tail": tail, "rounds": verify_rounds_used,
                            "passed": passed}
            record = self.transcript.write("verify", round=verify_rounds_used,
                                           exit_code=exit_code, passed=passed)
            if passed or verify_rounds_used > self.verify_rounds:
                return None, record
            return VERIFY_FEEDBACK.format(round=verify_rounds_used,
                                          rounds=self.verify_rounds + 1,
                                          command=self.verify,
                                          exit_code=exit_code, output=tail), record

        def take_fingerprint() -> str | None:
            """Spec #66 §4.1/§4.3: one measurement. Returns the fingerprint or
            None. One rule: a successful measurement sets changed/clears the
            reason; a failed or raising one sets changed = None and stores
            the reason (so changed_reason is present exactly when changed is
            null for that reason). BudgetExceeded/SandboxError are stored,
            then re-raised for the caller to map (finish() catches them)."""
            nonlocal fp_turn, fp_value, changed, changed_reason
            try:
                fp, reason = _fingerprint(self.sandbox)
            except BudgetExceeded as e:
                changed, changed_reason = None, f"budget: {e.reason}"
                raise
            except SandboxError as e:
                changed, changed_reason = None, f"sandbox: {e}"
                raise
            if fp is None:
                changed, changed_reason = None, reason
                return None
            fp_turn, fp_value = turns, fp
            if fp_start is not None:
                changed, changed_reason = (fp != fp_start), None
            return fp

        def check_verify(final: str, via: str):
            """(RunResult to return, or None; feedback to deliver, or None) for a
            completion path. Both completion paths — the finish tool and a plain
            answer — go through this one function, so they can never disagree
            about what verifying means. `via` names the carrier the caller will
            use for feedback ("finish_result" / "user") and is stamped on the
            verify event only when feedback is delivered (spec #60 §6.2). Every
            branch resolves the turn's terminal records (§4) -- a no-op on the
            plain-answer path, which has none. BudgetExceeded/SandboxError end
            the run with the same statuses a tool call would."""
            nonlocal stuck, unchanged_finishes
            if fp_start is not None:
                try:
                    fp = take_fingerprint()
                except BudgetExceeded as e:
                    resolve_finish(f"run not finished: change check could not run ({e.reason})")
                    return finish("budget_exceeded", e.reason), None
                except SandboxError as e:
                    resolve_finish(f"run not finished: change check could not run ({e})")
                    return finish("sandbox_error", str(e)), None
                if fp is not None and fp == fp_start:
                    if unchanged_finishes == 0:
                        unchanged_finishes = 1
                        stuck = None
                        repeats.reset()             # a rejection round is a fresh episode, as verify feedback
                        record = self.transcript.write("nudge", kind="unchanged_finish", turn=turns)
                        if record is not None:
                            record["via"] = "tool_result" if via == "finish_result" else "user"
                        text = UNCHANGED_REQUIRED if self.require_changes else UNCHANGED_PLAIN
                        resolve_finish(text)
                        return None, text
                    if self.require_changes:
                        resolve_finish("run not finished: nothing changed")
                        return finish("unchanged", final), None
            if not self.verify:
                resolve_finish(FINISH_DONE)
                return finish("completed", final), None
            try:
                feedback, record = run_verify()
            except BudgetExceeded as e:
                resolve_finish(f"run not finished: verify could not run ({e.reason})")
                return finish("budget_exceeded", e.reason), None
            except SandboxError as e:
                resolve_finish(f"run not finished: verify could not run ({e})")
                return finish("sandbox_error", str(e)), None
            if feedback is not None:
                # A feedback round is a fresh episode: the worker is retrying
                # against new instructions, so whatever bash streak was latched
                # (possibly in THIS SAME turn, before it called finish) must not
                # end the next turn as "stuck" for a check that no longer
                # reflects what the worker is doing.
                stuck = None
                repeats.reset()
                if record is not None:
                    record["via"] = via
                resolve_finish(feedback)
                return None, feedback
            if verify_state["passed"]:
                resolve_finish(FINISH_DONE)
                return finish("completed", final), None
            code = verify_state["exit_code"]
            resolve_finish("run not finished: verify failed "
                           f"(exit {code if code is not None else 'unknown'}); no fix rounds remain")
            return finish("verify_failed", final), None

        def one_turn():
            """One model turn (spec #60 §6.1: runs inside transcript.turn()).
            Returns the RunResult that ends the run, or None to continue."""
            nonlocal turns, trimmed_turns, timeouts, truncations, stuck
            turn_tool_msgs.clear()
            turn_terminal.clear()
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
                                          max_tokens=self.max_tokens,
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
            append_assistant(resp.text, tool_calls, finish_reason)
            trunc: dict = {}
            counted = False

            def note_truncation(tc=None) -> None:
                """Spec #65 §3.1: count this turn's truncation once and build
                the numbers both texts format. `tc` is the cut call (cases
                a/b) or None on the text path."""
                nonlocal truncations, counted
                if counted:
                    return
                truncations += 1
                counted = True
                text_chars, raw_chars = reply_size(resp)
                cut_chars, cut_lines = call_size(tc) if tc is not None else (0, 0)
                tc_chars, tc_lines = chunk_target(self.max_tokens, cut_chars, cut_lines)
                trunc.update({"cap": self.max_tokens,
                              "cap_chars": self.max_tokens * CHARS_PER_TOKEN,
                              "received": text_chars + raw_chars,
                              "cut_chars": cut_chars, "cut_lines": cut_lines,
                              "target_chars": tc_chars, "target_lines": tc_lines,
                              "n": truncations, "max": MAX_TRUNCATED_REPLIES})
            if not resp.tool_calls:
                content = resp.text
                kind = classify_text_reply(content, finish_reason)
                if kind == "answer":
                    ended, feedback = check_verify(content, via="user")
                    if ended is not None:
                        return ended
                    sandbox_text, sandbox_records = drain_sandbox()
                    deliver(_join_nudges(feedback, sandbox_text), sandbox_records)
                    return None
                if kind == "truncated":
                    note_truncation()
                    kind_record = self.transcript.write("nudge", kind=kind, turn=turns)
                    if truncations >= MAX_TRUNCATED_REPLIES:
                        return finish("model_error",
                                      TRUNCATION_ABORT.format(n=truncations, cap=self.max_tokens))
                else:
                    kind_record = self.transcript.write("nudge", kind=kind, turn=turns)
                    abort_reason = failures.record("empty_reply")
                    if abort_reason is not None:
                        return finish("model_error", abort_reason)
                stalled, stall_text, stall_record = check_progress()
                if stalled is not None:
                    return stalled
                ended, nc_text, nc_record = check_no_change()
                if ended is not None:
                    return ended
                sandbox_text, sandbox_records = drain_sandbox()
                deliver(_join_nudges(NUDGES[kind].format(**trunc), sandbox_text, stall_text, nc_text),
                        [r for r in (kind_record, *sandbox_records, stall_record, nc_record) if r is not None])
                return None

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
                terminal = False
                if tc.error is not None:
                    if finish_reason == "length":
                        note_truncation(tc)
                        result = truncated_call_result(name, tc.raw_arguments, trunc)
                    else:
                        abort_reason = failures.record("malformed_args")
                        result = f"ERROR: {tc.error}"
                    if abort_reason is None and truncations >= MAX_TRUNCATED_REPLIES:
                        abort_reason = TRUNCATION_ABORT.format(n=truncations, cap=self.max_tokens)
                elif finish_reason == "length" and self._missing_required(name, args):
                    # Spec §1.3 case (b): the Anthropic shape. A truncated
                    # tool_use whose `input` came back {} parses
                    # "successfully", so tc.error is None -- but a required
                    # parameter is simply absent. Checked BEFORE dispatch so
                    # the registry's bad_args path never swallows it: this
                    # is a truncation, not an argument mistake.
                    note_truncation(tc)
                    result = truncated_call_result(name, tc.raw_arguments, trunc)
                    if abort_reason is None and truncations >= MAX_TRUNCATED_REPLIES:
                        abort_reason = TRUNCATION_ABORT.format(n=truncations, cap=self.max_tokens)
                else:
                    try:
                        spec = self.registry.spec(name)
                        if spec is not None and spec.terminal:
                            summary = args.get("summary")
                            pending_finish = summary if isinstance(summary, str) else ""
                            result = FINISH_PROVISIONAL
                            terminal = True
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
                record = self.transcript.write("tool_result", tool=name,
                                               args=raw_args[:500],
                                               result=self.registry.transcript_preview(name, result),
                                               **timed_out_fields)
                if name != FINISH_TOOL:
                    note_last_tool_result(name, raw_args, result)
                msg = tool_message(tc.id, result)
                messages.append(msg)
                turn_tool_msgs.append((msg, record))
                if terminal:
                    turn_terminal.append((msg, record))
                if abort_reason is not None:
                    return finish("model_error", abort_reason)

            # Composed here (text only, no transcript write yet) so both
            # paths below that may CONTINUE the run -- the verify-feedback
            # path just below, and the ordinary nudge path at the bottom --
            # can carry it. The transcript event itself is written at
            # exactly one of those two points (never both: they are
            # mutually exclusive per turn), and never at all on a turn that
            # ENDS the run (finish/stuck/verify-passed all return above or
            # below without reaching either write) -- spec §4.3: the nudge
            # is emitted on turns that continue.
            timeout_text = TIMEOUT_NUDGE if timed_out_this_turn else None

            if pending_finish is not None:
                ended, feedback = check_verify(pending_finish, via="finish_result")
                if ended is not None:
                    return ended
                # The feedback is already the finish result (resolve_finish);
                # only the timeout nudge still needs delivering.
                sandbox_text, sandbox_records = drain_sandbox()
                if timed_out_this_turn:
                    timeout_record = self.transcript.write("nudge", kind="timeout", turn=turns)
                    text = _join_nudges(sandbox_text, timeout_text)
                    if text:
                        deliver(text, [*sandbox_records, timeout_record])
                else:
                    # no timeout but there may be sandbox notices
                    text = _join_nudges(sandbox_text)
                    if text:
                        deliver(text, sandbox_records)
                return None

            # Same rule as `finish` in a mixed turn: the turn's remaining
            # tool calls have already run. `finish` still wins — a worker
            # that declared itself done did so with full knowledge of the
            # failure it had just seen.
            if stuck is not None:
                return finish("stuck",
                              f"the same failing command ran {stuck['repeats']} "
                              f"times in a row")

            stalled, stall_text, stall_record = check_progress()
            if stalled is not None:
                return stalled
            ended, nc_text, nc_record = check_no_change()
            if ended is not None:
                return ended

            malformed_text = malformed_record = None
            if malformed_count > 0:
                malformed_text = (f"{malformed_count} of your tool calls were malformed "
                                  "(unaddressable: no usable id/name) and were "
                                  "discarded. Re-issue them as valid tool calls.")
                # Spec #60 §6.2: delivered since 0.5, transcribed since 1.0.
                malformed_record = self.transcript.write("nudge", kind="malformed_entry", turn=turns)
            timeout_record = None
            if timed_out_this_turn:
                # Once per turn, however many commands timed out in it.
                timeout_record = self.transcript.write("nudge", kind="timeout", turn=turns)
            sandbox_text, sandbox_records = drain_sandbox()
            deliver(_join_nudges(malformed_text, sandbox_text, timeout_text, stall_text, nc_text),
                    [r for r in (malformed_record, *sandbox_records, timeout_record, stall_record, nc_record) if r is not None])
            return None
        try:
            # Spec #66 §4.1 (1): the start fingerprint, INSIDE this try so a
            # Ctrl-C during the exec reaches the outer KeyboardInterrupt
            # handler and ends `interrupted` with a run_end (turns == 0).
            try:
                fp_start = take_fingerprint()      # None = guard off for this run
            except BudgetExceeded as e:
                return finish("budget_exceeded", e.reason)
            except SandboxError as e:
                return finish("sandbox_error", str(e))
            fp_check = fp_start
            while True:
                with self.transcript.turn():
                    try:
                        ended = one_turn()
                    except KeyboardInterrupt:
                        # Spec #60 §4 (v3): caught INSIDE the turn so finish()
                        # resolves the finish record and flushes before the
                        # turn's buffer is written.
                        ended = finish("interrupted", "")
                if ended is not None:
                    return ended
        except KeyboardInterrupt:
            # Between turns only: nothing is buffered and no finish record can
            # be pending here.
            return finish("interrupted", "")
