"""The deterministic policy engine: `evaluate` (pure, first match wins) and
the fail-closed entry points `decide` / `decide_batch` (spec §3, §4, §6,
§7, §8)."""
from __future__ import annotations

import posixpath
from dataclasses import dataclass, replace
from typing import Optional, Sequence

from .bounds import MAX_DETAIL_CHARS
from .capabilities import ActionKind, Capability, FILE_TARGET_RULES
from .errors import FirewallInternalError
from .normalize import WRITE_KINDS, Normalization, canonicalize, duplicate_positions
from .paths import TargetClass, normalize_path
from .reasons import ReasonCode
from .request import Rejection
from .schema import (
    ActionRequest,
    CanonicalAction,
    Decision,
    FirewallEvent,
    PolicyDecision,
)
from .shell import analyze_command

# Fixed harness sentences for the two file-target deny rules that carry
# one (spec §4): never the path, always within MAX_DETAIL_CHARS.
DETAIL_REPO_METADATA_TARGET = "write under the repository's .git"
DETAIL_PATH_OUTSIDE_WORKSPACE = "path resolves outside the worktree"

# Every ActionKind with a `path` field: all but bash and finish (spec §4).
_PATH_KINDS = frozenset(ActionKind) - {ActionKind.BASH, ActionKind.FINISH}

# reason_code -> capability, built once from FILE_TARGET_RULES: the
# authority a path-rule DENY asserts, added to the returned action so its
# event carries the capability the denial matched (spec §4).
_FILE_TARGET_CAPABILITY: dict[ReasonCode, Capability] = {
    reason_code: capability for capability, reason_code in FILE_TARGET_RULES
}


@dataclass(frozen=True)
class PolicyContext:
    """The sandbox mode and the worktree root forms for one run (spec §3).

    `worktree_roots` is supplied, never discovered: host mode requires it
    nonempty (absolute posix paths, longest first, each already normalized
    and never "/"); Docker mode requires it empty, since a container-scoped
    command has no worktree path to rewrite against and every absolute path
    is already outside it (spec §4 rule 3).
    """

    mode: str
    worktree_roots: "tuple[str, ...]"

    def __post_init__(self) -> None:
        if self.mode not in ("host", "docker"):
            raise FirewallInternalError(f"unknown mode: {self.mode!r}")
        if not isinstance(self.worktree_roots, tuple) or not all(
            isinstance(root, str) for root in self.worktree_roots
        ):
            raise FirewallInternalError("worktree_roots must be a tuple of str")
        for root in self.worktree_roots:
            if (
                not root
                or not posixpath.isabs(root)
                or root != posixpath.normpath(root)
                or root == "/"
            ):
                raise FirewallInternalError(f"invalid worktree root: {root!r}")
        is_host = self.mode == "host"
        if is_host and not self.worktree_roots:
            raise FirewallInternalError("host mode requires a nonempty worktree_roots")
        if not is_host and self.worktree_roots:
            raise FirewallInternalError("docker mode requires an empty worktree_roots")


@dataclass(frozen=True)
class Verdict:
    """`evaluate`'s result: the action (unchanged on ALLOW; with the
    matching capability added to its evidence on a bash or path-kind DENY)
    and its PolicyDecision (spec §6)."""

    action: CanonicalAction
    policy: PolicyDecision


@dataclass(frozen=True)
class Outcome:
    """`decide` / `decide_batch`'s result for one request (spec §7).
    `action` is None on a request-stage rejection, or on an internal error
    raised before an action existed (a `canonicalize` failure); it is
    retained on an internal error raised after `canonicalize` already
    produced one, so evaluating or eventing that action can still fail
    without discarding its evidence (spec §9.2). `event` is None only when
    building the event itself also failed."""

    action: Optional[CanonicalAction]
    policy: PolicyDecision
    event: Optional[FirewallEvent]
    dropped_keys: int


def _matching_root(path: str, roots: "tuple[str, ...]") -> Optional[str]:
    return next((root for root in roots if path == root or path.startswith(root + "/")), None)


