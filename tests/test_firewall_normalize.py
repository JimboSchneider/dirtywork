from __future__ import annotations

import ast
import json
import pathlib
import time

import pytest

from dirtywork import builtin_tools, toolspec
from dirtywork.firewall import bounds
from dirtywork.firewall.capabilities import ActionKind, BASE_CAPABILITIES, Capability
from dirtywork.firewall.errors import FirewallInternalError
from dirtywork.firewall.normalize import (
    FIELD_TABLE,
    Normalization,
    TOOL_CALL_MARKERS,
    canonicalize,
    recover_name,
)
from dirtywork.firewall.reasons import ReasonCode
from dirtywork.firewall.schema import (
    ActionRequest,
    AppendFileArgs,
    ApplyEditsArgs,
    BashArgs,
    Edit,
    EditFileArgs,
    FinishArgs,
    GrepArgs,
    InsertArgs,
    ListDirArgs,
    ReadFileArgs,
    SemanticStatus,
    WriteFileArgs,
    action_identity,
)


def _req(tool, arguments, call_id="call_1", turn=1, batch_index=0, batch_size=1):
    return ActionRequest(
        call_id=call_id,
        tool_name=tool,
        arguments=arguments,
        parse_error=None,
        raw_chars=len(json.dumps(arguments)) if arguments is not None else 0,
        turn=turn,
        batch_index=batch_index,
        batch_size=batch_size,
    )


_REGISTRY = builtin_tools.default_registry()
_KIND_VALUES = [kind.value for kind in ActionKind]
# Built by concatenation, like the module and the registry: a worker model
# writing this file through its own tool channel cannot emit the literal
# tags, because its chat template treats them as special tokens.
_TC = "<" + "tool_call>"
_FN = "<" + "function="
_TCS = "[" + "TOOL_CALLS]"


# --- group 1/2 (recover_name and TOOL_CALL_MARKERS parity) ------------------

_RECOVER_NAME_FIXTURE = list(_KIND_VALUES)
for _marker in TOOL_CALL_MARKERS:
    for _name in _KIND_VALUES:
        _RECOVER_NAME_FIXTURE.append(_marker + _name)
_RECOVER_NAME_FIXTURE.extend(
    [
        _TC + " bash ",
        _TC + "\tbash",
        _TC + _TC + "bash",
        _TCS + _FN + "grep",
        _TC + "nope",
        "foobash",
        "call bash",
        "",
    ]
)


def _fixture_id(name):
    return f"len={len(name)}" if len(name) > 60 else repr(name)


@pytest.mark.parametrize("name", _RECOVER_NAME_FIXTURE, ids=_fixture_id)
def test_recover_name_matches_registry(name):
    assert recover_name(name) == _REGISTRY.recover_name(name)


@pytest.mark.parametrize(
    "name",
    [_TC * 3000 + "nope", _TC * 3000 + "bash"],
    ids=["repeated_marker_no_tail", "repeated_marker_bash_tail"],
)
def test_recover_name_matches_registry_pathological(name):
    assert recover_name(name) == _REGISTRY.recover_name(name)


def test_tool_call_markers_equals_toolspec():
    assert TOOL_CALL_MARKERS == toolspec.TOOL_CALL_MARKERS


# --- group 3 (performance on a pathological name) ---------------------------


def test_giant_marker_only_name_is_fast_and_tool_name_invalid():
    name = _TC * 100_000
    assert len(name) > 1024 * 1024
    request = _req(name, {})
    t0 = time.perf_counter()
    result = canonicalize(request)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.TOOL_NAME_INVALID


# --- group 4 (non-string tool_name) ------------------------------------------


@pytest.mark.parametrize("bad_name", [None, ["bash"]])
def test_non_string_tool_name_is_tool_name_invalid(bad_name):
    result = canonicalize(_req(bad_name, {"command": "ls"}))
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.TOOL_NAME_INVALID


# --- group 5 (per-kind minimal accepted call) --------------------------------

