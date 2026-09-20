"""Tests for dirtywork.firewall.policy: PolicyContext invariants (including
worktree root validation), the file-target rules (backend-aware `.git`/`..`
alias classification included), the bash denylist wiring, evaluate's
fail-hard contract, decide/decide_batch's fail-closed contract, the parent
design §19 invariants this layer owns, and import isolation (spec §11)."""
from __future__ import annotations

import ast
import json
import pathlib
from dataclasses import replace

import pytest

from dirtywork.firewall.bounds import MAX_DETAIL_CHARS
from dirtywork.firewall.capabilities import ActionKind, Capability
from dirtywork.firewall.errors import FirewallInternalError
from dirtywork.firewall.normalize import canonicalize
from dirtywork.firewall.policy import (
    DETAIL_PATH_OUTSIDE_WORKSPACE,
    DETAIL_REPO_METADATA_TARGET,
    Outcome,
    PolicyContext,
    Verdict,
    decide,
    decide_batch,
    evaluate,
)
from dirtywork.firewall.reasons import ReasonCode
from dirtywork.firewall.schema import (
    ActionRequest,
    Decision,
    FirewallEvent,
    SemanticStatus,
    action_identity,
)
from dirtywork.firewall.shell import SHELL_RULES


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


def _action(tool, arguments):
    return canonicalize(_req(tool, arguments)).action


def _ctx_host(*roots: str) -> PolicyContext:
    return PolicyContext(mode="host", worktree_roots=tuple(roots))


def _ctx_docker() -> PolicyContext:
    return PolicyContext(mode="docker", worktree_roots=())


# --- group 1 (PolicyContext invariants) -------------------------------------


def test_policy_context_invariants():
    with pytest.raises(FirewallInternalError):
        PolicyContext(mode="sandbox", worktree_roots=("/x",))
    with pytest.raises(FirewallInternalError):
        PolicyContext(mode="host", worktree_roots=["/x"])
    with pytest.raises(FirewallInternalError):
        PolicyContext(mode="host", worktree_roots=())
    with pytest.raises(FirewallInternalError):
        PolicyContext(mode="docker", worktree_roots=("/x",))
    PolicyContext(mode="host", worktree_roots=("/x",))
    PolicyContext(mode="docker", worktree_roots=())


def test_policy_context_root_validation():
    for roots in (
        ("",),
        ("wt",),
        ("/",),
        ("/wt/",),
        ("/wt/../x",),
        ("/wt", ""),
    ):
        with pytest.raises(FirewallInternalError):
            PolicyContext(mode="host", worktree_roots=roots)
    PolicyContext(mode="host", worktree_roots=("/wt",))
    PolicyContext(mode="host", worktree_roots=("/private/tmp/wt", "/tmp/wt"))


def test_policy_context_empty_root_cannot_allow_etc_passwd():
    # A prior version matched any absolute path's prefix against an empty
    # root string, silently ALLOWing anything outside the worktree; an
    # empty root must now fail construction instead (spec §3).
    with pytest.raises(FirewallInternalError):
        PolicyContext(mode="host", worktree_roots=("",))


# --- group 2 (file-target rules) --------------------------------------------

_PATH_KIND_ARGS = {
    "read_file": lambda p: {"path": p},
    "write_file": lambda p: {"path": p, "content": "x"},
    "append_file": lambda p: {"path": p, "text": "x"},
    "edit_file": lambda p: {"path": p, "old_string": "a", "new_string": "b"},
    "apply_edits": lambda p: {"path": p, "edits": [{"old": "a", "new": "b"}]},
    "insert_before": lambda p: {"path": p, "anchor": "a", "text": "b"},
    "insert_after": lambda p: {"path": p, "anchor": "a", "text": "b"},
    "list_dir": lambda p: {"path": p},
    "grep": lambda p: {"pattern": "x", "path": p},
}


@pytest.mark.parametrize("kind", sorted(_PATH_KIND_ARGS))
def test_benign_relative_path_allowed_for_every_path_kind(kind):
    # No option-like rule any more (decision reversed): a plain relative
    # path -- including one that used to be flagged as option-like, such as
    # a leading "-" -- ALLOWs for every path-bearing ActionKind, in both
    # modes, so kind coverage is preserved (spec §4).
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action(kind, _PATH_KIND_ARGS[kind]("src/thing.py"))
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.ALLOW
        assert v.action is action


