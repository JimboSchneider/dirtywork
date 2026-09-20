"""Unit tests for dirtywork/firewall_gate.py (issue #138 spec §10, "Gate
unit tests")."""
from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys

import pytest

from dirtywork import toolspec
from dirtywork.builtin_tools import default_registry
from dirtywork.firewall import ActionRequest, PolicyContext, decide
from dirtywork.firewall.errors import FirewallInternalError
from dirtywork.firewall.normalize import canonicalize
from dirtywork.firewall.reasons import ReasonCode
from dirtywork.firewall.schema import FirewallEvent
from dirtywork.providers import ToolCall

import dirtywork.firewall_gate as gate
from dirtywork.firewall_gate import (
    FIREWALL_DENIAL_EVENT,
    decide_turn,
    denial_event_fields,
    denial_strike,
    denial_text,
    execution_args,
    policy_context_for,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _req(tool, arguments, *, call_id="call_1", turn=1, batch_index=0, batch_size=1, parse_error=None):
    return ActionRequest(
        call_id=call_id,
        tool_name=tool,
        arguments=arguments,
        parse_error=parse_error,
        raw_chars=len(json.dumps(arguments)) if arguments is not None else 0,
        turn=turn,
        batch_index=batch_index,
        batch_size=batch_size,
    )


def _tc(call_id="call_1", name="read_file", arguments=None, error=None, raw_arguments="{}"):
    return ToolCall(id=call_id, name=name, arguments=arguments, error=error, raw_arguments=raw_arguments)


def _ctx_docker() -> PolicyContext:
    return PolicyContext("docker", ())


def _ctx_host(*roots: str) -> PolicyContext:
    return PolicyContext("host", tuple(roots))


def _outcome_for(tool, arguments, *, ctx=None, **req_kwargs):
    """A real Outcome from the firewall for `tool`/`arguments`, via `decide`."""
    return decide(_req(tool, arguments, **req_kwargs), ctx or _ctx_docker())


# --- FIREWALL_DENIAL_EVENT ---------------------------------------------------


def test_firewall_denial_event_constant():
    assert FIREWALL_DENIAL_EVENT == "firewall_denial"


# --- policy_context_for ------------------------------------------------------


def test_policy_context_for_docker_gives_empty_roots():
    ctx = policy_context_for("docker", None)
    assert ctx == PolicyContext("docker", ())


def test_policy_context_for_host_gives_given_and_resolved_longest_first(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    ctx = policy_context_for("none", link)

    given = os.path.normpath(link.as_posix())
    resolved = os.path.normpath(os.path.realpath(link))
    assert given != resolved
    assert ctx.mode == "host"
    assert len(ctx.worktree_roots) == 2
    assert set(ctx.worktree_roots) == {given, resolved}
    # longest first
    assert len(ctx.worktree_roots[0]) >= len(ctx.worktree_roots[1])


def test_policy_context_for_host_collapses_equal_forms(tmp_path):
    # No symlink in the path: the given and resolved forms are identical, so
    # they collapse to a single root.
    real = tmp_path / "plain"
    real.mkdir()
    ctx = policy_context_for("none", real)
    assert ctx.mode == "host"
    assert len(ctx.worktree_roots) == 1
    assert ctx.worktree_roots[0] == os.path.normpath(real.as_posix())


def test_policy_context_for_host_given_form_first_on_a_tie(tmp_path, monkeypatch):
    # Force the given and resolved forms to differ but have equal length, so
    # the tie-break ("the given form first") is exercised deterministically.
    real = tmp_path / "aaaaaa"
    real.mkdir()
    link = tmp_path / "bbbbbb"
    link.symlink_to(real)

    given = os.path.normpath(link.as_posix())
    resolved = os.path.normpath(os.path.realpath(link))
    assert len(given) == len(resolved)
    assert given != resolved

    ctx = policy_context_for("none", link)
    assert ctx.worktree_roots == (given, resolved)


def test_policy_context_for_none_worktree_raises_value_error():
    with pytest.raises(ValueError):
        policy_context_for("none", None)


def test_policy_context_for_relative_worktree_raises_value_error():
    with pytest.raises(ValueError):
        policy_context_for("none", pathlib.Path("relative/worktree"))


def test_policy_context_for_unknown_mode_raises_value_error(tmp_path):
    with pytest.raises(ValueError):
        policy_context_for("bogus-mode", tmp_path)


# --- decide_turn --------------------------------------------------------------


def test_decide_turn_matches_outcomes_to_calls_by_position():
    tool_calls = [
        _tc(call_id="a", name="read_file", arguments={"path": "x"}),
        _tc(call_id="b", name="bash", arguments={"command": "sudo ls"}),
    ]
    outcomes = decide_turn(tool_calls, turn=5, context=_ctx_docker())
    assert len(outcomes) == 2
    assert outcomes[0].policy.decision.value == "allow"
    assert outcomes[1].policy.decision.value == "deny"
    assert outcomes[1].policy.reason_code is ReasonCode.PRIVILEGE_ESCALATION


def test_decide_turn_empty_batch():
    assert decide_turn([], turn=1, context=_ctx_docker()) == []


def test_decide_turn_raises_firewall_internal_error_on_length_mismatch(monkeypatch):
    monkeypatch.setattr(gate, "decide_batch", lambda requests, context: [])
    with pytest.raises(FirewallInternalError):
        decide_turn([_tc()], turn=1, context=_ctx_docker())


# --- execution_args -----------------------------------------------------------

_registry = default_registry()

_EXECUTION_ARGS_CASES = [
    ("read_file", {"path": "./a//b", "offset": 0, "limit": 400}),
    ("read_file", {"path": "a"}),
    ("write_file", {"path": "a", "content": "c"}),
    ("append_file", {"path": "a", "text": "t"}),
    ("edit_file", {"path": "a", "old_string": "o", "new_string": "n"}),
    (
        "apply_edits",
        {
            "path": "a",
            "edits": [
                {"old": "o1", "new": "n1"},
                {"old": "o2", "new": "n2"},
            ],
        },
    ),
    ("insert_before", {"path": "a", "anchor": "x", "text": "t"}),
    ("insert_after", {"path": "a", "anchor": "x", "text": "t"}),
    ("list_dir", {}),
    ("list_dir", {"path": "sub"}),
    ("grep", {"pattern": "p"}),
    ("grep", {"pattern": "p", "path": "sub", "glob": "*.py"}),
    ("bash", {"command": "ls"}),
    ("bash", {"command": "ls", "timeout": 601}),
]
_EXECUTION_ARGS_IDS = [f"{i}:{tool}" for i, (tool, _) in enumerate(_EXECUTION_ARGS_CASES)]


def _canonicalize_ok(tool, arguments):
    normalization = canonicalize(_req(tool, arguments))
    assert normalization.rejection is None, normalization.rejection
    return normalization.action


@pytest.mark.parametrize("tool,arguments", _EXECUTION_ARGS_CASES, ids=_EXECUTION_ARGS_IDS)
def test_execution_args_idempotent_under_registry_validation(tool, arguments):
    action = _canonicalize_ok(tool, arguments)
    args = execution_args(action)
    spec = _registry.spec(tool)
    validated = toolspec._validate_args(spec, args)
    assert validated == args


def test_execution_args_edits_is_a_list_of_dicts():
    action = _canonicalize_ok(
        "apply_edits", {"path": "a", "edits": [{"old": "o", "new": "n"}]}
    )
    args = execution_args(action)
    assert isinstance(args["edits"], list)
    assert args["edits"] == [{"old": "o", "new": "n"}]


def test_execution_args_grep_glob_none_when_absent():
    action = _canonicalize_ok("grep", {"pattern": "p"})
    args = execution_args(action)
    assert args["glob"] is None


def test_execution_args_carries_no_unexpected_key():
    action = _canonicalize_ok("write_file", {"path": "a", "content": "c", "bogus": 1})
    args = execution_args(action)
    assert set(args) == {"path", "content"}


# --- denial_text ---------------------------------------------------------------


def test_denial_text_authority_prefix_is_blocked():
    outcome = _outcome_for("bash", {"command": "sudo ls"})
    text = denial_text(outcome, _tc(name="bash"), available_tools="")
    assert text.startswith("BLOCKED: ")


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("read_file", {}),  # malformed: argument_missing
        ("bash", {"command": "x" * 40000}),  # bounds: string_too_long
    ],
    ids=["malformed", "bounds"],
)
def test_denial_text_malformed_and_bounds_prefix_is_error(tool, arguments):
    outcome = _outcome_for(tool, arguments)
    assert outcome.policy.decision.value == "deny"
    text = denial_text(outcome, _tc(name=tool), available_tools="")
    assert text.startswith("ERROR: ")


