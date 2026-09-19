"""Per-tool canonicalization: ActionRequest to CanonicalAction, or to a
Rejection carrying one of #135's codes (spec §3, §4, §5, §7, §8)."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Optional, Sequence

from .bounds import (
    FIREWALL_SCHEMA_VERSION,
    MAX_BASH_TIMEOUT,
    MAX_COMMAND_CHARS,
    MAX_GLOB_CHARS,
    MAX_INT,
    MAX_PATH_CHARS,
    MAX_PATTERN_CHARS,
    MAX_STRING_CHARS,
    MAX_SUMMARY_CHARS,
)
from .capabilities import ActionKind, BASE_CAPABILITIES, Capability
from .errors import FirewallInternalError
from .paths import TargetClass, normalize_path
from .reasons import ReasonCode
from .request import Rejection, check_request
from .schema import ARGS_FOR_KIND, ActionRequest, CanonicalAction, Edit, SemanticStatus

# Copied by value from dirtywork.toolspec (spec §3): these tags are built by
# concatenation ON PURPOSE, so a worker model editing this file through its
# own tool channel could not emit them literally.
_RAW_MARKERS = ("[" + "TOOL_CALLS]",) + tuple(
    "<" + m for m in ("tool_call>", "function=", "function_call>", "|tool_call|>")
)
TOOL_CALL_MARKERS = _RAW_MARKERS + tuple(
    re.sub(r"[^A-Za-z0-9_-]", "_", m) for m in _RAW_MARKERS
)

WRITE_KINDS = frozenset(
    {
        ActionKind.WRITE_FILE,
        ActionKind.APPEND_FILE,
        ActionKind.EDIT_FILE,
        ActionKind.APPLY_EDITS,
        ActionKind.INSERT_BEFORE,
        ActionKind.INSERT_AFTER,
    }
)


@dataclass(frozen=True)
class Field:
    """One parameter of one ActionKind, in the registry's own order (spec
    §4). `limit` bounds `str`/`path`/`command` characters after coercion;
    `lo`/`hi` bound `int` after coercion. Neither applies to `duration`
    (clamped, never rejected) or `edits` (its own rules, spec §5 step 6)."""

    name: str
    kind: str  # "str" | "int" | "duration" | "path" | "command" | "edits"
    required: bool
    default: Any  # ignored when required; may itself be None (grep.glob)
    limit: Optional[int]
    lo: Optional[int]
    hi: Optional[int]


FIELD_TABLE: "dict[ActionKind, tuple[Field, ...]]" = {
    ActionKind.READ_FILE: (
        Field("path", "path", True, None, MAX_PATH_CHARS, None, None),
        Field("offset", "int", False, 0, None, 0, MAX_INT),
        Field("limit", "int", False, 400, None, 1, MAX_INT),
    ),
    ActionKind.WRITE_FILE: (
        Field("path", "path", True, None, MAX_PATH_CHARS, None, None),
        Field("content", "str", True, None, MAX_STRING_CHARS, None, None),
    ),
    ActionKind.APPEND_FILE: (
        Field("path", "path", True, None, MAX_PATH_CHARS, None, None),
        Field("text", "str", True, None, MAX_STRING_CHARS, None, None),
    ),
    ActionKind.EDIT_FILE: (
        Field("path", "path", True, None, MAX_PATH_CHARS, None, None),
        Field("old_string", "str", True, None, MAX_STRING_CHARS, None, None),
        Field("new_string", "str", True, None, MAX_STRING_CHARS, None, None),
    ),
    ActionKind.APPLY_EDITS: (
        Field("path", "path", True, None, MAX_PATH_CHARS, None, None),
        Field("edits", "edits", True, None, None, None, None),
    ),
    ActionKind.INSERT_BEFORE: (
        Field("path", "path", True, None, MAX_PATH_CHARS, None, None),
        Field("anchor", "str", True, None, MAX_STRING_CHARS, None, None),
        Field("text", "str", True, None, MAX_STRING_CHARS, None, None),
    ),
    ActionKind.INSERT_AFTER: (
        Field("path", "path", True, None, MAX_PATH_CHARS, None, None),
        Field("anchor", "str", True, None, MAX_STRING_CHARS, None, None),
        Field("text", "str", True, None, MAX_STRING_CHARS, None, None),
    ),
    ActionKind.LIST_DIR: (
        Field("path", "path", False, ".", MAX_PATH_CHARS, None, None),
    ),
    ActionKind.GREP: (
        Field("pattern", "str", True, None, MAX_PATTERN_CHARS, None, None),
        Field("path", "path", False, ".", MAX_PATH_CHARS, None, None),
        Field("glob", "str", False, None, MAX_GLOB_CHARS, None, None),
    ),
    ActionKind.BASH: (
        Field("command", "command", True, None, MAX_COMMAND_CHARS, None, None),
        Field("timeout", "duration", False, 120, None, None, None),
    ),
    ActionKind.FINISH: (
        # Declared exception (spec §4): the registry requires `summary` with
        # no default; the Runner already canonicalizes a missing one to "".
        Field("summary", "str", False, "", MAX_SUMMARY_CHARS, None, None),
    ),
}


@dataclass(frozen=True)
class Normalization:
    """The result of one `canonicalize` call: exactly one of `action` and
    `rejection` is set (spec §2)."""

    action: Optional[CanonicalAction]
    rejection: Optional[Rejection]
    dropped_keys: int

    def __post_init__(self) -> None:
        action_set = self.action is not None
        rejection_set = self.rejection is not None
        if action_set == rejection_set:
            raise FirewallInternalError(
                "Normalization requires exactly one of action and rejection"
            )
        if isinstance(self.dropped_keys, bool) or not isinstance(self.dropped_keys, int):
            raise FirewallInternalError("dropped_keys must be an int")
        if self.dropped_keys < 0:
            raise FirewallInternalError("dropped_keys must be non-negative")
        if rejection_set and self.dropped_keys != 0:
            raise FirewallInternalError("dropped_keys must be 0 on a rejection")


_ACTION_KIND_VALUES = tuple(kind.value for kind in ActionKind)
_MARKERS_LONGEST_FIRST = tuple(sorted(TOOL_CALL_MARKERS, key=len, reverse=True))


def recover_name(name: str) -> tuple:
    """(name, marker, cut): the same answer as the registry's own
    `ToolRegistry.recover_name`, computed by a linear, end-anchored
    algorithm instead of the registry's quadratic marker search (spec §3).

    A name that already is an ActionKind value is returned as-is. Otherwise
    the trailing whitespace is stripped; the remaining tail must end in some
    ActionKind value `k`; walking back over any whitespace before `k` finds
    the position a marker must end at, checked longest-first. No two
    ActionKind values can both be a suffix of the tail (none is a suffix of
    another), so `k` is unique when it exists.
    """
    if name in _ACTION_KIND_VALUES:
        return name, None, 0
    tail = name.rstrip()
    matched_kind = None
    for kind_value in _ACTION_KIND_VALUES:
        if tail.endswith(kind_value):
            matched_kind = kind_value
            break
    if matched_kind is None:
        return name, None, 0
    marker_end = len(tail) - len(matched_kind)
    while marker_end > 0 and tail[marker_end - 1].isspace():
        marker_end -= 1
    for marker in _MARKERS_LONGEST_FIRST:
        start = marker_end - len(marker)
        if start >= 0 and tail[start:marker_end] == marker:
            return matched_kind, marker, start
    return name, None, 0


# Copied by value from dirtywork.toolspec._DURATION_REGEX / _coerce_duration
# (spec §4): digits, optional whitespace, a seconds or minutes unit.
_DURATION_REGEX = re.compile(
    r"^\s*(\d{1,9})\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes)\s*$",
    re.IGNORECASE | re.ASCII,
)


def _coerce_duration(value: Any) -> Optional[int]:
    """Seconds as an int, or None: a bool is never accepted; an int (not
    bool) passes through; a string is tried as a plain int first, then
    against `_DURATION_REGEX`, minutes multiplied by 60."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
        match = _DURATION_REGEX.match(value)
        if match:
            num = int(match.group(1))
            unit = match.group(2).lower()
            if unit in ("s", "sec", "secs", "second", "seconds"):
                return num
            if unit in ("m", "min", "mins", "minute", "minutes"):
                return num * 60
        return None
    return None