def test_git_metadata_read_allow_write_deny():
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        read_action = _action("read_file", {"path": ".git/config"})
        v = evaluate(read_action, ctx)
        assert v.policy.decision is Decision.ALLOW
        assert v.action is read_action

        write_action = _action("write_file", {"path": ".git/config", "content": "x"})
        v = evaluate(write_action, ctx)
        assert v.policy.decision is Decision.DENY
        assert v.policy.reason_code is ReasonCode.REPO_METADATA_TARGET
        assert v.policy.detail == DETAIL_REPO_METADATA_TARGET


def test_gitignore_write_allowed():
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("write_file", {"path": ".gitignore", "content": "x"})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.ALLOW


def test_leading_parent_component_outside_both_modes():
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("read_file", {"path": "../x"})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.DENY
        assert v.policy.reason_code is ReasonCode.PATH_OUTSIDE_WORKSPACE
        assert v.policy.detail == DETAIL_PATH_OUTSIDE_WORKSPACE


def test_absolute_outside_denied_both_modes():
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("read_file", {"path": "/etc/passwd"})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.DENY
        assert v.policy.reason_code is ReasonCode.PATH_OUTSIDE_WORKSPACE


def test_host_absolute_inside_root_allowed():
    ctx = _ctx_host("/wt")
    for path in ("/wt", "/wt/src/x.py"):
        action = _action("read_file", {"path": path})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.ALLOW, path


def test_host_absolute_outside_root_denied():
    ctx = _ctx_host("/wt")
    for path in ("/wtx/y", "/other/x"):
        action = _action("read_file", {"path": path})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.DENY, path
        assert v.policy.reason_code is ReasonCode.PATH_OUTSIDE_WORKSPACE


def test_docker_absolute_always_denied_even_if_root_like():
    ctx = _ctx_docker()
    action = _action("read_file", {"path": "/wt/src/x.py"})
    v = evaluate(action, ctx)
    assert v.policy.decision is Decision.DENY
    assert v.policy.reason_code is ReasonCode.PATH_OUTSIDE_WORKSPACE


def test_parent_ref_shallow_allowed_both_modes():
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("read_file", {"path": "src/a/../x.py"})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.ALLOW, ctx.mode


def test_parent_ref_escaping_denied_both_modes():
    # posixpath.normpath("a/../../x") == "../x": a leading ".." after lexical
    # resolution is outside in both modes now (spec §4).
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("read_file", {"path": "a/../../x"})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.DENY, ctx.mode
        assert v.policy.reason_code is ReasonCode.PATH_OUTSIDE_WORKSPACE


def test_parent_ref_non_dotdot_lookalike_allowed_both_modes():
    # posixpath.normpath("src/../..cache/data") == "../..cache/data" isn't
    # quite right -- "..cache" is a real component, not a ".." reference, so
    # it never denies (spec §4).
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("read_file", {"path": "src/../..cache/data"})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.ALLOW, ctx.mode


def test_parent_ref_git_alias_write_denied_both_modes():
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("write_file", {"path": "src/../.git/config", "content": "x"})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.DENY, ctx.mode
        assert v.policy.reason_code is ReasonCode.REPO_METADATA_TARGET
        assert v.policy.detail == DETAIL_REPO_METADATA_TARGET


def test_host_absolute_git_alias_via_root_write_denied():
    ctx = _ctx_host("/wt")
    action = _action("write_file", {"path": "/wt/.git/config", "content": "x"})
    v = evaluate(action, ctx)
    assert v.policy.decision is Decision.DENY
    assert v.policy.reason_code is ReasonCode.REPO_METADATA_TARGET
    assert v.policy.detail == DETAIL_REPO_METADATA_TARGET


def test_host_absolute_git_alias_via_root_read_allowed():
    ctx = _ctx_host("/wt")
    action = _action("read_file", {"path": "/wt/.git/config"})
    v = evaluate(action, ctx)
    assert v.policy.decision is Decision.ALLOW


