"""Pins `canonicalize` to the registry's VALIDATION step,
`dirtywork.toolspec._validate_args(spec, args)` -- not `ToolRegistry.execute`,
which additionally clamps `timeout`, applies the run deadline and byte caps,
and runs the tool (spec §12, §13)."""
from __future__ import annotations

import json

import pytest

from dirtywork import toolspec
from dirtywork.builtin_tools import default_registry
from dirtywork.firewall.bounds import MAX_GLOB_CHARS, MAX_PATH_CHARS, MAX_PATTERN_CHARS
from dirtywork.firewall.normalize import canonicalize
from dirtywork.firewall.reasons import ReasonCode
from dirtywork.firewall.schema import ActionRequest, Edit

_reg = default_registry()


def _spec(tool):
    return _reg.spec(tool)


def _req(tool, args, call_id="call_1", turn=1, batch_index=0, batch_size=1):
    return ActionRequest(
        call_id=call_id,
        tool_name=tool,
        arguments=args,
        parse_error=None,
        raw_chars=len(json.dumps(args)),
        turn=turn,
        batch_index=batch_index,
        batch_size=batch_size,
    )


def _registry(tool, args):
    """("accept", call_args) or ("reject", None), catching ToolValidationError."""
    try:
        call_args = toolspec._validate_args(_spec(tool), args)
    except toolspec.ToolValidationError:
        return "reject", None
    return "accept", call_args


def _firewall(tool, args):
    """("accept", action) or ("reject", rejection)."""
    result = canonicalize(_req(tool, args))
    if result.rejection is not None:
        return "reject", result.rejection
    return "accept", result.action


def _norm_path(value):
    """The registry does not normalize paths at validation; drop '.' and
    empty components the same way normalize_path does, so a registry path
    string compares equal to the canonical one (spec §12)."""
    parts = [p for p in value.split("/") if p not in ("", ".")]
    joined = "/".join(parts)
    if value.startswith("/"):
        return "/" + joined if joined else "/"
    return joined if joined else "."


def _assert_values_equal(reg_args, action):
    for key, reg_value in reg_args.items():
        fw_value = getattr(action.args, key)
        if key == "path":
            assert _norm_path(reg_value) == fw_value
        elif key == "edits":
            expected = tuple(Edit(old=d["old"], new=d["new"]) for d in reg_value)
            assert expected == fw_value
        else:
            assert reg_value == fw_value


# --- 1. shared-domain corpus: same accept, equal values ----------------------

