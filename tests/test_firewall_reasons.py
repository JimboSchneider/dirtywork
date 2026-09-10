from __future__ import annotations

import pytest

from dirtywork.firewall.errors import FirewallInternalError
from dirtywork.firewall.reasons import ReasonClass, ReasonCode, reason_class

EXPECTED_REASON_CLASSES = ["authority", "bounds", "internal", "malformed"]

EXPECTED_REASON_CODES = sorted(
    [
        "call_id_invalid",
        "tool_name_invalid",
        "tool_unknown",
        "arguments_unparseable",
        "arguments_not_object",
        "argument_missing",
        "argument_type_invalid",
        "argument_unexpected",
        "payload_too_large",
        "string_too_long",
        "collection_too_large",
        "nesting_too_deep",
        "number_out_of_range",
        "batch_too_large",
        "call_id_duplicate",
        "privilege_escalation",
        "repo_publish",
        "repo_control",
        "host_fs_destructive",
        "remote_code_exec",
        "system_control",
        "host_fs_redirect",
        "host_fs_chdir",
        "repo_metadata_target",
        "path_outside_workspace",
        "firewall_internal_error",
    ]
)

MALFORMED = [
    ReasonCode.CALL_ID_INVALID,
    ReasonCode.TOOL_NAME_INVALID,
    ReasonCode.TOOL_UNKNOWN,
    ReasonCode.ARGUMENTS_UNPARSEABLE,
    ReasonCode.ARGUMENTS_NOT_OBJECT,
    ReasonCode.ARGUMENT_MISSING,
    ReasonCode.ARGUMENT_TYPE_INVALID,
    ReasonCode.ARGUMENT_UNEXPECTED,
]
BOUNDS = [
    ReasonCode.PAYLOAD_TOO_LARGE,
    ReasonCode.STRING_TOO_LONG,
    ReasonCode.COLLECTION_TOO_LARGE,
    ReasonCode.NESTING_TOO_DEEP,
    ReasonCode.NUMBER_OUT_OF_RANGE,
    ReasonCode.BATCH_TOO_LARGE,
    ReasonCode.CALL_ID_DUPLICATE,
]
AUTHORITY = [
    ReasonCode.PRIVILEGE_ESCALATION,
    ReasonCode.REPO_PUBLISH,
    ReasonCode.REPO_CONTROL,
    ReasonCode.HOST_FS_DESTRUCTIVE,
    ReasonCode.REMOTE_CODE_EXEC,
    ReasonCode.SYSTEM_CONTROL,
    ReasonCode.HOST_FS_REDIRECT,
    ReasonCode.HOST_FS_CHDIR,
    ReasonCode.REPO_METADATA_TARGET,
    ReasonCode.PATH_OUTSIDE_WORKSPACE,
]
INTERNAL = [ReasonCode.FIREWALL_INTERNAL_ERROR]


def test_reason_class_vocabulary_pin():
    assert sorted(m.value for m in ReasonClass) == EXPECTED_REASON_CLASSES


def test_reason_code_vocabulary_pin():
    assert sorted(m.value for m in ReasonCode) == EXPECTED_REASON_CODES
    assert len(ReasonCode) == 26


def test_reason_class_totality_and_counts():
    assert len(MALFORMED) == 8
    assert len(BOUNDS) == 7
    assert len(AUTHORITY) == 10
    assert len(INTERNAL) == 1
    assert len(MALFORMED) + len(BOUNDS) + len(AUTHORITY) + len(INTERNAL) == len(ReasonCode)

    for code in ReasonCode:
        assert code in MALFORMED + BOUNDS + AUTHORITY + INTERNAL

    for code in MALFORMED:
        assert reason_class(code) is ReasonClass.MALFORMED
    for code in BOUNDS:
        assert reason_class(code) is ReasonClass.BOUNDS
    for code in AUTHORITY:
        assert reason_class(code) is ReasonClass.AUTHORITY
    for code in INTERNAL:
        assert reason_class(code) is ReasonClass.INTERNAL


def test_reason_class_rejects_bare_str_lookalike():
    with pytest.raises(FirewallInternalError):
        reason_class("tool_unknown")