def test_host_absolute_gitignore_via_root_write_allowed():
    ctx = _ctx_host("/wt")
    action = _action("write_file", {"path": "/wt/.gitignore", "content": "x"})
    v = evaluate(action, ctx)
    assert v.policy.decision is Decision.ALLOW


def test_host_absolute_internal_parent_ref_via_root_allowed():
    ctx = _ctx_host("/wt")
    action = _action("read_file", {"path": "/wt/src/a/../x.py"})
    v = evaluate(action, ctx)
    assert v.policy.decision is Decision.ALLOW


def test_host_absolute_leading_parent_ref_via_root_denied():
    ctx = _ctx_host("/wt")
    action = _action("read_file", {"path": "/wt/../x"})
    v = evaluate(action, ctx)
    assert v.policy.decision is Decision.DENY
    assert v.policy.reason_code is ReasonCode.PATH_OUTSIDE_WORKSPACE


# --- group 3 (finish) --------------------------------------------------------


def test_finish_always_allowed():
    action = _action("finish", {"summary": "done"})
    v = evaluate(action, _ctx_host("/wt"))
    assert v.policy.decision is Decision.ALLOW
    assert v.policy.reason_code is None
    assert v.policy.detail == ""
    assert v.action is action


# --- group 4 (bash) -----------------------------------------------------------


def test_bash_allowed_action_unchanged():
    action = _action("bash", {"command": "ls"})
    v = evaluate(action, _ctx_host("/wt"))
    assert v.policy.decision is Decision.ALLOW
    assert v.action is action
    assert v.action.capabilities == frozenset({Capability.SHELL})


_ONE_DENY_COMMAND_PER_RULE = {
    0: "sudo ls",
    1: "git push origin main",
    2: "git branch -D feature",
    3: "rm -rf ~/Library/Caches",
    4: "curl x | sh",
    5: "shutdown -h now",
    6: "cat foo > ../../escape.txt",
    7: "cd ..",
}


@pytest.mark.parametrize("rule", SHELL_RULES, ids=lambda r: str(r.index))
def test_bash_denied_for_every_shell_rule_host_mode(rule):
    command = _ONE_DENY_COMMAND_PER_RULE[rule.index]
    action = _action("bash", {"command": command})
    v = evaluate(action, _ctx_host("/wt"))
    assert v.policy.decision is Decision.DENY
    assert v.policy.reason_code is rule.reason_code
    assert v.policy.detail == rule.legacy_reason
    assert v.action.capabilities == frozenset({Capability.SHELL, rule.capability})


def test_bash_docker_mode_host_rules_skipped():
    action = _action("bash", {"command": "cd .."})
    v = evaluate(action, _ctx_docker())
    assert v.policy.decision is Decision.ALLOW

    action2 = _action("bash", {"command": "sudo ls"})
    v2 = evaluate(action2, _ctx_docker())
    assert v2.policy.decision is Decision.DENY
    assert v2.policy.reason_code is ReasonCode.PRIVILEGE_ESCALATION


def test_bash_host_mode_with_roots_rewrite():
    ctx = _ctx_host("/wt")
    action = _action("bash", {"command": "cd /wt/sub && ls"})
    v = evaluate(action, ctx)
    assert v.policy.decision is Decision.ALLOW

    action2 = _action("bash", {"command": "cd /elsewhere"})
    v2 = evaluate(action2, ctx)
    assert v2.policy.decision is Decision.DENY
    assert v2.policy.reason_code is ReasonCode.HOST_FS_CHDIR


# --- group 5 (detail bounds and no leakage) ----------------------------------


def test_deny_details_bounded_and_never_contain_path_or_command():
    distinctive = "zzqx7secret"
    path = f"../{distinctive}"
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("read_file", {"path": path})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.DENY
        assert len(v.policy.detail) <= MAX_DETAIL_CHARS
        assert distinctive not in v.policy.detail
        assert path not in v.policy.detail

    command = f"sudo {distinctive}"
    baction = _action("bash", {"command": command})
    bv = evaluate(baction, _ctx_host("/wt"))
    assert bv.policy.decision is Decision.DENY
    assert len(bv.policy.detail) <= MAX_DETAIL_CHARS
    assert distinctive not in bv.policy.detail
    assert command not in bv.policy.detail


