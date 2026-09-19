"""The tool-agnostic boundary validator: Rejection and check_request (spec §6)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .bounds import (
    MAX_ARGUMENT_KEYS,
    MAX_BATCH_CALLS,
    MAX_CALL_ID_CHARS,
    MAX_COLLECTION_ITEMS,
    MAX_DETAIL_CHARS,
    MAX_INT,
    MAX_NESTED_KEYS,
    MAX_NESTING_DEPTH,
    MAX_RAW_ARGUMENT_CHARS,
    MAX_STRING_CHARS,
    MAX_TOOL_NAME_CHARS,
    MIN_INT,
)
from .capabilities import ActionKind
from .errors import FirewallInternalError
from .reasons import ReasonCode
from .schema import ActionRequest, _str_field, valid_call_id


@dataclass(frozen=True)
class Rejection:
    """A request-stage denial: a ReasonCode plus harness-composed prose that
    never quotes worker-supplied text (spec §6.2)."""

    reason_code: ReasonCode
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.reason_code, ReasonCode):
            raise FirewallInternalError("reason_code must be a ReasonCode")
        _str_field(self.detail, "detail", MAX_DETAIL_CHARS)


def _check_scalar(value) -> Optional[ReasonCode]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if not (MIN_INT <= value <= MAX_INT):
            return ReasonCode.NUMBER_OUT_OF_RANGE
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return ReasonCode.NUMBER_OUT_OF_RANGE
        return None
    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            return ReasonCode.STRING_TOO_LONG
        return None
    return ReasonCode.ARGUMENT_TYPE_INVALID


def _check_key(key) -> Optional[ReasonCode]:
    if not isinstance(key, str):
        return ReasonCode.ARGUMENT_TYPE_INVALID
    return _check_scalar(key)


def _walk(node, depth: int) -> Optional[ReasonCode]:
    """Depth-first, key-order structural walk of one dict or list. `depth` is
    the container's own depth (the top-level `arguments` dict is depth 1).
    Depth and size are checked on entering a container, before its children
    are visited, so an oversized or too-deep structure is rejected without
    being traversed. Each key is checked together with its value, in order,
    so the first failure reported is the first one met."""
    if depth > MAX_NESTING_DEPTH:
        return ReasonCode.NESTING_TOO_DEEP
    if isinstance(node, dict):
        if len(node) > (MAX_ARGUMENT_KEYS if depth == 1 else MAX_NESTED_KEYS):
            return ReasonCode.COLLECTION_TOO_LARGE
        for key, child in node.items():
            rc = _check_key(key)
            if rc is None:
                rc = _child(child, depth)
            if rc is not None:
                return rc
        return None
    if len(node) > MAX_COLLECTION_ITEMS:
        return ReasonCode.COLLECTION_TOO_LARGE
    for child in node:
        rc = _child(child, depth)
        if rc is not None:
            return rc
    return None


def _child(child, depth: int) -> Optional[ReasonCode]:
    return _walk(child, depth + 1) if isinstance(child, (dict, list)) else _check_scalar(child)


_DETAIL_BY_CODE = {
    ReasonCode.BATCH_TOO_LARGE: f"batch_size exceeds {MAX_BATCH_CALLS}",
    ReasonCode.CALL_ID_INVALID: (
        f"call_id must be nonempty printable ASCII without whitespace, "
        f"<= {MAX_CALL_ID_CHARS} chars"
    ),
    ReasonCode.TOOL_NAME_INVALID: f"tool_name must be nonempty, <= {MAX_TOOL_NAME_CHARS} chars",
    ReasonCode.TOOL_UNKNOWN: "tool_name is not a known action kind",
    ReasonCode.ARGUMENTS_UNPARSEABLE: "arguments could not be parsed",
    ReasonCode.PAYLOAD_TOO_LARGE: f"raw_chars exceeds {MAX_RAW_ARGUMENT_CHARS}",
    ReasonCode.ARGUMENTS_NOT_OBJECT: "arguments must be a JSON object",
    ReasonCode.NESTING_TOO_DEEP: f"nesting exceeds depth {MAX_NESTING_DEPTH}",
    ReasonCode.COLLECTION_TOO_LARGE: "a collection exceeds its key or item limit",
    ReasonCode.ARGUMENT_TYPE_INVALID: "an argument has an unsupported type",
    ReasonCode.STRING_TOO_LONG: f"a string exceeds {MAX_STRING_CHARS} chars",
    ReasonCode.NUMBER_OUT_OF_RANGE: "a number is outside the supported range",
}


def _reject(code: ReasonCode) -> Rejection:
    return Rejection(reason_code=code, detail=_DETAIL_BY_CODE[code])


def check_request(request: ActionRequest) -> Optional[Rejection]:
    """Tool-agnostic structural validator (spec §6.2). Checks run in this
    fixed order and stop at the first failure."""
    # 1. batch size
    if request.batch_size > MAX_BATCH_CALLS:
        return _reject(ReasonCode.BATCH_TOO_LARGE)

    # 2. call_id
    if not valid_call_id(request.call_id):
        return _reject(ReasonCode.CALL_ID_INVALID)

    # 3. tool_name shape
    tool_name = request.tool_name
    if not isinstance(tool_name, str) or not 0 < len(tool_name) <= MAX_TOOL_NAME_CHARS:
        return _reject(ReasonCode.TOOL_NAME_INVALID)

    # 4. tool_name known
    try:
        ActionKind(tool_name)
    except ValueError:
        return _reject(ReasonCode.TOOL_UNKNOWN)

    # 5. parseable
    if request.parse_error is not None or request.arguments is None:
        return _reject(ReasonCode.ARGUMENTS_UNPARSEABLE)

    # 6. payload size
    if request.raw_chars > MAX_RAW_ARGUMENT_CHARS:
        return _reject(ReasonCode.PAYLOAD_TOO_LARGE)

    # 7. arguments is an object
    if not isinstance(request.arguments, dict):
        return _reject(ReasonCode.ARGUMENTS_NOT_OBJECT)

    # 8. structural walk
    code = _walk(request.arguments, 1)
    if code is not None:
        return _reject(code)

    return None