def test_denial_text_internal_prefix_is_error(monkeypatch):
    def boom(request):
        raise RuntimeError("boom")

    monkeypatch.setattr("dirtywork.firewall.policy.canonicalize", boom)
    outcome = decide(_req("read_file", {"path": "a"}), _ctx_docker())
    assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    text = denial_text(outcome, _tc(name="read_file"), available_tools="")
    assert text.startswith("ERROR: ")


def test_denial_text_arguments_unparseable_with_decode_error():
    outcome = _outcome_for("read_file", None, parse_error="Expecting value")
    tc = _tc(name="read_file", arguments=None, error="Expecting value")
    text = denial_text(outcome, tc, available_tools="")
    assert outcome.policy.reason_code is ReasonCode.ARGUMENTS_UNPARSEABLE
    assert text == f"ERROR: {outcome.policy.detail}: Expecting value"


def test_denial_text_arguments_unparseable_without_decode_error():
    outcome = _outcome_for("read_file", None)
    tc = _tc(name="read_file", arguments=None, error=None)
    text = denial_text(outcome, tc, available_tools="")
    assert outcome.policy.reason_code is ReasonCode.ARGUMENTS_UNPARSEABLE
    assert text == f"ERROR: {outcome.policy.detail}"
    assert not text.endswith(": ")