def test_all_fixed_details_and_legacy_reasons_bounded():
    for detail in (DETAIL_REPO_METADATA_TARGET, DETAIL_PATH_OUTSIDE_WORKSPACE):
        assert len(detail) <= MAX_DETAIL_CHARS
    for rule in SHELL_RULES:
        assert len(rule.legacy_reason) <= MAX_DETAIL_CHARS


# --- group 6 (evaluate raises) ------------------------------------------------


def test_evaluate_raises_on_dict_action():
    with pytest.raises(FirewallInternalError):
        evaluate({"kind": "read_file"}, _ctx_host("/wt"))


def test_evaluate_raises_on_normalization_instead_of_action():
    n = canonicalize(_req("read_file", {"path": "x"}))
    with pytest.raises(FirewallInternalError):
        evaluate(n, _ctx_host("/wt"))


def test_evaluate_raises_on_rejection_instead_of_action():
    n = canonicalize(_req("nope", {}))
    assert n.rejection is not None
    with pytest.raises(FirewallInternalError):
        evaluate(n.rejection, _ctx_host("/wt"))


def test_evaluate_raises_on_dict_context():
    action = _action("read_file", {"path": "x"})
    with pytest.raises(FirewallInternalError):
        evaluate(action, {"mode": "host", "worktree_roots": ()})


def test_evaluate_raises_on_weird_context_mode_for_bash():
    ctx = object.__new__(PolicyContext)
    object.__setattr__(ctx, "mode", "weird")
    object.__setattr__(ctx, "worktree_roots", ())
    action = _action("bash", {"command": "ls"})
    with pytest.raises(FirewallInternalError):
        evaluate(action, ctx)


# --- group 7 (decide) ---------------------------------------------------------


def test_decide_accept_path():
    req = _req("read_file", {"path": "x"})
    outcome = decide(req, _ctx_host("/wt"))
    assert isinstance(outcome, Outcome)
    assert outcome.action is not None
    assert outcome.policy.decision is Decision.ALLOW
    assert outcome.event is not None
    assert outcome.event.stage == "action"
    assert outcome.event.decision is Decision.ALLOW
    assert outcome.dropped_keys == 0


def test_decide_rejection_path():
    req = _req("nope", {})
    outcome = decide(req, _ctx_host("/wt"))
    assert outcome.action is None
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.TOOL_UNKNOWN
    assert outcome.event is not None
    assert outcome.event.stage == "request"
    assert outcome.dropped_keys == 0


def test_decide_dropped_keys_propagate():
    req = _req("read_file", {"path": "x", "bogus": 1})
    outcome = decide(req, _ctx_host("/wt"))
    assert outcome.policy.decision is Decision.ALLOW
    assert outcome.dropped_keys == 1


def test_decide_internal_error_canonicalize_raises(monkeypatch):
    def boom(request):
        raise RuntimeError("secret text")

    monkeypatch.setattr("dirtywork.firewall.policy.canonicalize", boom)
    req = _req("read_file", {"path": "x"})
    outcome = decide(req, _ctx_host("/wt"))
    assert outcome.action is None
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcome.policy.detail == "firewall internal error: RuntimeError"
    assert "secret" not in outcome.policy.detail
    assert outcome.event is not None
    assert outcome.event.stage == "request"


def test_decide_internal_error_evaluate_raises(monkeypatch):
    def boom(action, context):
        raise FirewallInternalError("nope")

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", boom)
    req = _req("read_file", {"path": "x"})
    outcome = decide(req, _ctx_host("/wt"))
    # The canonical action already existed when evaluate raised, so it is
    # retained and the event is action-stage, not discarded down to a
    # request-stage identity (PR #180 review, spec §9.2).
    assert outcome.action == canonicalize(req).action
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcome.policy.detail == "firewall internal error: FirewallInternalError"
    assert outcome.event is not None
    assert outcome.event.stage == "action"


def test_decide_internal_error_double_failure_no_event(monkeypatch):
    def boom_canonicalize(request):
        raise RuntimeError("secret text")

    def boom_event(request, rejection):
        raise RuntimeError("event boom")

    monkeypatch.setattr("dirtywork.firewall.policy.canonicalize", boom_canonicalize)
    monkeypatch.setattr(FirewallEvent, "from_rejection", boom_event)

    req = _req("read_file", {"path": "x"})
    outcome = decide(req, _ctx_host("/wt"))
    assert outcome.action is None
    assert outcome.event is None
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcome.policy.detail.endswith("; no event")
    assert "secret" not in outcome.policy.detail


