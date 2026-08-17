from __future__ import annotations

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


class ToolRegistry:
    def __init__(self, transcript=None):
        self.transcript = transcript
        self._table: dict = {}

    def register(self, spec: ToolSpec) -> None:
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
