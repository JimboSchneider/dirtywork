"""The closed reason-class and reason-code vocabularies (spec §7)."""
from __future__ import annotations

import enum

from .errors import FirewallInternalError


class ReasonClass(str, enum.Enum):
    """The four buckets every ReasonCode belongs to, for Supervisor weighting."""

    MALFORMED = "malformed"
    BOUNDS = "bounds"
    AUTHORITY = "authority"
    INTERNAL = "internal"


class ReasonCode(str, enum.Enum):
    """Closed, append-only reason vocabulary; never renamed, never reused."""

    CALL_ID_INVALID = "call_id_invalid"
    TOOL_NAME_INVALID = "tool_name_invalid"
    TOOL_UNKNOWN = "tool_unknown"
    ARGUMENTS_UNPARSEABLE = "arguments_unparseable"
    ARGUMENTS_NOT_OBJECT = "arguments_not_object"
    ARGUMENT_MISSING = "argument_missing"
    ARGUMENT_TYPE_INVALID = "argument_type_invalid"
    ARGUMENT_UNEXPECTED = "argument_unexpected"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    STRING_TOO_LONG = "string_too_long"
    COLLECTION_TOO_LARGE = "collection_too_large"
    NESTING_TOO_DEEP = "nesting_too_deep"
    NUMBER_OUT_OF_RANGE = "number_out_of_range"
    BATCH_TOO_LARGE = "batch_too_large"
    CALL_ID_DUPLICATE = "call_id_duplicate"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    REPO_PUBLISH = "repo_publish"
    REPO_CONTROL = "repo_control"
    HOST_FS_DESTRUCTIVE = "host_fs_destructive"
    REMOTE_CODE_EXEC = "remote_code_exec"
    SYSTEM_CONTROL = "system_control"
    HOST_FS_REDIRECT = "host_fs_redirect"
    HOST_FS_CHDIR = "host_fs_chdir"
    REPO_METADATA_TARGET = "repo_metadata_target"
    PATH_OUTSIDE_WORKSPACE = "path_outside_workspace"
    FIREWALL_INTERNAL_ERROR = "firewall_internal_error"


_REASON_CLASS_BY_CODE = {
    ReasonCode.CALL_ID_INVALID: ReasonClass.MALFORMED,
    ReasonCode.TOOL_NAME_INVALID: ReasonClass.MALFORMED,
    ReasonCode.TOOL_UNKNOWN: ReasonClass.MALFORMED,
    ReasonCode.ARGUMENTS_UNPARSEABLE: ReasonClass.MALFORMED,
    ReasonCode.ARGUMENTS_NOT_OBJECT: ReasonClass.MALFORMED,
    ReasonCode.ARGUMENT_MISSING: ReasonClass.MALFORMED,
    ReasonCode.ARGUMENT_TYPE_INVALID: ReasonClass.MALFORMED,
    ReasonCode.ARGUMENT_UNEXPECTED: ReasonClass.MALFORMED,
    ReasonCode.PAYLOAD_TOO_LARGE: ReasonClass.BOUNDS,
    ReasonCode.STRING_TOO_LONG: ReasonClass.BOUNDS,
    ReasonCode.COLLECTION_TOO_LARGE: ReasonClass.BOUNDS,
    ReasonCode.NESTING_TOO_DEEP: ReasonClass.BOUNDS,
    ReasonCode.NUMBER_OUT_OF_RANGE: ReasonClass.BOUNDS,
    ReasonCode.BATCH_TOO_LARGE: ReasonClass.BOUNDS,
    ReasonCode.CALL_ID_DUPLICATE: ReasonClass.BOUNDS,
    ReasonCode.PRIVILEGE_ESCALATION: ReasonClass.AUTHORITY,
    ReasonCode.REPO_PUBLISH: ReasonClass.AUTHORITY,
    ReasonCode.REPO_CONTROL: ReasonClass.AUTHORITY,
    ReasonCode.HOST_FS_DESTRUCTIVE: ReasonClass.AUTHORITY,
    ReasonCode.REMOTE_CODE_EXEC: ReasonClass.AUTHORITY,
    ReasonCode.SYSTEM_CONTROL: ReasonClass.AUTHORITY,
    ReasonCode.HOST_FS_REDIRECT: ReasonClass.AUTHORITY,
    ReasonCode.HOST_FS_CHDIR: ReasonClass.AUTHORITY,
    ReasonCode.REPO_METADATA_TARGET: ReasonClass.AUTHORITY,
    ReasonCode.PATH_OUTSIDE_WORKSPACE: ReasonClass.AUTHORITY,
    ReasonCode.FIREWALL_INTERNAL_ERROR: ReasonClass.INTERNAL,
}


def reason_class(code: ReasonCode) -> ReasonClass:
    """Total function from ReasonCode to its ReasonClass; fails closed.

    Checks membership by identity, not value equality: a str enum member
    hashes and compares equal to a plain str of the same value, so a
    look-alike string must be rejected explicitly rather than trusted to a
    dict lookup.
    """
    if not isinstance(code, ReasonCode):
        raise FirewallInternalError(f"not a ReasonCode: {code!r}")
    return _REASON_CLASS_BY_CODE[code]