def test_decide_keyboard_interrupt_propagates(monkeypatch):
    def boom(request):
        raise KeyboardInterrupt()

    monkeypatch.setattr("dirtywork.firewall.policy.canonicalize", boom)
    req = _req("read_file", {"path": "x"})
    with pytest.raises(KeyboardInterrupt):
        decide(req, _ctx_host("/wt"))


# --- group 8 (decide_batch) ---------------------------------------------------


def test_decide_batch_mixed_in_order():
    ctx = _ctx_host("/wt")
    requests = [
        _req("read_file", {"path": "x"}, call_id="c1"),
        _req("bash", {"command": "sudo ls"}, call_id="c2"),
        _req("nope", {}, call_id="c3"),
        _req("read_file", {"path": "y"}, call_id="c1"),  # duplicate call_id
    ]
    outcomes = decide_batch(requests, ctx)
    assert len(outcomes) == 4

    assert outcomes[0].policy.decision is Decision.ALLOW
    assert outcomes[0].action is not None

    assert outcomes[1].policy.decision is Decision.DENY
    assert outcomes[1].policy.reason_code is ReasonCode.PRIVILEGE_ESCALATION

    assert outcomes[2].policy.decision is Decision.DENY
    assert outcomes[2].policy.reason_code is ReasonCode.TOOL_UNKNOWN
    assert outcomes[2].event.stage == "request"

    assert outcomes[3].policy.decision is Decision.DENY
    assert outcomes[3].policy.reason_code is ReasonCode.CALL_ID_DUPLICATE
    assert outcomes[3].event.stage == "request"


def test_decide_batch_one_bash_failure_does_not_affect_neighbours(monkeypatch):
    ctx = _ctx_host("/wt")
    real_evaluate = evaluate

    def wrapped(action, context):
        if action.kind is ActionKind.BASH:
            raise RuntimeError("boom")
        return real_evaluate(action, context)

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", wrapped)

    requests = [
        _req("read_file", {"path": "x"}, call_id="c1"),
        _req("bash", {"command": "ls"}, call_id="c2"),
        _req("read_file", {"path": "y"}, call_id="c3"),
    ]
    outcomes = decide_batch(requests, ctx)
    assert outcomes[0].policy.decision is Decision.ALLOW
    assert outcomes[1].policy.decision is Decision.DENY
    assert outcomes[1].policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcomes[2].policy.decision is Decision.ALLOW


def test_decide_batch_duplicate_positions_raises_every_outcome_internal_error(monkeypatch):
    # decide_batch no longer calls canonicalize_batch; the batch-wide guard
    # is now around duplicate_positions.
    def boom(requests):
        raise RuntimeError("batch boom")

    monkeypatch.setattr("dirtywork.firewall.policy.duplicate_positions", boom)
    requests = [
        _req("read_file", {"path": "x"}, call_id="c1"),
        _req("read_file", {"path": "y"}, call_id="c2"),
    ]
    outcomes = decide_batch(requests, _ctx_host("/wt"))
    assert len(outcomes) == 2
    for outcome in outcomes:
        assert outcome.action is None
        assert outcome.policy.decision is Decision.DENY
        assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
        assert outcome.policy.detail == "firewall internal error: RuntimeError"


def test_decide_batch_canonicalize_raises_for_one_request_only(monkeypatch):
    real_canonicalize = canonicalize

    def wrapped(request):
        if request.call_id == "c2":
            raise RuntimeError("boom")
        return real_canonicalize(request)

    monkeypatch.setattr("dirtywork.firewall.policy.canonicalize", wrapped)

    requests = [
        _req("read_file", {"path": "x"}, call_id="c1"),
        _req("read_file", {"path": "y"}, call_id="c2"),
        _req("read_file", {"path": "z"}, call_id="c3"),
    ]
    outcomes = decide_batch(requests, _ctx_host("/wt"))
    assert outcomes[0].policy.decision is Decision.ALLOW
    assert outcomes[1].policy.decision is Decision.DENY
    assert outcomes[1].policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcomes[2].policy.decision is Decision.ALLOW


