from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from dirtywork.firewall import bounds
from dirtywork.firewall.errors import FirewallInternalError
from dirtywork.firewall.reasons import ReasonCode
from dirtywork.firewall.request import Rejection, check_request
from dirtywork.firewall.schema import ActionRequest


def _request(**overrides):
    kwargs = dict(
        call_id="call_1",
        tool_name="read_file",
        arguments={"path": "a"},
        parse_error=None,
        raw_chars=12,
        turn=1,
        batch_index=0,
        batch_size=1,
    )
    kwargs.update(overrides)
    return ActionRequest(**kwargs)


# --- group 9 (Rejection invariants) ----------------------------------------


def test_rejection_valid_construction():
    r = Rejection(reason_code=ReasonCode.TOOL_UNKNOWN, detail="tool_name is not known")
    assert r.reason_code is ReasonCode.TOOL_UNKNOWN


def test_rejection_bad_reason_code_raises():
    with pytest.raises(FirewallInternalError):
        Rejection(reason_code="tool_unknown", detail="")


def test_rejection_detail_too_long_raises():
    with pytest.raises(FirewallInternalError):
        Rejection(reason_code=ReasonCode.TOOL_UNKNOWN, detail="x" * (bounds.MAX_DETAIL_CHARS + 1))


def test_rejection_detail_not_str_raises():
    with pytest.raises(FirewallInternalError):
        Rejection(reason_code=ReasonCode.TOOL_UNKNOWN, detail=None)


# --- group 6: check_request malformed input, one case per code ------------


def test_batch_too_large():
    r = check_request(_request(batch_size=33))
    assert r.reason_code is ReasonCode.BATCH_TOO_LARGE


def test_call_id_empty():
    r = check_request(_request(call_id=""))
    assert r.reason_code is ReasonCode.CALL_ID_INVALID


def test_call_id_oversized():
    r = check_request(_request(call_id="a" * (bounds.MAX_CALL_ID_CHARS + 1)))
    assert r.reason_code is ReasonCode.CALL_ID_INVALID


def test_call_id_whitespace():
    r = check_request(_request(call_id="call 1"))
    assert r.reason_code is ReasonCode.CALL_ID_INVALID


def test_tool_name_empty():
    r = check_request(_request(tool_name=""))
    assert r.reason_code is ReasonCode.TOOL_NAME_INVALID


def test_tool_name_oversized():
    r = check_request(_request(tool_name="a" * (bounds.MAX_TOOL_NAME_CHARS + 1)))
    assert r.reason_code is ReasonCode.TOOL_NAME_INVALID


def test_tool_name_unknown():
    r = check_request(_request(tool_name="read_files"))
    assert r.reason_code is ReasonCode.TOOL_UNKNOWN


def test_tool_name_unknown_marker_polluted():
    r = check_request(_request(tool_name="functions.read_file"))
    assert r.reason_code is ReasonCode.TOOL_UNKNOWN


def test_parse_error_set():
    r = check_request(_request(parse_error="bad json"))
    assert r.reason_code is ReasonCode.ARGUMENTS_UNPARSEABLE


def test_arguments_none():
    r = check_request(_request(arguments=None))
    assert r.reason_code is ReasonCode.ARGUMENTS_UNPARSEABLE


def test_raw_chars_over():
    r = check_request(_request(raw_chars=bounds.MAX_RAW_ARGUMENT_CHARS + 1))
    assert r.reason_code is ReasonCode.PAYLOAD_TOO_LARGE


def test_arguments_is_a_list():
    r = check_request(_request(arguments=["a"]))
    assert r.reason_code is ReasonCode.ARGUMENTS_NOT_OBJECT


def test_arguments_is_a_str():
    r = check_request(_request(arguments="a"))
    assert r.reason_code is ReasonCode.ARGUMENTS_NOT_OBJECT


def test_arguments_is_an_int():
    r = check_request(_request(arguments=1))
    assert r.reason_code is ReasonCode.ARGUMENTS_NOT_OBJECT


def test_depth_5():
    r = check_request(_request(arguments={"a": [[[{}]]]}))
    assert r.reason_code is ReasonCode.NESTING_TOO_DEEP


def test_33_top_level_keys():
    args = {f"k{i}": i for i in range(33)}
    r = check_request(_request(arguments=args))
    assert r.reason_code is ReasonCode.COLLECTION_TOO_LARGE


def test_nested_object_with_9_keys():
    nested = {f"k{i}": i for i in range(9)}
    r = check_request(_request(arguments={"path": nested}))
    assert r.reason_code is ReasonCode.COLLECTION_TOO_LARGE


def test_101_item_list():
    r = check_request(_request(arguments={"edits": list(range(101))}))
    assert r.reason_code is ReasonCode.COLLECTION_TOO_LARGE