def _deny_relative(rel: str, kind: ActionKind) -> Optional[PolicyDecision]:
    """`rel` is a relative posix path whose '..' have been resolved lexically
    (or that has none): first component '..' -> DENY PATH_OUTSIDE_WORKSPACE;
    first component '.git' and kind in WRITE_KINDS -> DENY
    REPO_METADATA_TARGET; else None. (`first component` = the first
    non-empty, non-'.' element of `rel.split("/")`; for "." it is none.)"""
    first_component = next((part for part in rel.split("/") if part not in ("", ".")), None)
    if first_component == "..":
        return PolicyDecision(
            Decision.DENY, ReasonCode.PATH_OUTSIDE_WORKSPACE, DETAIL_PATH_OUTSIDE_WORKSPACE
        )
    if first_component == ".git" and kind in WRITE_KINDS:
        return PolicyDecision(
            Decision.DENY, ReasonCode.REPO_METADATA_TARGET, DETAIL_REPO_METADATA_TARGET
        )
    return None


def _deny_verdict(action: CanonicalAction, policy: PolicyDecision) -> Verdict:
    """Build the Verdict for a path-rule DENY (spec §4): the capability
    `_FILE_TARGET_CAPABILITY` maps the denial's reason code to is added to
    the action's capabilities, so the event carries the authority the
    denial asserts. Every path-rule DENY returns through here, so none can
    forget the capability; only ALLOW returns the action unchanged. Raises
    FirewallInternalError if the reason code has no mapping -- an unmapped
    internal state fails closed rather than under-reporting."""
    capability = _FILE_TARGET_CAPABILITY.get(policy.reason_code)
    if capability is None:
        raise FirewallInternalError(f"unmapped path-denial reason code: {policy.reason_code!r}")
    enriched = replace(action, capabilities=frozenset(action.capabilities | {capability}))
    return Verdict(enriched, policy)


def _evaluate_path_kind(action: CanonicalAction, context: PolicyContext) -> Verdict:
    """The file-target rules of spec §4, first match wins, over the
    canonical path string and its re-derived TargetClass (the #136
    normalizer is idempotent, so this is exact). Backend-aware: a `.git` or
    `..` alias reachable only via the worktree root (host, absolute) or via
    a lexically-resolved relative parent reference (both modes) is
    classified the same as the literal form. Every DENY returned here
    carries, via `_deny_verdict`, the capability its reason code maps to;
    only ALLOW returns the action unchanged."""
    p = action.args.path
    t = normalize_path(p).target

    if t is TargetClass.REPO_METADATA and action.kind in WRITE_KINDS:
        return _deny_verdict(
            action,
            PolicyDecision(Decision.DENY, ReasonCode.REPO_METADATA_TARGET, DETAIL_REPO_METADATA_TARGET),
        )

    if t is TargetClass.OUTSIDE:
        root = _matching_root(p, context.worktree_roots) if p.startswith("/") else None
        if context.mode == "host" and root is not None:
            # The remainder is resolved lexically too, so `/wt/src/../.git/config`
            # and `/wt/a/../../etc` classify like their relative forms (spec §4).
            rel = posixpath.normpath(p[len(root):].lstrip("/") or ".")
            denial = _deny_relative(rel, action.kind)
            if denial is not None:
                return _deny_verdict(action, denial)
            return Verdict(action, PolicyDecision(Decision.ALLOW, None, ""))
        return _deny_verdict(
            action,
            PolicyDecision(
                Decision.DENY, ReasonCode.PATH_OUTSIDE_WORKSPACE, DETAIL_PATH_OUTSIDE_WORKSPACE
            ),
        )

    if t is TargetClass.PARENT_REF:
        denial = _deny_relative(posixpath.normpath(p), action.kind)
        if denial is not None:
            return _deny_verdict(action, denial)

    return Verdict(action, PolicyDecision(Decision.ALLOW, None, ""))