def test_decide_batch_duplicate_detection():
    ctx = _ctx_host("/wt")
    requests = [
        _req("read_file", {"path": "x"}, call_id="a"),
        _req("read_file", {"path": "y"}, call_id="b"),
        _req("read_file", {"path": "z"}, call_id="a"),
    ]
    outcomes = decide_batch(requests, ctx)
    assert outcomes[0].policy.decision is Decision.ALLOW
    assert outcomes[1].policy.decision is Decision.ALLOW
    assert outcomes[2].policy.decision is Decision.DENY
    assert outcomes[2].policy.reason_code is ReasonCode.CALL_ID_DUPLICATE


def test_decide_batch_empty():
    assert decide_batch([], _ctx_host("/wt")) == []


# --- group 9 (parent design §19 invariants) -----------------------------------


def test_invariant_raw_dict_cannot_reach_rules():
    with pytest.raises(FirewallInternalError):
        evaluate({"kind": "read_file", "args": {"path": "x"}}, _ctx_host("/wt"))


def test_invariant_malformed_input_cannot_become_allow_through_exception_handling():
    self_ref: dict = {}
    self_ref["self"] = self_ref
    req = ActionRequest(
        call_id="call_1",
        tool_name="read_file",
        arguments=self_ref,
        parse_error=None,
        raw_chars=100,
        turn=1,
        batch_index=0,
        batch_size=1,
    )
    outcome = decide(req, _ctx_host("/wt"))
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.decision is not Decision.ALLOW


def test_invariant_precedence_is_deterministic():
    ctx = _ctx_host("/wt")
    requests = [
        _req("bash", {"command": cmd}, call_id=f"c{i}")
        for i, cmd in enumerate(_ONE_DENY_COMMAND_PER_RULE.values())
    ]
    outcomes1 = decide_batch(requests, ctx)
    outcomes2 = decide_batch(requests, ctx)
    assert outcomes1 == outcomes2


def test_invariant_event_fields_are_members_of_closed_vocabularies():
    ctx = _ctx_host("/wt")
    requests = [
        _req("read_file", {"path": "x"}, call_id="c1"),
        _req("bash", {"command": "sudo ls"}, call_id="c2"),
        _req("nope", {}, call_id="c3"),
        _req("bash", {"command": "ls"}, call_id="c4"),
    ]
    capability_values = {c.value for c in Capability}
    for outcome in decide_batch(requests, ctx):
        event = outcome.event
        assert event is not None
        if event.reason_code is not None:
            assert isinstance(event.reason_code, ReasonCode)
        for cap in event.capabilities:
            assert cap in capability_values
        assert isinstance(event.action_identity, str) and event.action_identity


def test_invariant_semantic_unknown_never_denies_without_a_shell_rule():
    ctx = _ctx_host("/wt")
    shell_codes = {rule.reason_code for rule in SHELL_RULES}

    deny_action = _action("bash", {"command": "sudo ls"})
    v = evaluate(deny_action, ctx)
    assert v.policy.decision is Decision.DENY
    assert v.policy.reason_code in shell_codes

    allow_action = _action("bash", {"command": "ls"})
    v2 = evaluate(allow_action, ctx)
    assert v2.policy.decision is Decision.ALLOW
    assert v2.action.semantic_status is SemanticStatus.UNKNOWN


def test_invariant_internal_failure_fails_closed(monkeypatch):
    def boom(request):
        raise RuntimeError("boom")

    monkeypatch.setattr("dirtywork.firewall.policy.canonicalize", boom)
    req = _req("read_file", {"path": "x"})
    outcome = decide(req, _ctx_host("/wt"))
    assert outcome.policy.decision is Decision.DENY
    assert outcome.action is None


# --- group 10 (import isolation) ----------------------------------------------


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
    for rel in ("dirtywork/firewall/shell.py", "dirtywork/firewall/policy.py"):
        assert _forbidden_imports(root / rel) == [], rel


# --- group 11 (host absolute-inside remainder is resolved lexically) ---------


def test_host_absolute_inside_git_alias_through_dotdot_is_denied():
    verdict = evaluate(_action("write_file", {"path": "/wt/src/../.git/config", "content": "x"}), _ctx_host("/wt"))
    assert verdict.policy.reason_code is ReasonCode.REPO_METADATA_TARGET