def test_denial_text_tool_unknown_carries_available_tools_and_no_name_echo():
    long_name = "totally-bogus-tool-name-that-should-never-be-echoed"
    outcome = _outcome_for(long_name, {})
    tc = _tc(name=long_name)
    text = denial_text(outcome, tc, available_tools="read_file, write_file, bash, finish")
    assert outcome.policy.reason_code is ReasonCode.TOOL_UNKNOWN
    assert "Available: read_file, write_file, bash, finish" in text
    assert "To end the run call finish(summary=...)." in text
    assert long_name not in text


# --- denial_strike ---------------------------------------------------------------


def _outcome_with_code(reason_code: ReasonCode):
    """A stand-in Outcome carrying exactly `reason_code`, for the exhaustive
    denial_strike sweep -- no real Outcome is needed, only `.policy.reason_code`."""

    class _Policy:
        pass

    class _Outcome:
        pass

    policy = _Policy()
    policy.reason_code = reason_code
    outcome = _Outcome()
    outcome.policy = policy
    return outcome


_STRIKE_EXPECTATIONS = {
    ReasonCode.ARGUMENTS_UNPARSEABLE: ("strike", "malformed_args"),
    ReasonCode.ARGUMENTS_NOT_OBJECT: ("strike", "malformed_args"),
    ReasonCode.CALL_ID_INVALID: ("strike", "malformed_args"),
    ReasonCode.CALL_ID_DUPLICATE: ("strike", "malformed_args"),
    ReasonCode.TOOL_UNKNOWN: ("strike", "unknown_tool"),
    ReasonCode.TOOL_NAME_INVALID: ("strike", "unknown_tool"),
    ReasonCode.ARGUMENT_MISSING: ("strike", "bad_args"),
    ReasonCode.ARGUMENT_TYPE_INVALID: ("strike", "bad_args"),
    ReasonCode.ARGUMENT_UNEXPECTED: ("strike", "bad_args"),
    ReasonCode.PAYLOAD_TOO_LARGE: ("strike", "bad_args"),
    ReasonCode.STRING_TOO_LONG: ("strike", "bad_args"),
    ReasonCode.COLLECTION_TOO_LARGE: ("strike", "bad_args"),
    ReasonCode.NESTING_TOO_DEEP: ("strike", "bad_args"),
    ReasonCode.NUMBER_OUT_OF_RANGE: ("strike", "bad_args"),
    ReasonCode.BATCH_TOO_LARGE: ("strike", "bad_args"),
    ReasonCode.PRIVILEGE_ESCALATION: ("reset", None),
    ReasonCode.REPO_PUBLISH: ("reset", None),
    ReasonCode.REPO_CONTROL: ("reset", None),
    ReasonCode.HOST_FS_DESTRUCTIVE: ("reset", None),
    ReasonCode.REMOTE_CODE_EXEC: ("reset", None),
    ReasonCode.SYSTEM_CONTROL: ("reset", None),
    ReasonCode.HOST_FS_REDIRECT: ("reset", None),
    ReasonCode.HOST_FS_CHDIR: ("reset", None),
    ReasonCode.REPO_METADATA_TARGET: ("reset", None),
    ReasonCode.PATH_OUTSIDE_WORKSPACE: ("reset", None),
    ReasonCode.FIREWALL_INTERNAL_ERROR: ("hold", None),
}