def _evaluate_bash(action: CanonicalAction, context: PolicyContext) -> Verdict:
    """The eight-rule shell denylist (spec §5, §6 step 4). A match adds the
    rule's capability to the action's evidence; no match ALLOWs unchanged."""
    match = analyze_command(action.args.command, mode=context.mode, worktree_roots=context.worktree_roots)
    if match is None:
        return Verdict(action, PolicyDecision(Decision.ALLOW, None, ""))
    denied_action = replace(action, capabilities=frozenset(action.capabilities | {match.capability}))
    return Verdict(denied_action, PolicyDecision(Decision.DENY, match.reason_code, match.legacy_reason))


def evaluate(action: CanonicalAction, context: PolicyContext) -> Verdict:
    """Deterministic ALLOW/DENY on one canonical action (spec §6). Pure: no
    I/O, no filesystem, no clock. Raises FirewallInternalError on any input
    outside its contract or on internal inconsistency; never catches
    anything, so a bug here is a bug, not a silent ALLOW."""
    if not isinstance(action, CanonicalAction):
        raise FirewallInternalError("action must be a CanonicalAction")
    if not isinstance(context, PolicyContext):
        raise FirewallInternalError("context must be a PolicyContext")

    if action.kind is ActionKind.FINISH:
        return Verdict(action, PolicyDecision(Decision.ALLOW, None, ""))
    if action.kind in _PATH_KINDS:
        return _evaluate_path_kind(action, context)
    if action.kind is ActionKind.BASH:
        return _evaluate_bash(action, context)
    # Unreachable: ActionKind is closed and CanonicalAction.__post_init__
    # checks membership, but the branch is explicit rather than implied,
    # so unrecognized internal state fails closed instead of falling
    # through to ALLOW (spec §6 step 5).
    raise FirewallInternalError(f"unhandled ActionKind: {action.kind!r}")


def _outcome_from(request: ActionRequest, normalization, context: PolicyContext) -> Outcome:
    """The post-`canonicalize` half of `decide` (spec §7), shared with
    `decide_batch` once its per-request `Normalization` is in hand."""
    if normalization.rejection is not None:
        policy = PolicyDecision(
            Decision.DENY, normalization.rejection.reason_code, normalization.rejection.detail
        )
        event = FirewallEvent.from_rejection(request, normalization.rejection)
        return Outcome(None, policy, event, 0)
    verdict = evaluate(normalization.action, context)
    event = FirewallEvent.from_action(verdict.action, verdict.policy)
    return Outcome(verdict.action, verdict.policy, event, normalization.dropped_keys)


_NO_EVENT_SUFFIX = "; no event"


def _internal_error_detail(exc: Exception) -> str:
    """`"firewall internal error: <exception class name>"`, cut to fit
    `MAX_DETAIL_CHARS` with room for `_NO_EVENT_SUFFIX`, so a class name long
    enough to overflow the bound cannot make `PolicyDecision` or `Rejection`
    raise outside the guard (spec §7). Never the message: it can carry
    worker bytes."""
    base = f"firewall internal error: {type(exc).__name__}"
    return base[: MAX_DETAIL_CHARS - len(_NO_EVENT_SUFFIX)]


def _internal_error_outcome(request: ActionRequest, exc: Exception) -> Outcome:
    """The fail-closed outcome for an unexpected exception when no action
    exists yet -- `canonicalize` itself raised, or `normalization` is a
    rejection (spec §7): `action` is None, since none was ever produced. The
    detail names only the exception class, never its message, since a
    message can carry worker bytes. If even building the synthetic
    request-stage event fails, the event is None and the detail gains a
    suffix saying so."""
    detail = _internal_error_detail(exc)
    try:
        rejection = Rejection(ReasonCode.FIREWALL_INTERNAL_ERROR, detail)
        event = FirewallEvent.from_rejection(request, rejection)
    except Exception:
        event = None
        detail = detail + _NO_EVENT_SUFFIX
    return Outcome(None, PolicyDecision(Decision.DENY, ReasonCode.FIREWALL_INTERNAL_ERROR, detail), event, 0)