_PER_KIND_MINIMAL = [
    (ActionKind.READ_FILE, {"path": "a"}, ReadFileArgs(path="a", offset=0, limit=400)),
    (ActionKind.WRITE_FILE, {"path": "a", "content": "c"}, WriteFileArgs(path="a", content="c")),
    (ActionKind.APPEND_FILE, {"path": "a", "text": "t"}, AppendFileArgs(path="a", text="t")),
    (
        ActionKind.EDIT_FILE,
        {"path": "a", "old_string": "o", "new_string": "n"},
        EditFileArgs(path="a", old_string="o", new_string="n"),
    ),
    (
        ActionKind.APPLY_EDITS,
        {"path": "a", "edits": [{"old": "o", "new": "n"}]},
        ApplyEditsArgs(path="a", edits=(Edit(old="o", new="n"),)),
    ),
    (
        ActionKind.INSERT_BEFORE,
        {"path": "a", "anchor": "x", "text": "t"},
        InsertArgs(path="a", anchor="x", text="t"),
    ),
    (
        ActionKind.INSERT_AFTER,
        {"path": "a", "anchor": "x", "text": "t"},
        InsertArgs(path="a", anchor="x", text="t"),
    ),
    (ActionKind.LIST_DIR, {}, ListDirArgs(path=".")),
    (ActionKind.GREP, {"pattern": "p"}, GrepArgs(pattern="p", path=".", glob=None)),
    (ActionKind.BASH, {"command": "ls"}, BashArgs(command="ls", timeout=120)),
    (ActionKind.FINISH, {}, FinishArgs(summary="")),
]


@pytest.mark.parametrize("kind,arguments,expected_args", _PER_KIND_MINIMAL, ids=[k.value for k, _, _ in _PER_KIND_MINIMAL])
def test_minimal_call_per_kind(kind, arguments, expected_args):
    result = canonicalize(_req(kind.value, arguments))
    assert result.rejection is None
    action = result.action
    assert action.kind is kind
    assert type(action.args) is type(expected_args)
    assert action.args == expected_args
    assert action.capabilities == BASE_CAPABILITIES[kind]
    expected_status = SemanticStatus.UNKNOWN if kind is ActionKind.BASH else SemanticStatus.KNOWN
    assert action.semantic_status is expected_status
    assert result.dropped_keys == 0


# --- group 6 (every rejection code the pass can emit, once each) ------------


def test_argument_missing_names_field():
    result = canonicalize(_req("write_file", {}))
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_MISSING
    assert "path" in result.rejection.detail


def test_argument_type_invalid_names_field_and_excludes_value():
    result = canonicalize(_req("read_file", {"path": 12345}))
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID
    assert "path" in result.rejection.detail
    assert "12345" not in result.rejection.detail


def test_argument_unexpected_names_index_not_key():
    result = canonicalize(
        _req("apply_edits", {"path": "a", "edits": [{"old": "o", "new": "n", "sneaky": 1}]})
    )
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_UNEXPECTED
    assert "edits[0]" in result.rejection.detail
    assert "sneaky" not in result.rejection.detail


def test_string_too_long_names_field_and_limit():
    result = canonicalize(_req("read_file", {"path": "a" * (bounds.MAX_PATH_CHARS + 1)}))
    assert result.rejection.reason_code is ReasonCode.STRING_TOO_LONG
    assert "path" in result.rejection.detail
    assert str(bounds.MAX_PATH_CHARS) in result.rejection.detail


def test_number_out_of_range_names_field():
    result = canonicalize(_req("read_file", {"path": "a", "limit": "0"}))
    assert result.rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE
    assert "limit" in result.rejection.detail


def test_check_request_codes_pass_through():
    assert canonicalize(_req("not_a_tool", {})).rejection.reason_code is ReasonCode.TOOL_UNKNOWN
    result = canonicalize(_req("bash", "not a dict"))
    assert result.rejection.reason_code is ReasonCode.ARGUMENTS_NOT_OBJECT
    result = canonicalize(_req("bash", {"command": "ls"}, call_id=""))
    assert result.rejection.reason_code is ReasonCode.CALL_ID_INVALID


