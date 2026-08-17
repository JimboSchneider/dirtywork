from __future__ import annotations

import posixpath
import time
from dataclasses import dataclass
from typing import Any, Callable


class _MissingType:
    """Sentinel distinguishing 'no default provided' from an explicit ``None``
    default. Falsy, so ``if param.default:`` degrades safely for callers that
    forget to check identity, but code that needs correctness uses ``is``."""

    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING = _MissingType()


@dataclass(frozen=True)
class ParamSpec:
    type: str
    description: str = ""
    default: Any = MISSING


@dataclass(frozen=True)
class Caps:
    """Enforced generically by ToolRegistry (Task 2). ``fs``/``network`` are
    declared for documentation and schema purposes only: every tool runs inside
    the Sandbox passed to ``execute``, so there is no host/sandbox dispatch flag
    left to gate on. ``max_output_chars`` is an OUTER safety net -- every
    built-in tool already truncates its own result and appends an explanatory
    note, so a tool's cap here must sit above its own cap plus that note or the
    registry would chop the note off (see builtin_tools.TOOL_OUTPUT_CAP).
    ``transcript`` picks how much of a result reaches the transcript:
    "full" (uncapped), "preview" (TRANSCRIPT_PREVIEW_CHARS), "none" (nothing)."""

    fs: str  # "none" | "read" | "write"
    network: bool = False
    max_input_bytes: int | None = None
    max_output_chars: int = 8000
    timeout_default: int | None = None
    timeout_max: int | None = None
    transcript: str = "preview"  # "full" | "preview" | "none"


@dataclass(frozen=True)
class ToolSpec:
    """``terminal=True`` marks a tool the RUNNER handles itself and never
    dispatches to ``execute`` -- ``finish`` ends the run, so its arguments are
    read straight off the ToolCall (a missing ``summary`` completes the run with
    an empty final message rather than becoming a validation strike)."""

    name: str
    description: str
    params: dict
    required: tuple
    fn: Callable
    caps: Caps
    terminal: bool = False


@dataclass(frozen=True)
class ToolResult:
    """``failure`` is the runner's FailureTracker kind for this result --
    "unknown_tool" or "bad_args" -- or None when nothing should be counted
    against the model. A blocked result and a deadline-exceeded refusal both
    carry ``failure=None`` because today's executor lets them RESET the strike
    counter (``execute`` returned normally), and that behaviour is preserved."""

    text: str
    kind: str  # "ok" | "error" | "blocked"
    failure: str | None = None


class ToolValidationError(Exception):
    """Raised by the internal argument validator; ToolRegistry.execute (Task 2)
    catches it and converts it to an error ToolResult. Never escapes execute()."""


TRANSCRIPT_PREVIEW_CHARS = 2000

_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
}


def _validate_args(spec: ToolSpec, args: dict) -> dict:
    """Effective keyword arguments for spec.fn. Unknown keys are DROPPED, not
    rejected (SP1, commit 23a9c22): local models routinely attach parameters
    from other harnesses' tool schemas, and turning that habit into three
    consecutive bad_args strikes aborted real runs. Missing/invalid REQUIRED
    arguments still fail loudly."""
    missing = [p for p in spec.required if p not in args]
    if missing:
        raise ToolValidationError(f"missing required parameter(s): {', '.join(missing)}")
    call_args = {}
    for pname, pspec in spec.params.items():
        if pname in args:
            value = args[pname]
            if value is None and pspec.default is None:
                call_args[pname] = None          # the model spelled out the default
                continue
            check = _TYPE_CHECKS.get(pspec.type)
            if check is not None and not check(value):
                raise ToolValidationError(
                    f"parameter '{pname}' must be {pspec.type}, got {type(value).__name__}")
            call_args[pname] = value
        elif pspec.default is not MISSING:
            call_args[pname] = pspec.default
    return call_args