_SHARED_DOMAIN_CASES = [
    ("read_file", {"path": "a"}),
    ("read_file", {"path": "a", "offset": 0, "limit": 400}),
    ("read_file", {"path": "a", "offset": "5", "limit": "1_0"}),
    ("read_file", {"path": "a", "bogus": 1}),
    ("read_file", {"path": "a", "e1": 1, "e2": 2, "e3": 3}),
    ("write_file", {"path": "a", "content": "c"}),
    ("write_file", {"path": "a", "content": "c", "bogus": 1}),
    ("write_file", {"path": "a", "content": "c", "e1": 1, "e2": 2, "e3": 3}),
    ("append_file", {"path": "a", "text": "t"}),
    ("append_file", {"path": "a", "text": "t", "bogus": 1}),
    ("append_file", {"path": "a", "text": "t", "e1": 1, "e2": 2, "e3": 3}),
    ("edit_file", {"path": "a", "old_string": "o", "new_string": "n"}),
    ("edit_file", {"path": "a", "old_string": "o", "new_string": "n", "bogus": 1}),
    ("edit_file", {"path": "a", "old_string": "o", "new_string": "n", "e1": 1, "e2": 2, "e3": 3}),
    ("apply_edits", {"path": "a", "edits": [{"old": "o", "new": "n"}]}),
    (
        "apply_edits",
        {
            "path": "a",
            "edits": [
                {"old": "o1", "new": "n1"},
                {"old": "o2", "new": "n2"},
                {"old": "o3", "new": "n3"},
            ],
        },
    ),
    ("apply_edits", {"path": "a", "edits": [{"old": "o", "new": "n"}], "bogus": 1}),
    (
        "apply_edits",
        {"path": "a", "edits": [{"old": "o", "new": "n"}], "e1": 1, "e2": 2, "e3": 3},
    ),
    ("insert_before", {"path": "a", "anchor": "x", "text": "t"}),
    ("insert_before", {"path": "a", "anchor": "x", "text": "t", "bogus": 1}),
    ("insert_before", {"path": "a", "anchor": "x", "text": "t", "e1": 1, "e2": 2, "e3": 3}),
    ("insert_after", {"path": "a", "anchor": "x", "text": "t"}),
    ("insert_after", {"path": "a", "anchor": "x", "text": "t", "bogus": 1}),
    ("insert_after", {"path": "a", "anchor": "x", "text": "t", "e1": 1, "e2": 2, "e3": 3}),
    ("list_dir", {}),
    ("list_dir", {"path": "."}),
    ("list_dir", {"bogus": 1}),
    ("list_dir", {"e1": 1, "e2": 2, "e3": 3}),
    ("grep", {"pattern": "p"}),
    ("grep", {"pattern": "p", "path": ".", "glob": None}),
    ("grep", {"pattern": "p", "glob": None}),
    ("grep", {"pattern": "p", "bogus": 1}),
    ("grep", {"pattern": "p", "timeout": 30}),
    ("grep", {"pattern": "p", "e1": 1, "e2": 2, "e3": 3}),
    ("bash", {"command": "ls"}),
    ("bash", {"command": "ls", "timeout": 120}),
    ("bash", {"command": "ls", "timeout": "60"}),
    ("bash", {"command": "ls", "timeout": "2m"}),
    ("bash", {"command": "ls", "timeout": "2 MIN"}),
    ("bash", {"command": "ls", "bogus": 1}),
    ("bash", {"command": "ls", "e1": 1, "e2": 2, "e3": 3}),
    ("finish", {"summary": "done"}),
    ("finish", {"summary": ""}),
    ("finish", {"summary": "done", "bogus": 1}),
    ("finish", {"summary": "done", "e1": 1, "e2": 2, "e3": 3}),
]
_SHARED_DOMAIN_IDS = [f"{i}:{tool}:{sorted(args)}" for i, (tool, args) in enumerate(_SHARED_DOMAIN_CASES)]


@pytest.mark.parametrize("tool,args", _SHARED_DOMAIN_CASES, ids=_SHARED_DOMAIN_IDS)
def test_shared_domain_accept_and_equal(tool, args):
    reg_outcome, reg_val = _registry(tool, args)
    fw_outcome, action = _firewall(tool, args)
    assert reg_outcome == "accept"
    assert fw_outcome == "accept"
    _assert_values_equal(reg_val, action)


# --- 2. shared-domain rejections: same reject ---------------------------------

_SHARED_DOMAIN_REJECTIONS = [
    ("read_file", {}),
    ("write_file", {"path": "a"}),
    ("append_file", {"path": "a"}),
    ("edit_file", {"path": "a", "old_string": "o"}),
    ("apply_edits", {"path": "a"}),
    ("insert_before", {"path": "a", "anchor": "x"}),
    ("insert_after", {"path": "a", "anchor": "x"}),
    ("grep", {}),
    ("bash", {}),
    ("read_file", {"path": 5}),
    ("write_file", {"path": "a", "content": []}),
    ("read_file", {"path": "a", "offset": "1.5"}),
    ("bash", {"command": "ls", "timeout": "60ms"}),
    ("bash", {"command": "ls", "timeout": True}),
    ("apply_edits", {"path": "a", "edits": [{"old": "o", "new": "n", "sneaky": 1}]}),
    ("apply_edits", {"path": "a", "edits": [{"old": "o"}]}),
    ("apply_edits", {"path": "a", "edits": "not-a-list"}),
]
_SHARED_DOMAIN_REJECTION_IDS = [
    f"{i}:{tool}:{sorted(args)}" for i, (tool, args) in enumerate(_SHARED_DOMAIN_REJECTIONS)
]