# An int in [MIN_INT, MAX_INT] needs at most 11 characters; the allowance
# covers the sign, whitespace and underscores that int() accepts. A longer
# numeric string cannot be in range and is rejected before int() runs, which
# is quadratic on Python 3.9 and raises past 4,300 digits on 3.11+ (spec §5).
_MAX_NUMERIC_CHARS = 32


def _clamp_timeout(value: int) -> int:
    return max(1, min(value, MAX_BASH_TIMEOUT))


def _missing(name: str) -> Rejection:
    return Rejection(ReasonCode.ARGUMENT_MISSING, f"missing required argument '{name}'")


def _type_invalid(name: str) -> Rejection:
    return Rejection(ReasonCode.ARGUMENT_TYPE_INVALID, f"argument '{name}' has an invalid type")


def _unexpected(location: str) -> Rejection:
    return Rejection(ReasonCode.ARGUMENT_UNEXPECTED, f"unexpected key in {location}")


def _too_long(name: str, limit: int) -> Rejection:
    return Rejection(ReasonCode.STRING_TOO_LONG, f"argument '{name}' exceeds {limit} chars")


def _out_of_range(name: str, lo: int, hi: int) -> Rejection:
    return Rejection(ReasonCode.NUMBER_OUT_OF_RANGE, f"argument '{name}' must be in [{lo}, {hi}]")


