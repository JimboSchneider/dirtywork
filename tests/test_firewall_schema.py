from __future__ import annotations

from types import SimpleNamespace

import pytest

from dirtywork.firewall import bounds
from dirtywork.firewall.capabilities import ActionKind, Capability
from dirtywork.firewall.errors import FirewallInternalError
from dirtywork.firewall.reasons import ReasonClass, ReasonCode
from dirtywork.firewall import schema
from dirtywork.firewall.schema import (
    ActionRequest,
    ApplyEditsArgs,
    BashArgs,
    CanonicalAction,
    Decision,
    Edit,
    FirewallEvent,
    GrepArgs,
    InsertArgs,
    PolicyDecision,
    ReadFileArgs,
    SemanticStatus,
    WriteFileArgs,
    action_identity,
    rejection_identity,
    valid_call_id,
)

EXPECTED_DECISIONS = sorted(["allow", "deny"])
EXPECTED_SEMANTIC_STATUSES = sorted(["semantic_known", "semantic_unknown"])


class _FakeToolCall:
    def __init__(self, id="call_1", name="write_file", arguments=None, error=None,
                 raw_arguments=""):
        self.id = id
        self.name = name
        self.arguments = arguments
        self.error = error
        self.raw_arguments = raw_arguments


def _write_action(**overrides):
    kwargs = dict(
        schema_version=bounds.FIREWALL_SCHEMA_VERSION,
        call_id="call_1",
        turn=1,
        kind=ActionKind.WRITE_FILE,
        args=WriteFileArgs(path="src/app.py", content="print(1)\n"),
        capabilities=frozenset({Capability.WORKSPACE_WRITE}),
        semantic_status=SemanticStatus.KNOWN,
    )
    kwargs.update(overrides)
    return CanonicalAction(**kwargs)


# --- group 1: vocabulary pins ---------------------------------------------


def test_decision_vocabulary_pin():
    assert sorted(m.value for m in Decision) == EXPECTED_DECISIONS


def test_semantic_status_vocabulary_pin():
    assert sorted(m.value for m in SemanticStatus) == EXPECTED_SEMANTIC_STATUSES


# --- group 9: PolicyDecision invariants ------------------------------------


def test_policy_decision_allow_with_reason_code_raises():
    with pytest.raises(FirewallInternalError):
        PolicyDecision(Decision.ALLOW, ReasonCode.TOOL_UNKNOWN, "")


def test_policy_decision_deny_with_none_raises():
    with pytest.raises(FirewallInternalError):
        PolicyDecision(Decision.DENY, None, "")


def test_policy_decision_deny_with_bare_string_raises():
    with pytest.raises(FirewallInternalError):
        PolicyDecision(Decision.DENY, "tool_unknown", "")


def test_policy_decision_valid_allow_and_deny():
    allow = PolicyDecision(Decision.ALLOW, None, "")
    assert allow.decision is Decision.ALLOW
    deny = PolicyDecision(Decision.DENY, ReasonCode.TOOL_UNKNOWN, "unknown tool")
    assert deny.reason_code is ReasonCode.TOOL_UNKNOWN


# --- group 10: semantic_unknown cannot deny --------------------------------


def test_policy_decision_rejects_semantic_unknown_as_reason_code():
    with pytest.raises(FirewallInternalError):
        PolicyDecision(Decision.DENY, "semantic_unknown", "")
    assert "semantic_unknown" not in [m.value for m in ReasonCode]


def test_canonical_action_semantic_unknown_constructs_with_no_reason_code():
    action = _write_action(semantic_status=SemanticStatus.UNKNOWN)
    assert action.semantic_status is SemanticStatus.UNKNOWN


# --- group 11: constructor invariants --------------------------------------


def test_canonical_action_wrong_args_class_for_kind_raises():
    with pytest.raises(FirewallInternalError):
        _write_action(args=ReadFileArgs(path="a", offset=0, limit=1))


def test_canonical_action_string_kind_raises():
    with pytest.raises(FirewallInternalError):
        _write_action(kind="bash")


def test_canonical_action_empty_capabilities_raises():
    with pytest.raises(FirewallInternalError):
        _write_action(capabilities=frozenset())