def _action_internal_error_outcome(
    request: ActionRequest, action: CanonicalAction, dropped_keys: int, exc: Exception
) -> Outcome:
    """The fail-closed outcome for an unexpected exception raised by
    `evaluate` or by building its event, once `canonicalize` already
    produced `action` (spec §7, §9.2). Unlike `_internal_error_outcome`,
    the action is *retained*: discarding it would downgrade the event to a
    request-stage identity keyed only on tool name and reason code, which
    cannot tell two different targets of the same kind apart -- exactly the
    identity the Supervisor's exact-equivalent denial tracking needs from
    an action that already exists.

    Three-tier fallback, most specific first:
      1. an action-stage DENY event, built from `action` -- it carries the
         action's own capabilities and `action_identity`;
      2. if building that also raises, a request-stage synthetic-rejection
         event (the same shape `_internal_error_outcome` builds; detail
         unchanged);
      3. if that also raises, no event, and the detail gains a "; no
         event" suffix -- the same collapse `_internal_error_outcome` uses.

    `action` is returned in all three tiers, since canonicalization already
    succeeded. The detail names only the exception class, never its
    message."""
    detail = _internal_error_detail(exc)
    policy = PolicyDecision(Decision.DENY, ReasonCode.FIREWALL_INTERNAL_ERROR, detail)
    try:
        event = FirewallEvent.from_action(action, policy)
        return Outcome(action, policy, event, dropped_keys)
    except Exception:
        pass
    try:
        rejection = Rejection(ReasonCode.FIREWALL_INTERNAL_ERROR, detail)
        event = FirewallEvent.from_rejection(request, rejection)
    except Exception:
        event = None
        detail = detail + _NO_EVENT_SUFFIX
        policy = PolicyDecision(Decision.DENY, ReasonCode.FIREWALL_INTERNAL_ERROR, detail)
    return Outcome(action, policy, event, dropped_keys)


def decide(request: ActionRequest, context: PolicyContext) -> Outcome:
    """The one fail-closed entry point per addressable call (spec §7):
    never raises, never returns ALLOW from the internal-error path. Catches
    `Exception`, not `BaseException`, so KeyboardInterrupt and SystemExit
    still stop the run.

    Two guards, not one, because a failure after canonicalization succeeds
    must fall back differently depending on whether an action exists: a
    canonicalize failure (or a rejection's own outcome failing to build)
    falls back through `_internal_error_outcome` (spec §7); a failure while
    evaluating or eventing an already-canonicalized action falls back
    through `_action_internal_error_outcome`'s three-tier fallback instead,
    so the action's evidence is not discarded (spec §9.2)."""
    try:
        normalization = canonicalize(request)
    except Exception as exc:
        return _internal_error_outcome(request, exc)
    try:
        return _outcome_from(request, normalization, context)
    except Exception as exc:
        if normalization.rejection is None:
            return _action_internal_error_outcome(
                request, normalization.action, normalization.dropped_keys, exc
            )
        return _internal_error_outcome(request, exc)


def decide_batch(requests: "Sequence[ActionRequest]", context: PolicyContext) -> "list[Outcome]":
    """`decide` over a batch (spec §7): duplicate ids are found once via
    `duplicate_positions`; each request is then turned into an Outcome under
    the same two-guard, per-request fallback as `decide` (spec §9.2), so one
    request's failure never affects its neighbours. If `duplicate_positions`
    itself raises, every request gets the internal-error outcome."""
    try:
        positions = duplicate_positions(requests)
    except Exception as exc:
        return [_internal_error_outcome(request, exc) for request in requests]

    outcomes = []
    for index, request in enumerate(requests):
        try:
            if index in positions:
                normalization = Normalization(
                    None,
                    Rejection(
                        ReasonCode.CALL_ID_DUPLICATE,
                        f"duplicate call_id at batch index {index}",
                    ),
                    0,
                )
            else:
                normalization = canonicalize(request)
        except Exception as exc:
            outcomes.append(_internal_error_outcome(request, exc))
            continue
        try:
            outcomes.append(_outcome_from(request, normalization, context))
        except Exception as exc:
            if normalization.rejection is None:
                outcomes.append(
                    _action_internal_error_outcome(
                        request, normalization.action, normalization.dropped_keys, exc
                    )
                )
            else:
                outcomes.append(_internal_error_outcome(request, exc))
    return outcomes