def _coerce_scalar(field: Field, raw: Any) -> "tuple[Any, Optional[Rejection]]":
    """Coerces and bounds one `str`/`path`/`command`/`int`/`duration` field,
    immediately after coercion (spec §5 steps 5 and 6). Returns
    `(value, None)` or `(None, rejection)`."""
    kind = field.kind
    if kind in ("str", "path", "command"):
        if not isinstance(raw, str):
            return None, _type_invalid(field.name)
        if len(raw) > field.limit:
            return None, _too_long(field.name, field.limit)
        return raw, None
    if kind == "int":
        if isinstance(raw, bool):
            return None, _type_invalid(field.name)
        if isinstance(raw, int):
            value = raw
        elif isinstance(raw, str):
            if len(raw) > _MAX_NUMERIC_CHARS:
                return None, _too_long(field.name, _MAX_NUMERIC_CHARS)
            try:
                value = int(raw)
            except ValueError:
                return None, _type_invalid(field.name)
        else:
            return None, _type_invalid(field.name)
        if not (field.lo <= value <= field.hi):
            return None, _out_of_range(field.name, field.lo, field.hi)
        return value, None
    if kind == "duration":
        if isinstance(raw, str) and len(raw) > _MAX_NUMERIC_CHARS:
            return None, _too_long(field.name, _MAX_NUMERIC_CHARS)
        value = _coerce_duration(raw)
        if value is None:
            return None, _type_invalid(field.name)
        return _clamp_timeout(value), None
    raise FirewallInternalError(f"unhandled field kind {kind!r}")


def _coerce_edits(raw: Any) -> "tuple[Any, Optional[Rejection]]":
    """The `edits` value-kind rule and bound in one pass (spec §5 step 6): a
    non-empty list of objects with exactly `old` and `new`, both `str`,
    `old` nonempty. An unknown key is reported by index only, never by
    name."""
    if not isinstance(raw, list) or not raw:
        return None, _type_invalid("edits")
    items = []
    for index, item in enumerate(raw):
        location = f"edits[{index}]"
        if not isinstance(item, dict):
            return None, _type_invalid(location)
        if "old" not in item or "new" not in item:
            missing_field = "old" if "old" not in item else "new"
            return None, _type_invalid(f"{location}.{missing_field}")
        if set(item) - {"old", "new"}:
            return None, _unexpected(location)
        old, new = item["old"], item["new"]
        if not isinstance(old, str) or not old:
            return None, _type_invalid(f"{location}.old")
        if not isinstance(new, str):
            return None, _type_invalid(f"{location}.new")
        if len(old) > MAX_STRING_CHARS:
            return None, _too_long(f"{location}.old", MAX_STRING_CHARS)
        if len(new) > MAX_STRING_CHARS:
            return None, _too_long(f"{location}.new", MAX_STRING_CHARS)
        items.append(Edit(old=old, new=new))
    return tuple(items), None