# --- group 7 (step order) ----------------------------------------------------


def test_unknown_key_beside_missing_required_reports_missing():
    result = canonicalize(_req("write_file", {"path": "a", "bogus": 1}))
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_MISSING
    assert "content" in result.rejection.detail


def test_null_required_field_is_type_invalid_not_missing():
    result = canonicalize(_req("write_file", {"path": "a", "content": None}))
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_first_field_string_too_long_beats_second_field_type_invalid():
    result = canonicalize(
        _req(
            "edit_file",
            {
                "path": "a" * (bounds.MAX_PATH_CHARS + 1),
                "old_string": 5,
                "new_string": "n",
            },
        )
    )
    assert result.rejection.reason_code is ReasonCode.STRING_TOO_LONG


def test_check_request_rejection_beats_field_pass():
    result = canonicalize(_req("write_file", {"path": "a"}, batch_size=33))
    assert result.rejection.reason_code is ReasonCode.BATCH_TOO_LARGE


# --- group 8 (dropped_keys) --------------------------------------------------


def test_dropped_keys_counts_two_unknown():
    result = canonicalize(_req("write_file", {"path": "a", "content": "c", "x": 1, "y": 2}))
    assert result.rejection is None
    assert result.dropped_keys == 2


def test_dropped_keys_grep_timeout_is_ignored():
    result = canonicalize(_req("grep", {"pattern": "p", "timeout": 5}))
    assert result.rejection is None
    assert result.dropped_keys == 1
    assert not hasattr(result.action.args, "timeout")


def test_dropped_keys_counts_null_valued_unknown_key():
    result = canonicalize(_req("finish", {"extra": None}))
    assert result.rejection is None
    assert result.dropped_keys == 1


# --- group 9 (apply_edits nested rules) --------------------------------------

_APPLY_EDITS_CASES = [
    (["oops"], ReasonCode.ARGUMENT_TYPE_INVALID, "edits[0]"),
    ([{"old": "a"}], ReasonCode.ARGUMENT_TYPE_INVALID, "edits[0].new"),
    ([{"old": 5, "new": "b"}], ReasonCode.ARGUMENT_TYPE_INVALID, "edits[0].old"),
    ([{"old": "", "new": "b"}], ReasonCode.ARGUMENT_TYPE_INVALID, "edits[0].old"),
    ([], ReasonCode.ARGUMENT_TYPE_INVALID, "edits"),
]


@pytest.mark.parametrize("edits,code,needle", _APPLY_EDITS_CASES)
def test_apply_edits_nested_rules(edits, code, needle):
    result = canonicalize(_req("apply_edits", {"path": "a", "edits": edits}))
    assert result.rejection is not None
    assert result.rejection.reason_code is code
    assert needle in result.rejection.detail


def test_apply_edits_extra_key_is_unexpected_by_index():
    result = canonicalize(
        _req(
            "apply_edits",
            {
                "path": "a",
                "edits": [{"old": "a", "new": "b"}, {"old": "a", "new": "b", "sneaky": "x"}],
            },
        )
    )
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_UNEXPECTED
    assert "edits[1]" in result.rejection.detail
    assert "sneaky" not in result.rejection.detail


def test_apply_edits_item_string_over_max_chars():
    huge = "x" * (bounds.MAX_STRING_CHARS + 1)
    result = canonicalize(_req("apply_edits", {"path": "a", "edits": [{"old": huge, "new": "b"}]}))
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.STRING_TOO_LONG


# --- group 10 (coercion table, both directions) ------------------------------


def test_offset_numeric_string_coerces():
    result = canonicalize(_req("read_file", {"path": "a", "offset": "5"}))
    assert result.rejection is None
    assert result.action.args.offset == 5