@pytest.mark.parametrize("tool,args", _SHARED_DOMAIN_REJECTIONS, ids=_SHARED_DOMAIN_REJECTION_IDS)
def test_shared_domain_rejection_parity(tool, args):
    reg_outcome, _reg_val = _registry(tool, args)
    fw_outcome, _rejection = _firewall(tool, args)
    assert reg_outcome == "reject"
    assert fw_outcome == "reject"


# --- 3. exception table, one test per row -------------------------------------

_NULL_OPTIONAL_NON_NONE_DEFAULT = [
    ("read_file", {"path": "a", "offset": None}, "offset", 0),
    ("read_file", {"path": "a", "limit": None}, "limit", 400),
    ("list_dir", {"path": None}, "path", "."),
    ("grep", {"pattern": "p", "path": None}, "path", "."),
    ("bash", {"command": "ls", "timeout": None}, "timeout", 120),
]


@pytest.mark.parametrize(
    "tool,args,field,default",
    _NULL_OPTIONAL_NON_NONE_DEFAULT,
    ids=[f"{tool}.{field}" for tool, _, field, _ in _NULL_OPTIONAL_NON_NONE_DEFAULT],
)
def test_null_on_optional_non_none_default_registry_rejects_firewall_defaults(tool, args, field, default):
    reg_outcome, _reg_val = _registry(tool, args)
    assert reg_outcome == "reject"
    fw_outcome, action = _firewall(tool, args)
    assert fw_outcome == "accept"
    assert getattr(action.args, field) == default


def test_grep_glob_none_explicit_is_not_an_exception_both_accept_none():
    args = {"pattern": "p", "glob": None}
    reg_outcome, reg_val = _registry("grep", args)
    fw_outcome, action = _firewall("grep", args)
    assert reg_outcome == "accept" and reg_val["glob"] is None
    assert fw_outcome == "accept" and action.args.glob is None


@pytest.mark.parametrize(
    "tool,args,field",
    [
        ("read_file", {"path": "a", "offset": -1}, "offset"),
        ("read_file", {"path": "a", "limit": 0}, "limit"),
    ],
    ids=["offset_negative", "limit_zero"],
)
def test_offset_limit_domain_registry_accepts_firewall_number_out_of_range(tool, args, field):
    reg_outcome, reg_val = _registry(tool, args)
    assert reg_outcome == "accept"
    assert reg_val[field] == args[field]
    fw_outcome, rejection = _firewall(tool, args)
    assert fw_outcome == "reject"
    assert rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


def test_offset_numeric_string_above_max_int_registry_accepts_firewall_rejects():
    args = {"path": "a", "offset": "2147483648"}
    reg_outcome, reg_val = _registry("read_file", args)
    assert reg_outcome == "accept"
    assert reg_val["offset"] == 2147483648
    fw_outcome, rejection = _firewall("read_file", args)
    assert fw_outcome == "reject"
    assert rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


@pytest.mark.parametrize("value", [0, -5], ids=["zero", "negative"])
def test_bash_timeout_low_registry_unchanged_firewall_clamps_to_one(value):
    args = {"command": "ls", "timeout": value}
    reg_outcome, reg_val = _registry("bash", args)
    assert reg_outcome == "accept"
    assert reg_val["timeout"] == value
    fw_outcome, action = _firewall("bash", args)
    assert fw_outcome == "accept"
    assert action.args.timeout == 1


def test_bash_timeout_601_registry_unchanged_firewall_clamps_to_600():
    args = {"command": "ls", "timeout": 601}
    reg_outcome, reg_val = _registry("bash", args)
    assert reg_outcome == "accept"
    assert reg_val["timeout"] == 601
    fw_outcome, action = _firewall("bash", args)
    assert fw_outcome == "accept"
    assert action.args.timeout == 600