def test_host_absolute_inside_escape_through_dotdot_is_denied():
    verdict = evaluate(_action("read_file", {"path": "/wt/a/../../etc/passwd"}), _ctx_host("/wt"))
    assert verdict.policy.reason_code is ReasonCode.PATH_OUTSIDE_WORKSPACE


def test_host_absolute_inside_benign_dotdot_is_allowed():
    verdict = evaluate(_action("read_file", {"path": "/wt/src/a/../x.py"}), _ctx_host("/wt"))
    assert verdict.policy.decision is Decision.ALLOW


# --- group 12 (file-target denials carry their capability, PR #180 review) ---


@pytest.mark.parametrize(
    "mode, roots, kind, path, extra, expected",
    [
        ("docker", (), "write_file", "src/../.git/config", {"content": "x"}, frozenset({"repo_control"})),
        ("docker", (), "read_file", "a/../../x", {}, frozenset({"host_fs"})),
        ("host", ("/wt",), "write_file", "/wt/.git/config", {"content": "x"}, frozenset({"host_fs", "repo_control"})),
        ("host", ("/wt",), "write_file", "/wt/src/../.git/config", {"content": "x"}, frozenset({"repo_control"})),
        ("docker", (), "write_file", ".git/config", {"content": "x"}, frozenset({"repo_control"})),
        ("docker", (), "read_file", "/etc/passwd", {}, frozenset({"host_fs"})),
    ],
    ids=[
        "docker_write_git_alias_via_parent_ref",
        "docker_read_outside_via_leading_dotdot",
        "host_root_git_alias_write",
        "host_root_git_alias_via_parent_ref_write",
        "docker_write_literal_git_metadata_unchanged",
        "docker_read_absolute_outside_unchanged",
    ],
)
def test_file_target_denial_event_capabilities(mode, roots, kind, path, extra, expected):
    ctx = PolicyContext(mode=mode, worktree_roots=roots)
    arguments = {"path": path, **extra}
    outcome = decide(_req(kind, arguments), ctx)
    assert outcome.policy.decision is Decision.DENY
    capabilities = set(outcome.event.to_dict()["capabilities"])
    assert expected <= capabilities


def test_allow_path_verdict_action_is_input_unchanged():
    action = _action("read_file", {"path": "src/thing.py"})
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        verdict = evaluate(action, ctx)
        assert verdict.policy.decision is Decision.ALLOW
        assert verdict.action == action


def test_deny_path_verdict_action_differs_only_in_capabilities():
    action = _action("write_file", {"path": "src/../.git/config", "content": "x"})
    verdict = evaluate(action, _ctx_docker())
    assert verdict.policy.decision is Decision.DENY
    assert verdict.action != action
    assert replace(verdict.action, capabilities=action.capabilities) == action


# --- group 13 (evaluate-stage internal error keeps the canonical action, PR #180 review) ---


def test_decide_action_internal_error_keeps_action_and_builds_action_stage_event(monkeypatch):
    def boom(action, context):
        raise RuntimeError("secret")

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", boom)
    req = _req("read_file", {"path": "x", "bogus": 1})
    outcome = decide(req, _ctx_host("/wt"))

    expected_action = canonicalize(req).action
    assert outcome.action == expected_action
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcome.policy.detail == "firewall internal error: RuntimeError"
    assert "secret" not in outcome.policy.detail
    assert outcome.event is not None
    assert outcome.event.stage == "action"
    assert outcome.event.kind is ActionKind.READ_FILE
    assert set(outcome.event.capabilities) == {"workspace_read"}
    assert outcome.event.action_identity == action_identity(outcome.action)
    assert outcome.dropped_keys == 1


def test_decide_action_internal_error_identity_distinguishes_different_targets(monkeypatch):
    def boom(action, context):
        raise RuntimeError("boom")

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", boom)
    ctx = _ctx_host("/wt")

    outcome_a1 = decide(_req("read_file", {"path": "a.txt"}), ctx)
    outcome_a2 = decide(_req("read_file", {"path": "a.txt"}), ctx)
    outcome_b = decide(_req("read_file", {"path": "b.txt"}), ctx)

    assert outcome_a1.event.action_identity == outcome_a2.event.action_identity
    assert outcome_a1.event.action_identity != outcome_b.event.action_identity