def test_offset_decimal_string_rejected():
    result = canonicalize(_req("read_file", {"path": "a", "offset": "1.5"}))
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_offset_bool_rejected():
    result = canonicalize(_req("read_file", {"path": "a", "offset": True}))
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_offset_underscore_digit_string_parses_like_int():
    # int() accepts underscore digit grouping ("1_0" == 10); documented parity
    # with the registry's own int()-based coercion.
    result = canonicalize(_req("read_file", {"path": "a", "offset": "1_0"}))
    assert result.rejection is None
    assert result.action.args.offset == 10


@pytest.mark.parametrize(
    "value,expected",
    [(60, 60), ("60", 60), ("60s", 60), ("2m", 120), ("2 MIN", 120)],
)
def test_timeout_accepted_forms(value, expected):
    result = canonicalize(_req("bash", {"command": "ls", "timeout": value}))
    assert result.rejection is None
    assert result.action.args.timeout == expected


@pytest.mark.parametrize("value", ["60ms", "-5s", 1.5, True])
def test_timeout_rejected_forms(value):
    result = canonicalize(_req("bash", {"command": "ls", "timeout": value}))
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_timeout_underscore_digit_string_parses():
    result = canonicalize(_req("bash", {"command": "ls", "timeout": "1_0"}))
    assert result.rejection is None
    assert result.action.args.timeout == 10


# --- group 11 (bounds after coercion) ----------------------------------------


def test_offset_max_int_accepted():
    result = canonicalize(_req("read_file", {"path": "a", "offset": str(bounds.MAX_INT)}))
    assert result.rejection is None
    assert result.action.args.offset == bounds.MAX_INT


def test_offset_above_max_int_rejected():
    result = canonicalize(_req("read_file", {"path": "a", "offset": str(bounds.MAX_INT + 1)}))
    assert result.rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


def test_offset_negative_rejected():
    result = canonicalize(_req("read_file", {"path": "a", "offset": "-1"}))
    assert result.rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


def test_limit_zero_rejected():
    result = canonicalize(_req("read_file", {"path": "a", "limit": "0"}))
    assert result.rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, 1),
        (-5, 1),
        (601, bounds.MAX_BASH_TIMEOUT),
        ("2147483648", bounds.MAX_BASH_TIMEOUT),
    ],
)
def test_timeout_clamped_never_rejected(value, expected):
    result = canonicalize(_req("bash", {"command": "ls", "timeout": value}))
    assert result.rejection is None
    assert result.action.args.timeout == expected


def test_timeout_integer_above_max_int_rejected_by_check_request():
    result = canonicalize(_req("bash", {"command": "ls", "timeout": bounds.MAX_INT + 1}))
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


# --- group 18 (numeric strings are bounded before conversion) --------------


@pytest.mark.parametrize("field", ["offset", "limit"])
def test_numeric_string_at_the_bound_is_converted(field):
    value = "0" * 31 + "5"  # 32 chars; int() gives 5
    result = canonicalize(_req("read_file", {"path": "x", field: value}))
    assert result.action is not None
    assert getattr(result.action.args, field) == 5


@pytest.mark.parametrize("field", ["offset", "limit"])
def test_numeric_string_over_the_bound_is_string_too_long_before_conversion(field):
    value = "0" * 32 + "5"  # 33 chars
    result = canonicalize(_req("read_file", {"path": "x", field: value}))
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.STRING_TOO_LONG
    assert field in result.rejection.detail
    assert value not in result.rejection.detail


def test_timeout_numeric_string_over_the_bound_is_string_too_long():
    result = canonicalize(_req("bash", {"command": "ls", "timeout": "0" * 32 + "60"}))
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.STRING_TOO_LONG