def test_bash_timeout_numeric_string_above_max_int_registry_unchanged_firewall_clamps_to_600():
    args = {"command": "ls", "timeout": "2147483648"}
    reg_outcome, reg_val = _registry("bash", args)
    assert reg_outcome == "accept"
    assert reg_val["timeout"] == 2147483648
    fw_outcome, action = _firewall("bash", args)
    assert fw_outcome == "accept"
    assert action.args.timeout == 600


def test_bash_timeout_integer_above_max_int_registry_accepts_firewall_rejects():
    # The pair the exception table calls out precisely: the same magnitude as
    # an int is in the registry's validation domain (no domain check there)
    # but is caught by the Firewall's own structural walk (check_request)
    # before the pass ever clamps timeout, since it is outside
    # [MIN_INT, MAX_INT]. The string form above clamps instead (previous test).
    args = {"command": "ls", "timeout": 2147483648}
    reg_outcome, reg_val = _registry("bash", args)
    assert reg_outcome == "accept"
    assert reg_val["timeout"] == 2147483648
    fw_outcome, rejection = _firewall("bash", args)
    assert fw_outcome == "reject"
    assert rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


@pytest.mark.parametrize(
    "tool,args",
    [
        ("grep", {"pattern": "p" * (MAX_PATTERN_CHARS + 1)}),
        ("grep", {"pattern": "p", "glob": "g" * (MAX_GLOB_CHARS + 1)}),
        ("read_file", {"path": "p" * (MAX_PATH_CHARS + 1)}),
    ],
    ids=["pattern_over_max", "glob_over_max", "path_over_max"],
)
def test_string_over_section4_bound_registry_accepts_firewall_string_too_long(tool, args):
    reg_outcome, _reg_val = _registry(tool, args)
    assert reg_outcome == "accept"
    fw_outcome, rejection = _firewall(tool, args)
    assert fw_outcome == "reject"
    assert rejection.reason_code is ReasonCode.STRING_TOO_LONG


def test_33_unknown_top_level_keys_registry_drops_firewall_collection_too_large():
    args = {"command": "ls"}
    for i in range(33):
        args[f"unknown_{i}"] = i
    reg_outcome, reg_val = _registry("bash", args)
    assert reg_outcome == "accept"
    assert reg_val == {"command": "ls", "timeout": 120}
    fw_outcome, rejection = _firewall("bash", args)
    assert fw_outcome == "reject"
    assert rejection.reason_code is ReasonCode.COLLECTION_TOO_LARGE


def test_finish_no_summary_registry_rejects_firewall_accepts_empty():
    reg_outcome, _reg_val = _registry("finish", {})
    assert reg_outcome == "reject"
    fw_outcome, action = _firewall("finish", {})
    assert fw_outcome == "accept"
    assert action.args.summary == ""


def test_apply_edits_empty_old_registry_accepts_firewall_rejects():
    args = {"path": "a", "edits": [{"old": "", "new": "n"}]}
    reg_outcome, reg_val = _registry("apply_edits", args)
    assert reg_outcome == "accept"
    assert reg_val["edits"] == [{"old": "", "new": "n"}]
    fw_outcome, rejection = _firewall("apply_edits", args)
    assert fw_outcome == "reject"
    assert rejection.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_apply_edits_unknown_nested_key_firewall_code_is_argument_unexpected():
    args = {"path": "a", "edits": [{"old": "o", "new": "n", "sneaky": 1}]}
    reg_outcome, _reg_val = _registry("apply_edits", args)
    assert reg_outcome == "reject"
    fw_outcome, rejection = _firewall("apply_edits", args)
    assert fw_outcome == "reject"
    assert rejection.reason_code is ReasonCode.ARGUMENT_UNEXPECTED


def test_numeric_string_over_32_chars_registry_accepts_firewall_string_too_long():
    args = {"path": "x", "offset": "0" * 40 + "5"}
    outcome, call_args = _registry("read_file", args)
    assert outcome == "accept"
    assert call_args["offset"] == 5
    outcome, rejection = _firewall("read_file", args)
    assert outcome == "reject"
    assert rejection.reason_code is ReasonCode.STRING_TOO_LONG