def test_decide_action_internal_error_double_failure_falls_back_to_request_stage(monkeypatch):
    def boom_evaluate(action, context):
        raise RuntimeError("boom")

    def boom_from_action(action, policy):
        raise RuntimeError("event boom")

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", boom_evaluate)
    monkeypatch.setattr(FirewallEvent, "from_action", boom_from_action)

    req = _req("read_file", {"path": "x"})
    outcome = decide(req, _ctx_host("/wt"))

    assert outcome.action == canonicalize(req).action
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcome.policy.detail == "firewall internal error: RuntimeError"
    assert outcome.event is not None
    assert outcome.event.stage == "request"


def test_decide_action_internal_error_triple_failure_no_event(monkeypatch):
    def boom_evaluate(action, context):
        raise RuntimeError("boom")

    def boom_from_action(action, policy):
        raise RuntimeError("event boom")

    def boom_from_rejection(request, rejection):
        raise RuntimeError("rejection boom")

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", boom_evaluate)
    monkeypatch.setattr(FirewallEvent, "from_action", boom_from_action)
    monkeypatch.setattr(FirewallEvent, "from_rejection", boom_from_rejection)

    req = _req("read_file", {"path": "x"})
    outcome = decide(req, _ctx_host("/wt"))

    assert outcome.action == canonicalize(req).action
    assert outcome.event is None
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcome.policy.detail.endswith("; no event")
    assert outcome.policy.detail.startswith("firewall internal error: RuntimeError")


def test_decide_action_internal_error_keyboard_interrupt_propagates(monkeypatch):
    def boom(action, context):
        raise KeyboardInterrupt()

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", boom)
    req = _req("read_file", {"path": "x"})
    with pytest.raises(KeyboardInterrupt):
        decide(req, _ctx_host("/wt"))


def test_decide_batch_action_internal_error_middle_request_only(monkeypatch):
    real_evaluate = evaluate

    def wrapped(action, context):
        if action.args.path == "y":
            raise RuntimeError("boom")
        return real_evaluate(action, context)

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", wrapped)

    requests = [
        _req("read_file", {"path": "x"}, call_id="c1"),
        _req("read_file", {"path": "y"}, call_id="c2"),
        _req("read_file", {"path": "z"}, call_id="c3"),
    ]
    outcomes = decide_batch(requests, _ctx_host("/wt"))

    assert outcomes[0].policy.decision is Decision.ALLOW
    assert outcomes[0].action is not None

    middle = outcomes[1]
    assert middle.policy.decision is Decision.DENY
    assert middle.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert middle.action is not None
    assert middle.action == canonicalize(requests[1]).action
    assert middle.event is not None
    assert middle.event.stage == "action"

    assert outcomes[2].policy.decision is Decision.ALLOW
    assert outcomes[2].action is not None


# --- group 14 (the internal-error detail is bounded before construction) ----


_LongName = type("E" * 300, (RuntimeError,), {})


@pytest.mark.parametrize("stage", ["canonicalize", "evaluate"])
def test_internal_error_detail_is_bounded_for_a_long_exception_class_name(monkeypatch, stage):
    def boom(*args, **kwargs):
        raise _LongName("x")
    monkeypatch.setattr("dirtywork.firewall.policy." + stage, boom)
    outcome = decide(_req("read_file", {"path": "x"}), _ctx_docker())
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert len(outcome.policy.detail) <= MAX_DETAIL_CHARS
    assert outcome.policy.detail.startswith("firewall internal error: EEEE")
    assert outcome.event is not None


def test_internal_error_detail_with_no_event_suffix_stays_bounded(monkeypatch):
    def boom(*args, **kwargs):
        raise _LongName("x")
    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", boom)
    monkeypatch.setattr(FirewallEvent, "from_action", boom)
    monkeypatch.setattr(FirewallEvent, "from_rejection", boom)
    outcome = decide(_req("read_file", {"path": "x"}), _ctx_docker())
    assert outcome.event is None
    assert outcome.policy.detail.endswith("; no event")
    assert len(outcome.policy.detail) <= MAX_DETAIL_CHARS