def test_non_str_key():
    r = check_request(_request(arguments={1: "a"}))
    assert r.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_none_key_top_level():
    r = check_request(_request(arguments={None: "a"}))
    assert r.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_none_key_nested():
    r = check_request(_request(arguments={"path": {None: "a"}}))
    assert r.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_string_too_long():
    r = check_request(_request(arguments={"path": "a" * (bounds.MAX_STRING_CHARS + 1)}))
    assert r.reason_code is ReasonCode.STRING_TOO_LONG


def test_key_too_long():
    r = check_request(_request(arguments={"k" * (bounds.MAX_STRING_CHARS + 1): "a"}))
    assert r.reason_code is ReasonCode.STRING_TOO_LONG


def test_int_out_of_range():
    r = check_request(_request(arguments={"offset": 2**31}))
    assert r.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


def test_float_infinite():
    r = check_request(_request(arguments={"offset": float("inf")}))
    assert r.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


def test_bytes_value():
    r = check_request(_request(arguments={"path": b"a"}))
    assert r.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_apply_edits_100_edits_depth_3_well_formed():
    edits = [{"old": "x", "new": "y"} for _ in range(100)]
    r = check_request(
        _request(tool_name="apply_edits", arguments={"path": "a", "edits": edits})
    )
    assert r is None


def test_glob_none_returns_none():
    r = check_request(_request(tool_name="grep", arguments={"pattern": "x", "glob": None}))
    assert r is None


def test_null_extra_returns_none():
    r = check_request(_request(arguments={"path": "a", "extra": None}))
    assert r is None


# --- group 7: first-failure order ------------------------------------------


def test_unknown_tool_and_oversized_reports_tool_unknown():
    r = check_request(
        _request(
            tool_name="not_a_tool",
            raw_chars=bounds.MAX_RAW_ARGUMENT_CHARS + 1,
        )
    )
    assert r.reason_code is ReasonCode.TOOL_UNKNOWN


def test_bad_value_before_later_bad_key_reports_the_value():
    r = check_request(_request(arguments={"first": float("inf"), 1: "a"}))
    assert r.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


def test_nonobject_and_oversized_reports_payload_too_large():
    r = check_request(
        _request(
            arguments="not an object",
            raw_chars=bounds.MAX_RAW_ARGUMENT_CHARS + 1,
        )
    )
    assert r.reason_code is ReasonCode.PAYLOAD_TOO_LARGE


# --- group 8: rejection detail never contains worker-supplied text --------


def test_detail_never_contains_worker_supplied_text():
    sentinel = "SENTINEL_VALUE_ZZYZX"
    r = check_request(
        _request(
            tool_name=sentinel,
            arguments={sentinel: sentinel},
        )
    )
    assert r is not None
    assert sentinel not in r.detail
    assert len(r.detail) <= bounds.MAX_DETAIL_CHARS


def test_detail_sentinel_absent_in_structural_rejection():
    sentinel = "OTHER_SENTINEL_1234"
    r = check_request(_request(arguments={sentinel: "a" * (bounds.MAX_STRING_CHARS + 1)}))
    assert r is not None
    assert sentinel not in r.detail
    assert len(r.detail) <= bounds.MAX_DETAIL_CHARS


# --- group 16: isolation ----------------------------------------------------


def test_isolation_no_executor_modules_imported():
    repo_root = str(pathlib.Path(__file__).resolve().parent.parent)
    code = (
        "import sys, dirtywork.firewall; "
        "bad = [m for m in ('dirtywork.tools','dirtywork.builtin_tools',"
        "'dirtywork.guardrails','dirtywork.runner','dirtywork.sandbox') "
        "if m in sys.modules]; "
        "sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env={"PYTHONPATH": repo_root, "PATH": os.environ.get("PATH", "")},
    )
    assert proc.returncode == 0


# --- __all__ / package re-export completeness ------------------------------


def test_package_all_matches_spec_and_every_name_resolves():
    import dirtywork.firewall as fw

    expected = [
        "FIREWALL_SCHEMA_VERSION", "IDENTITY_VERSION",
        "FirewallInternalError",
        "ReasonClass", "ReasonCode", "reason_class",
        "ActionKind", "Capability", "BASE_CAPABILITIES", "LEGACY_RULES", "FILE_TARGET_RULES",
        "ActionRequest", "Edit", "ReadFileArgs", "WriteFileArgs", "AppendFileArgs", "EditFileArgs",
        "ApplyEditsArgs", "InsertArgs", "ListDirArgs", "GrepArgs", "BashArgs", "FinishArgs",
        "CanonicalArgs", "CanonicalAction", "Decision", "SemanticStatus", "PolicyDecision",
        "FirewallEvent", "action_identity", "rejection_identity",
        "Rejection", "check_request",
        "Normalization", "canonicalize", "canonicalize_batch", "recover_name",
        "NormalizedPath", "TargetClass", "normalize_path",
        "PolicyContext", "Verdict", "Outcome", "evaluate", "decide", "decide_batch",
        "SHELL_RULES", "analyze_command",
    ]
    assert fw.__all__ == expected
    for name in expected:
        assert hasattr(fw, name), f"{name} does not resolve on the package"