class ToolRegistry:
    def __init__(self, transcript=None):
        self.transcript = transcript
        self._table: dict = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._table:
            raise ValueError(f"tool '{spec.name}' is already registered")
        self._table[spec.name] = spec

    def spec(self, name: str):
        return self._table.get(name)

    def names(self) -> list:
        """Tool names in registration order -- the order they are advertised to
        the model, and the order the unknown-tool error lists them in."""
        return list(self._table)

    def schemas(self) -> list:
        out = []
        for spec in self._table.values():
            properties = {}
            for pname, pspec in spec.params.items():
                prop = {"type": pspec.type}
                if pspec.description:
                    prop["description"] = pspec.description
                properties[pname] = prop
            out.append({
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": list(spec.required),
                    },
                },
            })
        return out

    def canonical_args(self, name: str, args) -> dict:
        """The call's *effective* arguments, for the runner's stall detector:
        unknown keys dropped (execute() ignores them), defaults filled in from
        the spec, `timeout` dropped (execute() clamps it per call anyway),
        path-like strings normalized (`foo` == `./foo` == `foo/`), and
        `command` stripped. Two calls that do the same thing must look the
        same, or a stuck model could dodge `stalled` by varying noise."""
        if not isinstance(args, dict):
            return {}
        spec = self._table.get(name)
        if spec is None:
            return dict(args)
        out = {}
        for pname, pspec in spec.params.items():
            if pname == "timeout":
                continue
            if pname in args:
                out[pname] = args[pname]
            elif pspec.default is not MISSING:
                out[pname] = pspec.default
        if isinstance(out.get("path"), str):
            stripped = out["path"].strip()
            out["path"] = posixpath.normpath(stripped) if stripped else "."
        if isinstance(out.get("command"), str):
            out["command"] = out["command"].strip()
        return out

    def transcript_preview(self, name: str, text: str) -> str:
        """How much of a tool result reaches the transcript, per Caps.transcript.
        Unknown names (an unknown-tool error) get the default preview cap."""
        spec = self._table.get(name)
        mode = spec.caps.transcript if spec is not None else "preview"
        if mode == "none":
            return ""
        if mode == "full":
            return text
        return text[:TRANSCRIPT_PREVIEW_CHARS]

    def execute(self, name: str, args: dict, *, sandbox, deadline) -> ToolResult:
        spec = self._table.get(name)
        if spec is None:
            available = ", ".join(self._table)
            return ToolResult(
                text=(f"ERROR: unknown tool '{name}'. Available: {available}. "
                      f"To end the run call finish(summary=...)."),
                kind="error", failure="unknown_tool")
        try:
            call_args = _validate_args(spec, args)
        except ToolValidationError as e:
            return ToolResult(text=f"ERROR: bad arguments for {name}: {e}",
                              kind="error", failure="bad_args")

        caps = spec.caps
        if caps.max_input_bytes is not None:
            total = sum(len(v.encode("utf-8")) for v in call_args.values()
                        if isinstance(v, str))
            if total > caps.max_input_bytes:
                return ToolResult(
                    text=(f"ERROR: bad arguments for {name}: input is {total} bytes, "
                          f"over the {caps.max_input_bytes}-byte limit."),
                    kind="error", failure="bad_args")

        remaining = None
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Matches ToolExecutor.execute: the run is over, the tool never
                # runs, and this does NOT count as a model failure.
                return ToolResult(
                    text=("ERROR: run deadline exceeded; stop calling tools and "
                          "summarize what you have done."),
                    kind="error", failure=None)

        if caps.timeout_default is not None:
            # Deadline-based clamping applies whenever a tool declares a
            # timeout_default, even for tools (like grep) that never expose
            # "timeout" as a model-settable schema parameter -- the registry
            # injects/overwrites call_args["timeout"] either way, and spec.fn's
            # own keyword default absorbs it since it isn't in the JSON schema.
            requested = call_args.get("timeout")
            bound = requested if requested is not None else caps.timeout_default
            if caps.timeout_max is not None:
                bound = min(bound, caps.timeout_max)
            if remaining is not None:
                bound = min(bound, max(1, int(remaining)))
            call_args["timeout"] = int(bound)

        try:
            result_text = spec.fn(sandbox, **call_args)
        except TypeError as e:
            # ToolExecutor turned a TypeError out of the tool function into a
            # bad_args strike; keep that (validation above catches the common
            # cases, this catches the rest) rather than aborting the run.
            return ToolResult(text=f"ERROR: bad arguments for {name}: {e}",
                              kind="error", failure="bad_args")

        if len(result_text) > caps.max_output_chars:
            result_text = (result_text[: caps.max_output_chars]
                           + f"\n[output truncated at {caps.max_output_chars} chars]")

        if result_text.startswith("BLOCKED:"):
            if self.transcript is not None:
                self.transcript.write("guardrail_block", tool=name, args=call_args,
                                      reason=result_text)
            return ToolResult(text=result_text, kind="blocked", failure=None)
        return ToolResult(text=result_text, kind="ok", failure=None)
