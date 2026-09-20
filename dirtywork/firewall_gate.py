"""The Runner-side gate onto the Worker Action Firewall (issue #138 spec §2).

Imports from `dirtywork.firewall` and `dirtywork.providers` (the `ToolCall`
type) only -- never `dirtywork.toolspec`, `dirtywork.tools`,
`dirtywork.guardrails` or `dirtywork.sandbox`. The registry's tool list and
the transcript name cap are passed in by the Runner, not discovered here.
`dirtywork.firewall` itself still imports nothing outside itself; this
module is the package's first runtime importer.
"""
from __future__ import annotations

import dataclasses
import os
import posixpath
from pathlib import Path
from typing import Optional, Sequence

from dirtywork.firewall import (
    FIREWALL_SCHEMA_VERSION,
    ActionRequest,
    CanonicalAction,
    FirewallInternalError,
    Outcome,
    PolicyContext,
    ReasonClass,
    ReasonCode,
    decide_batch,
    reason_class,
)
from dirtywork.firewall.bounds import MAX_CALL_ID_CHARS
from dirtywork.providers import ToolCall

FIREWALL_DENIAL_EVENT = "firewall_denial"


def policy_context_for(sandbox_mode: str, worktree: Optional[Path]) -> PolicyContext:
    """The only place the CLI's sandbox vocabulary meets the Firewall's
    (spec §3). `"docker"` -> `PolicyContext("docker", ())`. `"none"` ->
    host mode with the worktree's two root forms (the given form and the
    resolved form, longest first, the given form first on a tie; equal
    forms collapse to one root). Any other mode string raises `ValueError`,
    as does a `None` or non-absolute worktree."""
    if sandbox_mode == "docker":
        return PolicyContext("docker", ())
    if sandbox_mode == "none":
        if worktree is None:
            raise ValueError("worktree must not be None for sandbox_mode 'none'")
        given = posixpath.normpath(worktree.as_posix())
        resolved = posixpath.normpath(os.path.realpath(worktree))
        for form in (given, resolved):
            if not form.startswith("/"):
                raise ValueError(f"worktree root must be absolute: {form!r}")
        if given == resolved:
            roots = (given,)
        elif len(resolved) > len(given):
            roots = (resolved, given)
        else:
            roots = (given, resolved)
        return PolicyContext("host", roots)
    raise ValueError(f"unknown sandbox mode: {sandbox_mode!r}")


def decide_turn(tool_calls: Sequence[ToolCall], *, turn: int, context: PolicyContext) -> list[Outcome]:
    """Evaluate one turn's batch, up front (spec §4). Builds an
    `ActionRequest` per call in order, calls `decide_batch` once, and
    returns its list. Outcomes are matched to calls by position, never by
    call id, because ids can repeat. Raises `FirewallInternalError` if the
    returned list length ever differs from the batch length; `decide_batch`
    never raises by contract, so that would be a harness bug, not a
    bypass."""
    requests = [
        ActionRequest.from_tool_call(tc, turn=turn, batch_index=i, batch_size=len(tool_calls))
        for i, tc in enumerate(tool_calls)
    ]
    outcomes = decide_batch(requests, context)
    if len(outcomes) != len(requests):
        raise FirewallInternalError(
            f"decide_batch returned {len(outcomes)} outcomes for {len(requests)} requests"
        )
    return outcomes


def denial_text(outcome: Outcome, tc: ToolCall, *, available_tools: str) -> str:
    """The result text a denied call gets: `"PREFIX: detail"` (spec §5).
    `detail` is `outcome.policy.detail`, already bounded by
    `MAX_DETAIL_CHARS`. The prefix follows the reason class: `BLOCKED` for
    authority, `ERROR` for malformed, bounds and internal. Two request-stage
    codes append guidance: `arguments_unparseable` appends the provider's
    decode error when it recorded one; `tool_unknown` appends the
    available-tools sentence, with no echo of the offending name."""
    policy = outcome.policy
    code = policy.reason_code
    klass = reason_class(code)
    prefix = "BLOCKED" if klass is ReasonClass.AUTHORITY else "ERROR"
    detail = policy.detail
    if code is ReasonCode.ARGUMENTS_UNPARSEABLE and tc.error:
        detail = f"{detail}: {tc.error}"
    elif code is ReasonCode.TOOL_UNKNOWN:
        detail = f"{detail}. Available: {available_tools}. To end the run call finish(summary=...)."
    return f"{prefix}: {detail}"


# reason_code -> (verb, FAILURE_KINDS member or None), exhaustive over
# ReasonCode (spec §5). Prefixes may depend on the reason class; strikes may
# not, because `call_id_duplicate` is a bounds-class code with a
# malformed-class strike.
_STRIKE_TABLE: "dict[ReasonCode, tuple]" = {
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


def denial_strike(outcome: Outcome) -> tuple[str, Optional[str]]:
    """One of three verbs, because "no strike" is two different behaviors
    (spec §5): `("strike", kind)` where `kind` is a member of the Runner's
    `FAILURE_KINDS`, `("reset", None)` for an authority denial, which clears
    the failure counters exactly as a `BLOCKED:` result does today, and
    `("hold", None)` for `firewall_internal_error`, which leaves the
    counters untouched. Raises `FirewallInternalError` on a reason code it
    does not know."""
    code = outcome.policy.reason_code
    if not isinstance(code, ReasonCode):
        raise FirewallInternalError(f"not a ReasonCode: {code!r}")
    try:
        return _STRIKE_TABLE[code]
    except KeyError:
        raise FirewallInternalError(f"unmapped reason code: {code!r}") from None


def denial_event_fields(outcome: Outcome, tc: ToolCall, *, turn: int, tool: str) -> dict:
    """The `firewall_denial` event's fields only: no `ts` and no `event`
    key, since `Transcript.write` supplies both and would raise on a
    duplicate (spec §2, §7). The normal shape is `outcome.event.to_dict()`
    plus `tool`. When `outcome.event` is `None` -- every event factory the
    outcome's own path reached has failed -- the `event_missing` record of
    spec §7 is built instead, with `tc.id` cut to `MAX_CALL_ID_CHARS`."""
    if outcome.event is not None:
        fields = outcome.event.to_dict()
        fields["tool"] = tool
        return fields
    call_id = tc.id[:MAX_CALL_ID_CHARS] if isinstance(tc.id, str) else tc.id
    return {
        "schema_version": FIREWALL_SCHEMA_VERSION,
        "turn": turn,
        "call_id": call_id,
        "tool": tool,
        "decision": "deny",
        "reason_code": ReasonCode.FIREWALL_INTERNAL_ERROR.value,
        "reason_class": ReasonClass.INTERNAL.value,
        "event_missing": True,
    }


def execution_args(action: CanonicalAction) -> dict:
    """The canonical action's arguments in the shape the registry's own
    validation is idempotent on (spec §6): `dataclasses.asdict(action.args)`
    with the `apply_edits` `edits` tuple converted to a list of `{"old",
    "new"}` dicts (already dicts via `asdict`'s recursion into the nested
    `Edit` dataclass; only the outer tuple needs converting to a list), and
    optional fields (`grep.glob`) passed through as `None`. Carries no
    unexpected key: every key comes straight from the canonical dataclass's
    own fields."""
    args = dataclasses.asdict(action.args)
    if "edits" in args:
        args["edits"] = list(args["edits"])
    return args