def test_canonical_action_capabilities_missing_base_raises():
    with pytest.raises(FirewallInternalError):
        _write_action(capabilities=frozenset({Capability.WORKSPACE_READ}))


def test_canonical_action_str_inside_capabilities_raises():
    # A str equal to a Capability's value collapses into the frozenset (str
    # mixin equality), so it would not exercise the isinstance check; use a
    # look-alike value that is not any Capability's value instead.
    with pytest.raises(FirewallInternalError):
        _write_action(
            capabilities=frozenset({Capability.WORKSPACE_WRITE, "not_a_capability"})
        )


def test_canonical_action_schema_version_mismatch_raises():
    with pytest.raises(FirewallInternalError):
        _write_action(schema_version=2)


def test_canonical_action_turn_zero_raises():
    with pytest.raises(FirewallInternalError):
        _write_action(turn=0)


def test_apply_edits_args_list_instead_of_tuple_raises():
    with pytest.raises(FirewallInternalError):
        ApplyEditsArgs(path="a", edits=[Edit(old="x", new="y")])


def test_apply_edits_args_empty_tuple_raises():
    with pytest.raises(FirewallInternalError):
        ApplyEditsArgs(path="a", edits=())


def test_apply_edits_args_too_many_edits_raises():
    edits = tuple(Edit(old="x", new="y") for _ in range(101))
    with pytest.raises(FirewallInternalError):
        ApplyEditsArgs(path="a", edits=edits)


def test_apply_edits_args_within_bound_constructs():
    edits = tuple(Edit(old="x", new="y") for _ in range(100))
    args = ApplyEditsArgs(path="a", edits=edits)
    assert len(args.edits) == 100


def test_edit_empty_old_raises():
    with pytest.raises(FirewallInternalError):
        Edit(old="", new="y")


def test_read_file_args_negative_offset_raises():
    with pytest.raises(FirewallInternalError):
        ReadFileArgs(path="a", offset=-1, limit=1)


def test_bash_args_timeout_over_max_raises():
    with pytest.raises(FirewallInternalError):
        BashArgs(command="ls", timeout=601)


def test_bash_args_timeout_bool_raises():
    with pytest.raises(FirewallInternalError):
        BashArgs(command="ls", timeout=True)


def test_path_over_max_chars_raises():
    with pytest.raises(FirewallInternalError):
        ReadFileArgs(path="a" * (bounds.MAX_PATH_CHARS + 1), offset=0, limit=1)


def test_grep_args_glob_none_constructs():
    args = GrepArgs(pattern="x", path=".", glob=None)
    assert args.glob is None


def test_bytes_for_str_field_raises():
    with pytest.raises(FirewallInternalError):
        ReadFileArgs(path=b"a", offset=0, limit=1)


# --- group 12: identity -----------------------------------------------------


def test_identity_same_kind_and_path_different_content_hash_equal():
    a = _write_action(args=WriteFileArgs(path="src/app.py", content="one"))
    b = _write_action(args=WriteFileArgs(path="src/app.py", content="two"))
    assert action_identity(a) == action_identity(b)


def test_identity_insert_before_vs_after_same_path_differ():
    before = _write_action(
        kind=ActionKind.INSERT_BEFORE,
        args=InsertArgs(path="a", anchor="x", text="y"),
        capabilities=frozenset({Capability.WORKSPACE_WRITE}),
    )
    after = _write_action(
        kind=ActionKind.INSERT_AFTER,
        args=InsertArgs(path="a", anchor="x", text="y"),
        capabilities=frozenset({Capability.WORKSPACE_WRITE}),
    )
    assert action_identity(before) != action_identity(after)


def test_identity_bash_commands_differing_by_flag_differ():
    a = _write_action(
        kind=ActionKind.BASH,
        args=BashArgs(command="ls -la", timeout=30),
        capabilities=frozenset({Capability.SHELL}),
    )
    b = _write_action(
        kind=ActionKind.BASH,
        args=BashArgs(command="ls -l", timeout=30),
        capabilities=frozenset({Capability.SHELL}),
    )
    assert action_identity(a) != action_identity(b)