@pytest.mark.parametrize("tool,args", [
    ("read_file", {"path": "x", "offset": "1" * 1_000_000}),
    ("bash", {"command": "ls", "timeout": "1" * 1_000_000}),
], ids=["offset", "timeout"])
def test_million_digit_numeric_string_is_rejected_fast(tool, args):
    start = time.perf_counter()
    result = canonicalize(_req(tool, args))
    assert time.perf_counter() - start < 0.5
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.STRING_TOO_LONG


# --- group 12 (capability assembly) ------------------------------------------

_CAPABILITY_CASES = [
    ("read_file", {"path": "/etc/passwd"}, BASE_CAPABILITIES[ActionKind.READ_FILE] | {Capability.HOST_FS}),
    ("write_file", {"path": "../x", "content": "c"}, BASE_CAPABILITIES[ActionKind.WRITE_FILE] | {Capability.HOST_FS}),
    (
        "write_file",
        {"path": ".git/config", "content": "c"},
        BASE_CAPABILITIES[ActionKind.WRITE_FILE] | {Capability.REPO_CONTROL},
    ),
    ("read_file", {"path": ".git/config"}, BASE_CAPABILITIES[ActionKind.READ_FILE]),
    ("write_file", {"path": "src/a/../x", "content": "c"}, BASE_CAPABILITIES[ActionKind.WRITE_FILE]),
]


@pytest.mark.parametrize("tool,arguments,expected_caps", _CAPABILITY_CASES)
def test_capability_assembly(tool, arguments, expected_caps):
    result = canonicalize(_req(tool, arguments))
    assert result.rejection is None
    assert result.action.capabilities == frozenset(expected_caps)


# --- group 13 (idempotence, every kind) --------------------------------------


def _round_trip(action):
    arguments = dict(vars(action.args))
    if "edits" in arguments:
        arguments["edits"] = [{"old": e.old, "new": e.new} for e in arguments["edits"]]
    request = _req(action.kind.value, arguments, call_id=action.call_id, turn=action.turn)
    return canonicalize(request)


@pytest.mark.parametrize("kind,arguments,_expected", _PER_KIND_MINIMAL, ids=[k.value for k, _, _ in _PER_KIND_MINIMAL])
def test_idempotent_per_kind(kind, arguments, _expected):
    first = canonicalize(_req(kind.value, arguments))
    assert first.rejection is None
    second = _round_trip(first.action)
    assert second.rejection is None
    assert second.action == first.action
    assert action_identity(second.action) == action_identity(first.action)


# --- group 14 (bash identity) -------------------------------------------------


def _bash_identity(command):
    result = canonicalize(_req("bash", {"command": command}))
    assert result.rejection is None
    return action_identity(result.action)


def test_bash_identity_distinguishes_whitespace():
    ids = {_bash_identity(c) for c in ("ls", "ls\n", "  ls  ")}
    assert len(ids) == 3


def test_bash_identity_equal_for_repeated_call():
    assert _bash_identity("ls") == _bash_identity("ls")


# --- group 15 (Normalization invariants) --------------------------------------


def _sample_action():
    return canonicalize(_req("bash", {"command": "ls"})).action


def _sample_rejection():
    return canonicalize(_req("bash", {})).rejection


def test_normalization_requires_exactly_one_of_action_and_rejection():
    action = _sample_action()
    rejection = _sample_rejection()
    with pytest.raises(FirewallInternalError):
        Normalization(action=action, rejection=rejection, dropped_keys=0)
    with pytest.raises(FirewallInternalError):
        Normalization(action=None, rejection=None, dropped_keys=0)


def test_normalization_dropped_keys_must_be_a_nonnegative_int():
    action = _sample_action()
    with pytest.raises(FirewallInternalError):
        Normalization(action=action, rejection=None, dropped_keys=-1)
    with pytest.raises(FirewallInternalError):
        Normalization(action=action, rejection=None, dropped_keys=True)


def test_normalization_dropped_keys_must_be_zero_on_rejection():
    rejection = _sample_rejection()
    with pytest.raises(FirewallInternalError):
        Normalization(action=None, rejection=rejection, dropped_keys=1)


