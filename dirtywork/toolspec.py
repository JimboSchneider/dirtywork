from __future__ import annotations

import posixpath
import re
import time
from dataclasses import dataclass
from typing import Any, Callable


# These tags are built by concatenation ON PURPOSE: several local models' chat
# templates parse these exact tags in their own output (Qwen3-coder's tool-call
# XML, think-tag stripping), so a worker model editing this file through its
# tool channel could not emit them literally. Keep them concatenated.

_RAW_MARKERS = ("[" + "TOOL_CALLS]",) + tuple(
    "<" + m for m in ("tool_call>", "function=", "function_call>", "|tool_call|>")
)
# Spec #67 §0.3: an OpenAI-compatible server may sanitise a tool name to
# [A-Za-z0-9_-] before it reaches us, turning every marker character into "_"
# -- match that shape too (derived, not spelled).
TOOL_CALL_MARKERS = _RAW_MARKERS + tuple(re.sub(r"[^A-Za-z0-9_-]", "_", m) for m in _RAW_MARKERS)


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
    """``schema`` (spec §1.3) is the parameter's full JSON schema for a
    parameter whose shape is not a flat scalar -- an array of objects, say.
    When set, ToolRegistry.schemas() emits it INSTEAD of ``{"type": type}``
    (with ``description`` merged in exactly as for a flat param) and
    _validate_args validates against it recursively. ``type`` stays set to the
    schema's top-level type so canonical_args and any other reader that only
    knows about flat types keeps working -- canonical_args itself only special-
    cases "integer"/"number" coercion, so for a schema-bearing param (whose
    ``type`` is "array"/"object") it passes the raw, unvalidated, uncoerced
    value straight through rather than the validated/coerced one _validate_args
    produces. Moot for a mutating tool (ProgressTracker.note_call short-circuits
    on tool name for anything in runner._MUTATING_TOOLS before it ever hashes
    canonical_args), which is the only kind of tool a schema-bearing param is
    expected to belong to. Leave it None for a flat param and nothing about
    that param changes.

    ``unit="seconds"`` makes ``_validate_args`` coerce the value with
    ``_coerce_duration`` instead of ``_check_scalar``, and build a rejection
    message from the param's ``description`` (so that description must read as a
    "must be …" predicate); ``schemas()`` never emits ``unit``."""

    type: str
    description: str = ""
    default: Any = MISSING
    schema: dict | None = None
    unit: str | None = None


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


def _coerce_numeric_string(ptype: str, value):
    """A numeric STRING for an "integer"/"number" param, coerced the way the
    old ToolExecutor's own `int(timeout)` did -- local models routinely send
    "60" instead of 60. Returns the coerced int/float, or None when `value`
    is not a string or does not parse as that type ("abc", "1.5" for
    "integer"). bool is never a str, so True/False never reach here."""
    if not isinstance(value, str):
        return None
    try:
        if ptype == "integer":
            return int(value)
        if ptype == "number":
            return float(value)
    except ValueError:
        return None
    return None


def _check_scalar(ptype: str, value, label: str):
    """The scalar-leaf check shared by the flat-type path (_validate_args) and
    the nested-schema path (_validate_against_schema): look up ptype in
    _TYPE_CHECKS, and if the value doesn't already match, try coercing a
    numeric string via _coerce_numeric_string before giving up. Returns the
    (possibly coerced) value, or raises ToolValidationError(f"{label} must be
    {ptype}, got {type(value).__name__}") -- `label` is the caller's own
    identifier for the value (a flat param's `"parameter 'name'"`, or a nested
    leaf's dotted/indexed `path`), so both call sites keep their existing,
    byte-identical messages. A ptype with no _TYPE_CHECKS entry (e.g. "array"/
    "object" reaching here, which neither caller does today) passes through
    unchecked, matching prior behaviour at both sites."""
    check = _TYPE_CHECKS.get(ptype)
    if check is None or check(value):
        return value
    coerced = _coerce_numeric_string(ptype, value)
    if coerced is None:
        raise ToolValidationError(f"{label} must be {ptype}, got {type(value).__name__}")
    return coerced


# Compiled regex for duration string parsing: digits followed by optional whitespace and unit
_DURATION_REGEX = re.compile(r'^\s*(\d{1,9})\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes)\s*$', re.IGNORECASE | re.ASCII)


def _coerce_duration(value):
    """Convert a duration value to seconds (int).

    Accepts:
      - int (but not bool): returns as-is
      - string of digits: parses as seconds via int()
      - string with unit suffix (s/sec/secs/second/seconds/m/min/mins/minute/minutes):
        parses and multiplies accordingly (case-insensitive)

    Returns None for anything else: "60ms", "1.5s", "-5s", "", "abc", "s",
    floats, bools, None.
    """
    # Booleans are ints in Python but we never want to accept them
    if isinstance(value, bool):
        return None
    # int: return as-is
    if isinstance(value, int):
        return value
    # string: try to parse as duration or plain number
    if isinstance(value, str):
        # Try parsing as a plain integer first (for backward compatibility)
        try:
            return int(value)
        except ValueError:
            pass
        # Try parsing as a duration string
        match = _DURATION_REGEX.match(value)
        if match:
            num = int(match.group(1))
            unit = match.group(2).lower()
            if unit in ('s', 'sec', 'secs', 'second', 'seconds'):
                return num
            elif unit in ('m', 'min', 'mins', 'minute', 'minutes'):
                return num * 60
        return None
    return None


def _coerce_param(pspec, value):
    """Coerce a parameter value based on its spec type.

    Returns the coerced value, or None if coercion isn't applicable.
    For duration params (unit="seconds"), uses _coerce_duration.
    For integer/number params, uses _coerce_numeric_string."""
    if pspec.unit == "seconds":
        return _coerce_duration(value)
    elif pspec.type in ("integer", "number"):
        return _coerce_numeric_string(pspec.type, value)
    return None


