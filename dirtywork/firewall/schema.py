"""ActionRequest, the canonical action shapes, and the Firewall's identity
and event types (spec §3, §4, §8, §9)."""
from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional, Union

from .bounds import (
    FIREWALL_SCHEMA_VERSION,
    IDENTITY_VERSION,
    MAX_BASH_TIMEOUT,
    MAX_CALL_ID_CHARS,
    MAX_COLLECTION_ITEMS,
    MAX_COMMAND_CHARS,
    MAX_DETAIL_CHARS,
    MAX_GLOB_CHARS,
    MAX_INT,
    MAX_PATH_CHARS,
    MAX_PATTERN_CHARS,
    MAX_STRING_CHARS,
    MAX_SUMMARY_CHARS,
    MAX_TOOL_NAME_CHARS,
)
from .capabilities import ActionKind, Capability, BASE_CAPABILITIES
from .errors import FirewallInternalError
from .reasons import ReasonClass, ReasonCode, reason_class


@dataclass(frozen=True)
class ActionRequest:
    """The only way worker input enters the Firewall (spec §3)."""

    call_id: str
    tool_name: str
    arguments: Any
    parse_error: Optional[str]
    raw_chars: int
    turn: int
    batch_index: int
    batch_size: int

    @classmethod
    def from_tool_call(cls, tc, *, turn, batch_index, batch_size) -> "ActionRequest":
        """Copies the ToolCall fields verbatim; performs no validation."""
        return cls(
            call_id=tc.id,
            tool_name=tc.name,
            arguments=tc.arguments,
            parse_error=tc.error,
            raw_chars=len(tc.raw_arguments or ""),
            turn=turn,
            batch_index=batch_index,
            batch_size=batch_size,
        )


def valid_call_id(value: Any) -> bool:
    """Check 2 of spec §6.2: nonempty str, <= MAX_CALL_ID_CHARS, printable
    ASCII with no whitespace (every char in 0x21..0x7e). Reused by request.py."""
    if not isinstance(value, str) or not value or len(value) > MAX_CALL_ID_CHARS:
        return False
    return all(0x21 <= ord(ch) <= 0x7E for ch in value)


def _str_field(value: Any, name: str, limit: int, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str):
        raise FirewallInternalError(f"{name} must be a str")
    if len(value) > limit:
        raise FirewallInternalError(f"{name} exceeds {limit} chars")