def test_denial_strike_table_is_exhaustive_over_reason_code():
    # Fails if ReasonCode grows a member this test (or the expectation table
    # above) does not know about, so a new code cannot fall through silently.
    assert set(_STRIKE_EXPECTATIONS) == set(ReasonCode)


@pytest.mark.parametrize(
    "reason_code", list(ReasonCode), ids=[c.value for c in ReasonCode]
)
def test_denial_strike_one_case_per_reason_code(reason_code):
    verb, kind = denial_strike(_outcome_with_code(reason_code))
    expected_verb, expected_kind = _STRIKE_EXPECTATIONS[reason_code]
    assert verb == expected_verb
    assert kind == expected_kind


def test_denial_strike_unknown_code_raises():
    with pytest.raises(FirewallInternalError):
        denial_strike(_outcome_with_code(None))

    with pytest.raises(FirewallInternalError):
        denial_strike(_outcome_with_code("privilege_escalation"))  # a look-alike str, not the enum member


# --- denial_event_fields -----------------------------------------------------


def test_denial_event_fields_normal_shape_equals_event_to_dict_plus_tool():
    outcome = _outcome_for("bash", {"command": "sudo ls"}, call_id="call_9", turn=4)
    tc = _tc(call_id="call_9", name="bash")
    fields = denial_event_fields(outcome, tc, turn=4, tool="bash")

    expected = outcome.event.to_dict()
    expected["tool"] = "bash"
    assert fields == expected
    assert "event" not in fields
    assert "ts" not in fields


def test_denial_event_fields_event_missing_shape(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(FirewallEvent, "from_rejection", classmethod(lambda cls, *a, **kw: boom()))

    outcome = decide(_req("read_file", {}, call_id="cid", turn=7), _ctx_docker())
    assert outcome.event is None

    long_id = "x" * 300
    tc = _tc(call_id=long_id, name="read_file")
    fields = denial_event_fields(outcome, tc, turn=7, tool="read_file")

    assert fields == {
        "schema_version": 1,
        "turn": 7,
        "call_id": "x" * 256,
        "tool": "read_file",
        "decision": "deny",
        "reason_code": "firewall_internal_error",
        "reason_class": "internal",
        "event_missing": True,
    }
    assert "event" not in fields
    assert "ts" not in fields


# --- Isolation -----------------------------------------------------------------


def _forbidden_imports(path):
    """Like tests/test_firewall_policy.py's own _forbidden_imports, but the
    allowed prefixes are dirtywork.firewall and dirtywork.providers (spec
    §2)."""
    allowed = ("dirtywork.firewall", "dirtywork.providers")

    def _is_allowed(name):
        return name in allowed or any(name.startswith(prefix + ".") for prefix in allowed)

    tree = ast.parse(path.read_text())
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "dirtywork" or alias.name.startswith("dirtywork."):
                    if not _is_allowed(alias.name):
                        found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level >= 1:
                found.append("." * node.level + (node.module or ""))
            elif node.module and (node.module == "dirtywork" or node.module.startswith("dirtywork.")):
                if not _is_allowed(node.module):
                    found.append(node.module)
    return found


def test_import_isolation():
    path = _REPO_ROOT / "dirtywork" / "firewall_gate.py"
    assert _forbidden_imports(path) == []


def test_isolation_no_executor_modules_imported_by_firewall_gate():
    code = (
        "import sys, dirtywork.firewall_gate; "
        "bad = [m for m in ('dirtywork.tools','dirtywork.builtin_tools',"
        "'dirtywork.guardrails','dirtywork.runner','dirtywork.sandbox',"
        "'dirtywork.toolspec','dirtywork.__main__') "
        "if m in sys.modules]; "
        "sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        env={"PYTHONPATH": str(_REPO_ROOT), "PATH": os.environ.get("PATH", "")},
    )
    assert proc.returncode == 0


def test_subprocess_import_isolation_for_firewall_package_still_passes():
    # Pins spec §2's "the package's own import isolation is unchanged": the
    # existing dirtywork.firewall subprocess test (tests/test_firewall_request.py)
    # is untouched by this module; this is a second, independent witness.
    code = (
        "import sys, dirtywork.firewall; "
        "bad = [m for m in ('dirtywork.tools','dirtywork.builtin_tools',"
        "'dirtywork.guardrails','dirtywork.runner','dirtywork.sandbox') "
        "if m in sys.modules]; "
        "sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        env={"PYTHONPATH": str(_REPO_ROOT), "PATH": os.environ.get("PATH", "")},
    )
    assert proc.returncode == 0