# --- group 16 (FIELD_TABLE versus the live registry) --------------------------


def test_field_table_matches_registry():
    for kind in ActionKind:
        spec = _REGISTRY.spec(kind.value)
        fields = FIELD_TABLE[kind]
        assert [f.name for f in fields] == list(spec.params.keys())
        for field in fields:
            pspec = spec.params[field.name]
            reg_required = field.name in spec.required
            if kind is ActionKind.FINISH and field.name == "summary":
                # Declared exception (spec §4): the registry requires
                # `summary` with no default; the Runner already
                # canonicalizes a missing one to "".
                assert reg_required is True
                assert field.required is False
                assert pspec.default is toolspec.MISSING
                assert field.default == ""
                continue
            assert field.required == reg_required
            if not field.required:
                assert pspec.default is not toolspec.MISSING
                assert field.default == pspec.default


# --- group 17 (import isolation) ----------------------------------------------


def _forbidden_imports(path):
    tree = ast.parse(path.read_text())
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "dirtywork" or alias.name.startswith("dirtywork."):
                    if not (
                        alias.name == "dirtywork.firewall"
                        or alias.name.startswith("dirtywork.firewall.")
                    ):
                        found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level >= 2:
                # A relative import climbing out of dirtywork.firewall itself.
                found.append("." * node.level + (node.module or ""))
            elif node.module and (node.module == "dirtywork" or node.module.startswith("dirtywork.")):
                if not (
                    node.module == "dirtywork.firewall"
                    or node.module.startswith("dirtywork.firewall.")
                ):
                    found.append(node.module)
    return found


def test_import_isolation():
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("dirtywork/firewall/paths.py", "dirtywork/firewall/normalize.py"):
        assert _forbidden_imports(root / rel) == [], rel


# --- batch: canonicalize_batch --------------------------------------------

from dirtywork.firewall.normalize import canonicalize_batch


def test_batch_three_distinct_ids_all_canonicalized_in_order():
    requests = [
        _req("bash", {"command": "ls"}, call_id="call_1", batch_size=1),
        _req("bash", {"command": "pwd"}, call_id="call_2", batch_size=1),
        _req("bash", {"command": "echo hi"}, call_id="call_3", batch_size=1),
    ]
    results = canonicalize_batch(requests)
    assert len(results) == 3
    assert [r.rejection for r in results] == [None, None, None]
    assert [r.action.args.command for r in results] == ["ls", "pwd", "echo hi"]


def test_batch_duplicate_id_at_index_2():
    requests = [
        _req("bash", {"command": "ls"}, call_id="zzqx7", batch_size=1),
        _req("bash", {"command": "pwd"}, call_id="b", batch_size=1),
        _req("bash", {"command": "echo hi"}, call_id="zzqx7", batch_size=1),
    ]
    results = canonicalize_batch(requests)
    assert results[0].rejection is None
    assert results[0].action.args.command == "ls"
    assert results[1].rejection is None
    assert results[2].rejection is not None
    assert results[2].rejection.reason_code is ReasonCode.CALL_ID_DUPLICATE
    assert "index 2" in results[2].rejection.detail
    assert "zzqx7" not in results[2].rejection.detail
    assert results[2].dropped_keys == 0


def test_batch_three_copies_of_same_id():
    requests = [
        _req("bash", {"command": "ls"}, call_id="dup", batch_size=1),
        _req("bash", {"command": "pwd"}, call_id="dup", batch_size=1),
        _req("bash", {"command": "echo hi"}, call_id="dup", batch_size=1),
    ]
    results = canonicalize_batch(requests)
    assert results[0].rejection is None
    assert results[0].action.args.command == "ls"
    assert results[1].rejection is not None
    assert results[1].rejection.reason_code is ReasonCode.CALL_ID_DUPLICATE
    assert results[2].rejection is not None
    assert results[2].rejection.reason_code is ReasonCode.CALL_ID_DUPLICATE