def _int_field(value: Any, name: str, lo: int, hi: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FirewallInternalError(f"{name} must be an int")
    if not (lo <= value <= hi):
        raise FirewallInternalError(f"{name} must be in [{lo}, {hi}]")


def _tuple_field(value: Any, name: str, item_type: type, lo: int, hi: int) -> None:
    if not isinstance(value, tuple):
        raise FirewallInternalError(f"{name} must be a tuple")
    if not (lo <= len(value) <= hi):
        raise FirewallInternalError(f"{name} must have {lo}..{hi} items")
    for item in value:
        if not isinstance(item, item_type):
            raise FirewallInternalError(f"{name} items must be {item_type.__name__}")


@dataclass(frozen=True)
class Edit:
    old: str
    new: str

    def __post_init__(self) -> None:
        _str_field(self.old, "old", MAX_STRING_CHARS)
        if not self.old:
            raise FirewallInternalError("Edit.old must be nonempty")
        _str_field(self.new, "new", MAX_STRING_CHARS)


@dataclass(frozen=True)
class ReadFileArgs:
    path: str
    offset: int
    limit: int

    def __post_init__(self) -> None:
        _str_field(self.path, "path", MAX_PATH_CHARS)
        _int_field(self.offset, "offset", 0, MAX_INT)
        _int_field(self.limit, "limit", 1, MAX_INT)


@dataclass(frozen=True)
class WriteFileArgs:
    path: str
    content: str

    def __post_init__(self) -> None:
        _str_field(self.path, "path", MAX_PATH_CHARS)
        _str_field(self.content, "content", MAX_STRING_CHARS)


@dataclass(frozen=True)
class AppendFileArgs:
    path: str
    text: str

    def __post_init__(self) -> None:
        _str_field(self.path, "path", MAX_PATH_CHARS)
        _str_field(self.text, "text", MAX_STRING_CHARS)


@dataclass(frozen=True)
class EditFileArgs:
    path: str
    old_string: str
    new_string: str

    def __post_init__(self) -> None:
        _str_field(self.path, "path", MAX_PATH_CHARS)
        _str_field(self.old_string, "old_string", MAX_STRING_CHARS)
        _str_field(self.new_string, "new_string", MAX_STRING_CHARS)


@dataclass(frozen=True)
class ApplyEditsArgs:
    path: str
    edits: "tuple[Edit, ...]"

    def __post_init__(self) -> None:
        _str_field(self.path, "path", MAX_PATH_CHARS)
        _tuple_field(self.edits, "edits", Edit, 1, MAX_COLLECTION_ITEMS)


@dataclass(frozen=True)
class InsertArgs:
    path: str
    anchor: str
    text: str

    def __post_init__(self) -> None:
        _str_field(self.path, "path", MAX_PATH_CHARS)
        _str_field(self.anchor, "anchor", MAX_STRING_CHARS)
        _str_field(self.text, "text", MAX_STRING_CHARS)


@dataclass(frozen=True)
class ListDirArgs:
    path: str

    def __post_init__(self) -> None:
        _str_field(self.path, "path", MAX_PATH_CHARS)


@dataclass(frozen=True)
class GrepArgs:
    pattern: str
    path: str
    glob: Optional[str]

    def __post_init__(self) -> None:
        _str_field(self.pattern, "pattern", MAX_PATTERN_CHARS)
        _str_field(self.path, "path", MAX_PATH_CHARS)
        _str_field(self.glob, "glob", MAX_GLOB_CHARS, nullable=True)


@dataclass(frozen=True)
class BashArgs:
    command: str
    timeout: int

    def __post_init__(self) -> None:
        _str_field(self.command, "command", MAX_COMMAND_CHARS)
        _int_field(self.timeout, "timeout", 1, MAX_BASH_TIMEOUT)


@dataclass(frozen=True)
class FinishArgs:
    summary: str

    def __post_init__(self) -> None:
        _str_field(self.summary, "summary", MAX_SUMMARY_CHARS)


CanonicalArgs = Union[
    ReadFileArgs,
    WriteFileArgs,
    AppendFileArgs,
    EditFileArgs,
    ApplyEditsArgs,
    InsertArgs,
    ListDirArgs,
    GrepArgs,
    BashArgs,
    FinishArgs,
]

ARGS_FOR_KIND: "dict[ActionKind, type]" = {
    ActionKind.READ_FILE: ReadFileArgs,
    ActionKind.WRITE_FILE: WriteFileArgs,
    ActionKind.APPEND_FILE: AppendFileArgs,
    ActionKind.EDIT_FILE: EditFileArgs,
    ActionKind.APPLY_EDITS: ApplyEditsArgs,
    ActionKind.INSERT_BEFORE: InsertArgs,
    ActionKind.INSERT_AFTER: InsertArgs,
    ActionKind.LIST_DIR: ListDirArgs,
    ActionKind.GREP: GrepArgs,
    ActionKind.BASH: BashArgs,
    ActionKind.FINISH: FinishArgs,
}


class Decision(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"


class SemanticStatus(str, enum.Enum):
    KNOWN = "semantic_known"
    UNKNOWN = "semantic_unknown"


@dataclass(frozen=True)
class CanonicalAction:
    """The immutable, policy-relevant canonicalization of one worker action
    (spec §4.3)."""

    schema_version: int
    call_id: str
    turn: int
    kind: ActionKind
    args: CanonicalArgs
    capabilities: "frozenset[Capability]"
    semantic_status: SemanticStatus

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ActionKind):
            raise FirewallInternalError("kind must be an ActionKind member")
        if not isinstance(self.semantic_status, SemanticStatus):
            raise FirewallInternalError("semantic_status must be a SemanticStatus member")
        if type(self.args) is not ARGS_FOR_KIND[self.kind]:
            raise FirewallInternalError("args type does not match kind")
        if not valid_call_id(self.call_id):
            raise FirewallInternalError("call_id invalid")
        _int_field(self.turn, "turn", 1, MAX_INT)
        if not isinstance(self.capabilities, frozenset) or not self.capabilities:
            raise FirewallInternalError("capabilities must be a nonempty frozenset")
        for cap in self.capabilities:
            if not isinstance(cap, Capability):
                raise FirewallInternalError("capabilities members must be Capability")
        if not BASE_CAPABILITIES[self.kind] <= self.capabilities:
            raise FirewallInternalError("capabilities missing the kind's base set")
        if self.schema_version != FIREWALL_SCHEMA_VERSION:
            raise FirewallInternalError("schema_version mismatch")


@dataclass(frozen=True)
class PolicyDecision:
    """The Firewall's decision on one action or rejection (spec §8)."""

    decision: Decision
    reason_code: Optional[ReasonCode]
    detail: str

    def __post_init__(self) -> None:
        allow_ok = self.decision is Decision.ALLOW and self.reason_code is None
        deny_ok = self.decision is Decision.DENY and isinstance(self.reason_code, ReasonCode)
        if not (allow_ok or deny_ok):
            raise FirewallInternalError(
                "PolicyDecision must be ALLOW+None or DENY+ReasonCode"
            )
        _str_field(self.detail, "detail", MAX_DETAIL_CHARS)


IDENTITY_FIELDS: "dict[ActionKind, tuple]" = {
    ActionKind.READ_FILE: ("path",),
    ActionKind.WRITE_FILE: ("path",),
    ActionKind.APPEND_FILE: ("path",),
    ActionKind.EDIT_FILE: ("path",),
    ActionKind.APPLY_EDITS: ("path",),
    ActionKind.INSERT_BEFORE: ("path",),
    ActionKind.INSERT_AFTER: ("path",),
    ActionKind.LIST_DIR: ("path",),
    ActionKind.GREP: ("pattern", "path", "glob"),
    ActionKind.BASH: ("command",),
    ActionKind.FINISH: (),
}


def _digest(lines: list) -> str:
    """`surrogatepass` keeps hashing total: a raw tool name may carry a lone
    surrogate that strict UTF-8 refuses to encode, and a rejection must still
    get an identity."""
    text = f"dirtywork-firewall-identity/{IDENTITY_VERSION}\n" + "".join(line + "\n" for line in lines)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def action_identity(action: CanonicalAction) -> str:
    """SHA-256 hex of the versioned, policy-relevant identity of an action
    (spec §9.1)."""
    lines = [action.kind.value]
    for field_name in IDENTITY_FIELDS[action.kind]:
        value = getattr(action.args, field_name)
        lines.append(f"{field_name}={_json(value)}")
    return _digest(lines)


def rejection_identity(request: ActionRequest, rejection: Any) -> str:
    """SHA-256 hex identity of a request-stage rejection (spec §9.1).

    Coarse by design: only the reason code and the raw tool name are hashed,
    because a rejected request has no canonical target and parsing its
    arguments for one would be a second, untrusted normalization. It is an
    audit key, not an exact-equivalent denial key; request-stage events
    count toward total denial volume only (spec §9.2).

    `rejection` is duck-typed (only `.reason_code` is used) because
    `Rejection` lives in request.py, which imports this module.
    """
    reason_code = getattr(rejection, "reason_code", None)
    if not isinstance(reason_code, ReasonCode):
        raise FirewallInternalError("rejection.reason_code must be a ReasonCode")
    name = request.tool_name
    if not (isinstance(name, str) and 0 < len(name) <= MAX_TOOL_NAME_CHARS):
        name = ""
    return _digest(["rejection", reason_code.value, name])


@dataclass(frozen=True)
class FirewallEvent:
    """One event shape covering both a request-stage rejection and an
    action-stage decision (spec §9.2)."""

    schema_version: int
    stage: str
    turn: int
    call_id: str
    kind: Optional[ActionKind]
    capabilities: "tuple[str, ...]"
    decision: Decision
    reason_code: Optional[ReasonCode]
    reason_class: Optional[ReasonClass]
    action_identity: str
    semantic_status: Optional[SemanticStatus]

    def __post_init__(self) -> None:
        if self.schema_version != FIREWALL_SCHEMA_VERSION:
            raise FirewallInternalError("schema_version mismatch")
        if not isinstance(self.decision, Decision):
            raise FirewallInternalError("decision must be a Decision member")
        if self.kind is not None and not isinstance(self.kind, ActionKind):
            raise FirewallInternalError("kind must be an ActionKind member or None")
        if self.semantic_status is not None and not isinstance(
            self.semantic_status, SemanticStatus
        ):
            raise FirewallInternalError("semantic_status must be a SemanticStatus member or None")
        if self.reason_code is not None and not isinstance(self.reason_code, ReasonCode):
            raise FirewallInternalError("reason_code must be a ReasonCode member or None")
        if self.reason_class is not None and not isinstance(self.reason_class, ReasonClass):
            raise FirewallInternalError("reason_class must be a ReasonClass member or None")
        _tuple_field(self.capabilities, "capabilities", str, 0, len(Capability))
        for cap in self.capabilities:
            try:
                Capability(cap)
            except ValueError:
                raise FirewallInternalError("capabilities items must be Capability values") from None
        if self.stage == "request":
            if self.decision is not Decision.DENY:
                raise FirewallInternalError("request-stage event must be DENY")
            if self.kind is not None:
                raise FirewallInternalError("request-stage event must have kind=None")
            if self.capabilities != ():
                raise FirewallInternalError("request-stage event must have capabilities=()")
            if self.semantic_status is not None:
                raise FirewallInternalError("request-stage event must have semantic_status=None")
        elif self.stage == "action":
            if self.kind is None or self.semantic_status is None:
                raise FirewallInternalError("action-stage event requires kind and semantic_status")
            if not self.capabilities:
                raise FirewallInternalError("action-stage event requires nonempty capabilities")
        else:
            raise FirewallInternalError("stage must be 'request' or 'action'")

        allow = self.decision is Decision.ALLOW
        if allow != (self.reason_code is None):
            raise FirewallInternalError("reason_code is None iff decision is ALLOW")
        if allow != (self.reason_class is None):
            raise FirewallInternalError("reason_class is None iff decision is ALLOW")
        if self.reason_code is not None and self.reason_class is not reason_class(self.reason_code):
            raise FirewallInternalError("reason_class must be reason_class(reason_code)")

    @classmethod
    def from_action(cls, action: CanonicalAction, policy: PolicyDecision) -> "FirewallEvent":
        return cls(
            schema_version=FIREWALL_SCHEMA_VERSION,
            stage="action",
            turn=action.turn,
            call_id=action.call_id,
            kind=action.kind,
            capabilities=tuple(sorted(c.value for c in action.capabilities)),
            decision=policy.decision,
            reason_code=policy.reason_code,
            reason_class=(
                None if policy.reason_code is None else reason_class(policy.reason_code)
            ),
            action_identity=action_identity(action),
            semantic_status=action.semantic_status,
        )

    @classmethod
    def from_rejection(cls, request: ActionRequest, rejection: Any) -> "FirewallEvent":
        reason_code = rejection.reason_code
        return cls(
            schema_version=FIREWALL_SCHEMA_VERSION,
            stage="request",
            turn=request.turn,
            call_id=request.call_id if valid_call_id(request.call_id) else "",
            kind=None,
            capabilities=(),
            decision=Decision.DENY,
            reason_code=reason_code,
            reason_class=reason_class(reason_code),
            action_identity=rejection_identity(request, rejection),
            semantic_status=None,
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "turn": self.turn,
            "call_id": self.call_id,
            "kind": None if self.kind is None else self.kind.value,
            "capabilities": sorted(self.capabilities),
            "decision": self.decision.value,
            "reason_code": None if self.reason_code is None else self.reason_code.value,
            "reason_class": None if self.reason_class is None else self.reason_class.value,
            "action_identity": self.action_identity,
            "semantic_status": None if self.semantic_status is None else self.semantic_status.value,
        }