def _validate_against_schema(value, schema: dict, path: str):
    """Validate `value` against the minimal JSON-Schema subset a ParamSpec.schema
    may use (spec §1.3): type, minItems, maxItems, items, properties, required,
    additionalProperties. Deliberately NOT a JSON-Schema library -- anything else
    in the schema dict is ignored, and the only schemas that reach here are the
    ones this repo authors.

    Returns the validated value, with numeric strings coerced at every scalar
    leaf exactly as the top level coerces them, so a tool function may rely on
    the shape its schema declares. Raises ToolValidationError with a
    path-qualified message ("edits[2].new must be string, got int") that
    ToolRegistry.execute turns into a `bad_args` result.

    additionalProperties: false is enforced HERE too, not just on the wire:
    the registry's drop-unknown-keys policy is a TOP-LEVEL concession to local
    models that attach stray parameters from other harnesses' schemas, and a
    nested object is authored per call, so it gets the strict rule."""
    ptype = schema.get("type")
    if ptype == "array":
        if not isinstance(value, list):
            raise ToolValidationError(f"{path} must be array, got {type(value).__name__}")
        minimum = schema.get("minItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise ToolValidationError(f"{path} must have at least {minimum} item(s)")
        maximum = schema.get("maxItems")
        if isinstance(maximum, int) and len(value) > maximum:
            raise ToolValidationError(f"{path} must have at most {maximum} item(s)")
        item_schema = schema.get("items")
        if not isinstance(item_schema, dict):
            return value
        return [_validate_against_schema(item, item_schema, f"{path}[{i}]")
                for i, item in enumerate(value)]
    if ptype == "object":
        if not isinstance(value, dict):
            raise ToolValidationError(f"{path} must be an object")
        properties = schema.get("properties") or {}
        for name in schema.get("required") or ():
            if name not in value:
                raise ToolValidationError(f"{path} is missing required property {name!r}")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    raise ToolValidationError(f"{path} has unexpected property {name!r}")
        out = {}
        for name, item in value.items():
            sub = properties.get(name)
            out[name] = (_validate_against_schema(item, sub, f"{path}.{name}")
                         if isinstance(sub, dict) else item)
        return out
    return _check_scalar(ptype, value, path)


def _input_bytes(value) -> int:
    """UTF-8 length of every str VALUE inside `value`, recursively (spec §1.4).
    Dict KEYS and non-string scalars do not count: the cap exists to bound the
    text a model sent, and a key is this repo's schema, not the model's."""
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, dict):
        return sum(_input_bytes(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_input_bytes(item) for item in value)
    return 0


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
            if pspec.schema is not None:
                # Spec §1.3: the whole shape is proved here, before spec.fn runs,
                # so a tool function never has to re-check nested item shapes.
                call_args[pname] = _validate_against_schema(value, pspec.schema, pname)
                continue
            # Special handling for duration params (unit="seconds")
            if pspec.unit == "seconds":
                coerced = _coerce_param(pspec, value)
                if coerced is None:
                    raise ToolValidationError(
                        f"parameter {pname!r} must be {pspec.description} — got {value!r}")
                value = coerced
            else:
                value = _check_scalar(pspec.type, value, f"parameter '{pname}'")
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
                # A schema-bearing param renders its own schema verbatim; the
                # copy matters because `description` is merged in below and the
                # ParamSpec's dict is shared with every future call. The merge
                # runs last for both kinds of param, so `description` is always
                # the final key -- flat and nested render the same way.
                if pspec.unit == "seconds":
                    # Duration params accept both integer and string values
                    prop = {"type": ["integer", "string"]}
                elif pspec.schema is not None:
                    prop = dict(pspec.schema)
                else:
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
                value = args[pname]
                coerced = _coerce_param(pspec, value)
                if coerced is not None:
                    value = coerced   # "5" and 5, or "2m" and 120 must canonicalize the same way
                out[pname] = value
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

    def recover_name(self, name: str) -> "tuple[str, str | None, int]":
        """(name, marker, cut). A registered name is returned as-is with marker None.
        A name that is not registered but, after its LAST tool-call marker, ends in a
        registered name is recovered to that name; `marker` is the marker found and
        `cut` the number of characters before it (the model's stray text). Anything
        else is returned unchanged with marker None: the unknown-tool path decides."""
        if name in self._table:
            return name, None, 0
        best = max(((name.rfind(m), m) for m in TOOL_CALL_MARKERS if m in name),
                   default=(-1, None))
        if best[0] < 0:
            return name, None, 0
        pos, marker = best
        suffix = name[pos + len(marker):].strip()
        if suffix in self._table:
            return suffix, marker, pos
        return name, None, 0

    def execute(self, name: str, args: dict, *, sandbox, deadline) -> ToolResult:
        spec = self._table.get(name)
        if spec is None:
            available = ", ".join(self._table)
            shown = name if len(name) <= 80 else name[:40] + "…" + name[-40:] + " (name truncated)"
            return ToolResult(
                text=(f"ERROR: unknown tool '{shown}'. Available: {available}. "
                      f"To end the run call finish(summary=...)."),
                kind="error", failure="unknown_tool")
        try:
            call_args = _validate_args(spec, args)
        except ToolValidationError as e:
            return ToolResult(text=f"ERROR: bad arguments for {name}: {e}",
                              kind="error", failure="bad_args")

        caps = spec.caps
        if caps.max_input_bytes is not None:
            # Spec §1.4: recursive, so a batch tool's nested strings count too.
            # Top-level strings (e.g. `path`) still count exactly as before.
            total = _input_bytes(call_args)
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