def test_identity_read_file_different_offset_hash_equal():
    a = _write_action(
        kind=ActionKind.READ_FILE,
        args=ReadFileArgs(path="a", offset=0, limit=100),
        capabilities=frozenset({Capability.WORKSPACE_READ}),
    )
    b = _write_action(
        kind=ActionKind.READ_FILE,
        args=ReadFileArgs(path="a", offset=50, limit=400),
        capabilities=frozenset({Capability.WORKSPACE_READ}),
    )
    assert action_identity(a) == action_identity(b)


def test_identity_pinned_literal():
    action = CanonicalAction(
        schema_version=1,
        call_id="call_1",
        turn=1,
        kind=ActionKind.WRITE_FILE,
        args=WriteFileArgs(path="src/app.py", content="print(1)\n"),
        capabilities=frozenset({Capability.WORKSPACE_WRITE}),
        semantic_status=SemanticStatus.KNOWN,
    )
    assert action_identity(action) == (
        "3fcfda5eb96c0f0052810597adeb47a8f7d16cfe7bc46a29456e660ff74af783"
    )


def test_identity_changes_with_identity_version(monkeypatch):
    action = _write_action()
    original = action_identity(action)
    monkeypatch.setattr(schema, "IDENTITY_VERSION", schema.IDENTITY_VERSION + 1)
    assert action_identity(action) != original


# --- group 13: FirewallEvent -------------------------------------------------


def test_firewall_event_to_dict_keys_and_value_types():
    action = _write_action()
    policy = PolicyDecision(Decision.ALLOW, None, "")
    event = FirewallEvent.from_action(action, policy)
    d = event.to_dict()
    assert set(d.keys()) == {
        "schema_version", "stage", "turn", "call_id", "kind", "capabilities",
        "decision", "reason_code", "reason_class", "action_identity",
        "semantic_status",
    }
    for forbidden in ("args", "arguments", "content", "command", "path"):
        assert forbidden not in d
    assert isinstance(d["capabilities"], list)
    assert all(isinstance(c, str) for c in d["capabilities"])
    assert d["capabilities"] == sorted(d["capabilities"])
    for key in ("schema_version", "stage", "turn", "call_id", "decision", "action_identity"):
        assert isinstance(d[key], (str, int))


def test_firewall_event_from_action_allow_has_no_reason():
    action = _write_action()
    policy = PolicyDecision(Decision.ALLOW, None, "")
    event = FirewallEvent.from_action(action, policy)
    assert event.reason_code is None
    assert event.reason_class is None


def test_firewall_event_from_rejection_tool_unknown():
    request = ActionRequest(
        call_id="call_1", tool_name="read_files", arguments={}, parse_error=None,
        raw_chars=2, turn=1, batch_index=0, batch_size=1,
    )
    rejection = SimpleNamespace(reason_code=ReasonCode.TOOL_UNKNOWN, detail="")
    event = FirewallEvent.from_rejection(request, rejection)
    assert event.stage == "request"
    assert event.kind is None
    assert event.capabilities == ()
    assert event.semantic_status is None
    d = event.to_dict()
    assert d["capabilities"] == []


def test_firewall_event_from_rejection_call_id_invalid_gives_empty_call_id():
    request = ActionRequest(
        call_id="", tool_name="read_file", arguments={}, parse_error=None,
        raw_chars=2, turn=1, batch_index=0, batch_size=1,
    )
    rejection = SimpleNamespace(reason_code=ReasonCode.CALL_ID_INVALID, detail="")
    event = FirewallEvent.from_rejection(request, rejection)
    assert event.call_id == ""


def test_firewall_event_request_stage_allow_raises():
    with pytest.raises(FirewallInternalError):
        FirewallEvent(
            schema_version=bounds.FIREWALL_SCHEMA_VERSION,
            stage="request",
            turn=1,
            call_id="call_1",
            kind=None,
            capabilities=(),
            decision=Decision.ALLOW,
            reason_code=None,
            reason_class=None,
            action_identity="0" * 64,
            semantic_status=None,
        )