def test_batch_duplicate_of_a_rejected_first_occurrence_is_still_flagged():
    # Dedup is by position, before validation: the first occurrence being
    # itself rejected (for an unrelated reason) does not exempt a later
    # occurrence from call_id_duplicate.
    requests = [
        _req("not_a_tool", {}, call_id="x", batch_size=1),
        _req("bash", {"command": "ls"}, call_id="x", batch_size=1),
    ]
    results = canonicalize_batch(requests)
    assert results[0].rejection is not None
    assert results[0].rejection.reason_code is ReasonCode.TOOL_UNKNOWN
    assert results[1].rejection is not None
    assert results[1].rejection.reason_code is ReasonCode.CALL_ID_DUPLICATE


def test_batch_empty_is_empty():
    assert canonicalize_batch([]) == []


def test_batch_malformed_neighbour_does_not_affect_others():
    requests = [
        _req("write_file", {}, call_id="ok_1", batch_size=1),  # missing content
        _req("bash", {"command": "ls"}, call_id="ok_2", batch_size=1),
    ]
    results = canonicalize_batch(requests)
    assert results[0].rejection is not None
    assert results[0].rejection.reason_code is ReasonCode.ARGUMENT_MISSING
    assert results[1].rejection is None
    assert results[1].action.args.command == "ls"


def test_batch_ids_compared_exactly():
    # "call_1" and "call_1 " are different ids under plain `==`; the second
    # is not deduplicated against the first, so it reaches check_request on
    # its own and is rejected for whitespace, not for being a duplicate.
    requests = [
        _req("bash", {"command": "ls"}, call_id="call_1", batch_size=1),
        _req("bash", {"command": "pwd"}, call_id="call_1 ", batch_size=1),
    ]
    results = canonicalize_batch(requests)
    assert results[0].rejection is None
    assert results[1].rejection is not None
    assert results[1].rejection.reason_code is ReasonCode.CALL_ID_INVALID


def test_batch_size_field_not_required_to_match_request_count():
    # canonicalize_batch never checks batch_size against len(requests); that
    # consistency is the adapter's job (spec §9).
    requests = [
        _req("bash", {"command": "ls"}, call_id="call_1", batch_size=1),
        _req("bash", {"command": "pwd"}, call_id="call_2", batch_size=1),
    ]
    assert len(requests) == 2
    results = canonicalize_batch(requests)
    assert all(r.rejection is None for r in results)


def test_batch_single_request_matches_canonicalize():
    request = _req("bash", {"command": "ls"}, call_id="call_1", batch_size=1)
    batch_result = canonicalize_batch([request])[0]
    direct_result = canonicalize(request)
    assert batch_result.action == direct_result.action
    assert batch_result.rejection == direct_result.rejection


def _deep_list(depth):
    value = []
    for _ in range(depth):
        value = [value]
    return value


def test_batch_non_string_ids_never_crash_or_duplicate():
    # Two deeply nested list ids: comparing them with == recurses on Python 3.9,
    # so they must never reach the duplicate check; each is call_id_invalid
    # on its own and the valid neighbour is still processed.
    results = canonicalize_batch([
        _req("read_file", {"path": "x"}, call_id=_deep_list(2000)),
        _req("read_file", {"path": "x"}, call_id=_deep_list(2000)),
        _req("read_file", {"path": "x"}, call_id="call_ok"),
    ])
    assert [r.rejection.reason_code if r.rejection else None for r in results[:2]] == [
        ReasonCode.CALL_ID_INVALID, ReasonCode.CALL_ID_INVALID,
    ]
    assert results[2].action is not None


def test_batch_equal_non_string_ids_are_invalid_not_duplicate():
    results = canonicalize_batch([
        _req("read_file", {"path": "x"}, call_id=["x"]),
        _req("read_file", {"path": "x"}, call_id=["x"]),
    ])
    assert all(r.rejection.reason_code is ReasonCode.CALL_ID_INVALID for r in results)