def _process_fields(fields: "tuple[Field, ...]", arguments: dict) -> "tuple[Any, Optional[Rejection]]":
    """Steps 3, 5 and 6 (spec §5): every required field must be present
    before any field is coerced, so a type problem on one required field
    never hides a missing later one; then each field, in table order, gets
    its default/null handling, its value-kind coercion and its bound."""
    for field in fields:
        if field.required and field.name not in arguments:
            return None, _missing(field.name)
    values = {}
    for field in fields:
        if field.name not in arguments:
            values[field.name] = field.default
            continue
        raw = arguments[field.name]
        if raw is None:
            if field.required:
                return None, _type_invalid(field.name)
            values[field.name] = field.default
            continue
        if field.kind == "edits":
            value, rejection = _coerce_edits(raw)
        else:
            value, rejection = _coerce_scalar(field, raw)
        if rejection is not None:
            return None, rejection
        values[field.name] = value
    return values, None


def canonicalize(request: ActionRequest) -> Normalization:
    """The one entry point from a validated-or-not `ActionRequest` to a
    `Normalization` (spec §5). Runs name recovery and `check_request`
    itself, then the field table pass, then builds capabilities and the
    `CanonicalAction`. Never catches `FirewallInternalError`."""
    if isinstance(request.tool_name, str):
        recovered, _marker, _cut = recover_name(request.tool_name)
        request = replace(request, tool_name=recovered)

    rejection = check_request(request)
    if rejection is not None:
        return Normalization(action=None, rejection=rejection, dropped_keys=0)

    kind = ActionKind(request.tool_name)
    arguments = request.arguments
    fields = FIELD_TABLE[kind]
    known_names = {field.name for field in fields}
    dropped_keys = sum(1 for key in arguments if key not in known_names)

    values, rejection = _process_fields(fields, arguments)
    if rejection is not None:
        return Normalization(action=None, rejection=rejection, dropped_keys=0)

    path_targets = []
    for field in fields:
        if field.kind == "path":
            normalized = normalize_path(values[field.name])
            values[field.name] = normalized.path
            path_targets.append(normalized.target)

    args = ARGS_FOR_KIND[kind](**values)

    capabilities = set(BASE_CAPABILITIES[kind])
    if TargetClass.OUTSIDE in path_targets:
        capabilities.add(Capability.HOST_FS)
    if TargetClass.REPO_METADATA in path_targets and kind in WRITE_KINDS:
        capabilities.add(Capability.REPO_CONTROL)

    semantic_status = SemanticStatus.UNKNOWN if kind is ActionKind.BASH else SemanticStatus.KNOWN

    action = CanonicalAction(
        schema_version=FIREWALL_SCHEMA_VERSION,
        call_id=request.call_id,
        turn=request.turn,
        kind=kind,
        args=args,
        capabilities=frozenset(capabilities),
        semantic_status=semantic_status,
    )
    return Normalization(action=action, rejection=None, dropped_keys=dropped_keys)


def canonicalize_batch(requests: "Sequence[ActionRequest]") -> "list[Normalization]":
    """Canonicalize a batch of requests in order (spec §9): the first request
    carrying a given `call_id` goes through `canonicalize` normally, whatever
    it decides; every later request whose `call_id` equals an earlier one's
    -- compared with plain `==` on the id as given, before any validation, so
    a non-string id is compared as-is -- is rejected with
    `Rejection(ReasonCode.CALL_ID_DUPLICATE, ...)` naming only its batch
    index, and is never canonicalized. Every other request is independent:
    one request's rejection never affects its neighbours. Only `str` ids
    take part: a non-string id is never a duplicate and is left to
    `check_request`, which rejects it as `call_id_invalid`, so a malformed
    id can neither crash the batch (comparing two deeply nested lists
    recurses) nor be reported as a duplicate of another malformed id."""
    results: "list[Normalization]" = []
    seen: set = set()
    for index, request in enumerate(requests):
        call_id = request.call_id
        is_duplicate = isinstance(call_id, str) and call_id in seen
        if is_duplicate:
            results.append(
                Normalization(
                    action=None,
                    rejection=Rejection(
                        ReasonCode.CALL_ID_DUPLICATE,
                        f"duplicate call_id at batch index {index}",
                    ),
                    dropped_keys=0,
                )
            )
            continue
        if isinstance(call_id, str):
            seen.add(call_id)
        results.append(canonicalize(request))
    return results