def test_firewall_event_action_stage_no_kind_raises():
    with pytest.raises(FirewallInternalError):
        FirewallEvent(
            schema_version=bounds.FIREWALL_SCHEMA_VERSION,
            stage="action",
            turn=1,
            call_id="call_1",
            kind=None,
            capabilities=("workspace_write",),
            decision=Decision.ALLOW,
            reason_code=None,
            reason_class=None,
            action_identity="0" * 64,
            semantic_status=SemanticStatus.KNOWN,
        )


# --- group 14: rejection identity -------------------------------------------


def _request_for(tool_name, call_id="call_1"):
    return ActionRequest(
        call_id=call_id, tool_name=tool_name, arguments={}, parse_error=None,
        raw_chars=2, turn=1, batch_index=0, batch_size=1,
    )


def test_rejection_identity_same_name_hash_equal():
    r1 = _request_for("read_files")
    r2 = _request_for("read_files")
    rejection = SimpleNamespace(reason_code=ReasonCode.TOOL_UNKNOWN, detail="")
    assert rejection_identity(r1, rejection) == rejection_identity(r2, rejection)


def test_rejection_identity_different_name_differ():
    r1 = _request_for("read_files")
    r2 = _request_for("writee_file")
    rejection = SimpleNamespace(reason_code=ReasonCode.TOOL_UNKNOWN, detail="")
    assert rejection_identity(r1, rejection) != rejection_identity(r2, rejection)


def test_rejection_identity_different_reason_code_differs():
    r = _request_for("read_files")
    rej_unknown = SimpleNamespace(reason_code=ReasonCode.TOOL_UNKNOWN, detail="")
    rej_payload = SimpleNamespace(reason_code=ReasonCode.PAYLOAD_TOO_LARGE, detail="")
    assert rejection_identity(r, rej_unknown) != rejection_identity(r, rej_payload)


def test_rejection_identity_survives_a_lone_surrogate_in_the_name():
    r = _request_for("\ud800")
    rejection = SimpleNamespace(reason_code=ReasonCode.TOOL_UNKNOWN, detail="")
    assert len(rejection_identity(r, rejection)) == 64
    assert FirewallEvent.from_rejection(r, rejection).stage == "request"


def test_rejection_identity_raw_name_absent_from_hash_and_to_dict():
    sentinel = "sentinel-tool-name-zzyzx"
    r = _request_for(sentinel)
    rejection = SimpleNamespace(reason_code=ReasonCode.TOOL_UNKNOWN, detail="")
    identity = rejection_identity(r, rejection)
    assert sentinel not in identity
    event = FirewallEvent.from_rejection(r, rejection)
    assert sentinel not in str(event.to_dict())


# --- ActionRequest.from_tool_call -------------------------------------------


def test_action_request_from_tool_call_copies_fields():
    tc = _FakeToolCall(id="call_9", name="bash", arguments={"command": "ls"},
                       error=None, raw_arguments='{"command": "ls"}')
    request = ActionRequest.from_tool_call(tc, turn=3, batch_index=1, batch_size=2)
    assert request.call_id == "call_9"
    assert request.tool_name == "bash"
    assert request.arguments == {"command": "ls"}
    assert request.parse_error is None
    assert request.raw_chars == len('{"command": "ls"}')
    assert request.turn == 3
    assert request.batch_index == 1
    assert request.batch_size == 2


def test_action_request_from_tool_call_raw_chars_none():
    tc = _FakeToolCall(raw_arguments=None)
    request = ActionRequest.from_tool_call(tc, turn=1, batch_index=0, batch_size=1)
    assert request.raw_chars == 0


# --- valid_call_id -----------------------------------------------------------


def test_valid_call_id_accepts_printable_ascii_no_whitespace():
    assert valid_call_id("call_1")
    assert valid_call_id("toolu_ABC123")


def test_valid_call_id_rejects_empty_oversized_and_whitespace():
    assert not valid_call_id("")
    assert not valid_call_id("a" * (bounds.MAX_CALL_ID_CHARS + 1))
    assert not valid_call_id("call 1")
    assert not valid_call_id("call\t1")
    assert not valid_call_id(None)
