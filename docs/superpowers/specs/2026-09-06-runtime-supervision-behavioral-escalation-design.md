# Runtime Supervision, Behavioral Monitoring, and Escalation

**Date:** 2026-09-06  
**Status:** Design v3 candidate — implementation-ready pending owner approval  
**Working name:** DirtyWork Runtime Supervisor  
**Parent design:** Worker Action Firewall  
**Scope:** Cross-action runtime monitoring of worker behavior  
**Performance model:** Event-driven, bounded-state, no duplicate observation

## 0. Summary

DirtyWork has four distinct execution-safety layers:

```text
Action Firewall      = worker authority
Sandbox              = containment
Watchdog             = hard resource enforcement
Runtime Supervisor   = episode-level behavioral monitoring
```

The Runtime Supervisor correlates signals DirtyWork already observes across actions and turns.

It introduces:

- no polling thread;
- no additional Docker inspection;
- no additional process scanner;
- no additional filesystem scanner;
- no transcript rescanning;
- no model/provider call in the deterministic path.

The rollout is deliberately staged:

```text
baseline
→ observation
→ choose warning thresholds
→ warnings-enabled soak
→ choose termination thresholds
→ enforcement
```

Observation mode validates which patterns deserve warnings. Warnings-enabled soak validates whether workers adapt after actually seeing those warnings. Only that second stage can justify termination thresholds based on continued post-warning behavior.

## 1. Existing architecture

The Worker Action Firewall determines whether an untrusted worker action may execute. The Sandbox determines what an approved action can physically affect. The Docker Watchdog enforces hard runtime/resource boundaries. The Runtime Supervisor operates above those layers and asks whether the sequence of already-observed events indicates that continuing this execution episode is no longer acceptable.

It does not attempt to infer worker intent.

## 2. Performance contract

Runtime Supervisor consumes evidence DirtyWork already produced and must not independently rediscover it.

A routine CONTINUE causes no model call, Docker call, filesystem call, transcript event, or user-facing message.

## 3. Runner batching constraint

Providers may return multiple addressable tool calls in one model turn. Runner executes them sequentially inside that same turn and only after the batch completes delivers harness-authored follow-up text through the final tool result.

Therefore a warning generated during turn T is not visible to the worker until turn T has finished and the worker is asked to produce turn T+1. A later call in the same batch cannot count as behavior after that warning.

## 4. Supervisor inputs

All Supervisor inputs must be harness-normalized values. Raw worker-controlled structures do not enter rule evaluation directly.

### 4.1 Action Firewall

- `policy_deny`
- `semantic_unknown`
- normalized capabilities
- `reason_code`
- canonical action identity

These values enter Supervisor only after tool lookup, schema validation, unknown-key dropping, coercion, canonicalization, and capability analysis.

### 4.2 Sandbox

Normalized incidents:

- `stray_process`
- `sandbox_reset`

Supervisor consumes existing Sandbox outcomes rather than raw Docker/process data.

### 4.3 Runner

Initial aggregate-only signal:

- `worker_timeout`

`worker_timeout` is not an enforcement-rule input in the initial release. Stall, repeated failure, unchanged finish, and truncation remain owned by existing Runner behavior unless later deliberately transferred.

## 5. Single-owner rule for nudges

Every condition has one worker-facing nudge owner:

```text
stall                  -> Runner
repeated bash failure  -> RepeatTracker / Runner
timeout                -> Runner
stray process          -> Sandbox notice
sandbox reset          -> Sandbox notice
policy escalation      -> Runtime Supervisor
```

Supervisor may count a signal for correlation without generating a duplicate message for that same event.

## 6. SupervisorEvent

Conceptually:

```python
@dataclass(frozen=True)
class SupervisorEvent:
    kind: str
    turn: int
    tool: str | None
    attributes: dict
```

Initial kinds:

- `policy_deny`
- `semantic_unknown`
- `stray_process`
- `sandbox_reset`
- `worker_timeout`

Adapters validate and bound attributes before rule evaluation. Rule code never receives arbitrary raw worker dictionaries.

## 7. Input-hardening boundary

Supervisor rule functions operate only on closed, bounded internal structures. Unexpected fields are discarded or rejected by adapters, strings and collections are bounded, unknown reason codes map to a safe generic harness value, and recursive worker-provided structures do not survive the adapter boundary.

A worker must not be able to craft malformed input that makes Supervisor raise and disable itself.

## 8. RunEpisode

Conceptually:

```python
@dataclass
class RunEpisode:
    total_policy_denials: int = 0
    semantic_unknowns: int = 0
    stray_incidents: int = 0
    sandbox_reset_incidents: int = 0
    worker_timeouts: int = 0
    policy_warning_turn: int | None = None
```

Policy-denial rules share one warning class because they communicate the same instruction: stop requesting authority this run does not grant.

## 9. Unified policy warning class

If either repeated equivalent denials or high total denial volume triggers a policy WARN on turn T, set `policy_warning_turn = T`. Either policy rule may then treat future policy denials as post-warning only when `event.turn > policy_warning_turn`.

Do not issue two semantically identical policy warnings merely because two counters crossed thresholds.

## 10. Window storage

Turn-window rules must be represented by turn, not an unrelated fixed event count. One model turn can contain many tool calls, so a fixed-size event deque may evict still-valid evidence.

Preferred representation: bounded per-turn aggregates, evicted when `entry.turn < current_turn - W + 1`.

Event-count windows are allowed only for rules explicitly defined in relevant-event units.

## 11. Policy-denial identity

Exact equivalent-denial key:

```text
reason_code
+
capability_set
+
action_identity
```

The Supervisor does not re-canonicalize worker arguments. For bash, this exact key is intentionally narrow; differently flagged commands may not compare equivalent even if both are unauthorized remote writes.

Expected consequence: total-denial volume will likely carry more real-world signal than exact-equivalent denial matching. Observation should verify that expectation.

## 12. Policy-denial escalation

### 12.1 Equivalent denials

Observation mode can identify candidate warning thresholds/windows and clean-run frequency. It cannot validate post-warning termination behavior.

### 12.2 Total denials

Tracks all authorization denials in a bounded turn window. Observation mode can identify warning candidates only.

### 12.3 Termination

Termination requires:

```text
policy warning actually delivered
+
eligible denial on a later turn
+
measured continued-behavior threshold
```

That threshold comes only from a warnings-enabled soak.

## 13. Warning visibility

When policy WARN occurs on turn T, `policy_warning_turn = T`. The warning is visible only when the worker is asked to produce turn T+1. Therefore `post_warning = event.turn > policy_warning_turn`.

Same-turn calls never satisfy this predicate.

## 14. Supervisor decisions

```python
SupervisorDecision:
    disposition: continue | warn | terminate
    reason_code: str | None
    reason: str | None
```

WARN is queued for Runner's existing end-of-turn delivery. TERMINATE stops future worker-requested side effects.

## 15. Pre-action termination

A policy denial is observed before side effects. If that event causes TERMINATE, the triggering action remains denied and remaining unexecuted calls in the current batch are short-circuited.

## 16. Post-action termination

Sandbox incidents may be observed after a triggering action has already run as far as containment allowed. If they cause TERMINATE:

1. preserve the real triggering tool result;
2. preserve its transcript record;
3. append its tool message;
4. short-circuit remaining calls in the model turn;
5. create synthetic blocked results for those calls;
6. end `supervisor_stopped`.

Never rewrite history to imply the triggering action did not execute.

## 17. Multi-call batch short-circuit

When termination occurs on call i of N:

```text
1 ... i      retain actual results
i+1 ... N    do not execute
```

Every skipped addressable call receives:

```text
BLOCKED: run stopped by Runtime Supervisor before this tool call could execute.
```

Skipped calls have no side effect, do not count as policy denials or post-warning attempts, and do not generate Supervisor incidents. They exist to preserve valid assistant/tool-call history.

## 18. Incident normalization

One underlying containment incident advances one primary behavioral counter.

If stray cleanup succeeds: increment `stray_incidents`.

If stray cleanup cannot be verified and causes sandbox reset: increment `sandbox_reset_incidents` only, with cause metadata as appropriate. The more consequential normalized outcome wins.

Raw transcript evidence may still preserve multiple low-level facts where existing contracts require them.

## 19. Stray-process escalation

One contained stray incident continues. Observation mode determines whether repeated strays justify a warning. Any termination rule based on continued strays after warning requires warnings-enabled evidence. A warning-only production rule is acceptable.

## 20. Sandbox-reset escalation

One reset continues. Observation mode selects candidate warning behavior. Warnings-enabled soak measures whether workers continue producing resets after explicit guidance. Only then may a termination rule be introduced.

## 21. Worker timeout

`worker_timeout` is aggregate-only initially. It contributes run-end evidence and future research but has no standalone warning or termination rule.

## 22. Semantic uncertainty

`semantic_unknown` alone never warns or terminates. It remains aggregate evidence and a possible future compound-rule input.

## 23. Observation-mode self-disable

Observation mode must produce no worker-visible behavioral change. If Runtime Supervisor raises internally:

1. record `supervisor_disabled` if transcript writing is available;
2. disable Supervisor for the remainder of the run;
3. continue ordinary DirtyWork behavior.

No `supervisor_error` or `supervisor_stopped` may occur solely from observation-mode Supervisor failure.

## 24. Enforcement-mode self-disable

Default behavior is also to record failure, disable behavioral Supervisor, and continue under Action Firewall, Sandbox, Watchdog, and existing Runner guards. Supervisor is defense-in-depth, not the containment boundary.

However, `supervisor.enabled == false` during an enforcement-mode run is a mandatory orchestrator review signal. A completed run with unexpectedly disabled supervision must not be silently auto-accepted by an orchestrator.

## 25. Existing hard failures

Watchdog/container/resource failures retain existing semantics such as `sandbox_error` and `budget_exceeded`. Supervisor self-disable never disables those systems.

## 26. Transcript events

Sparse events:

- `supervisor` for real WARN / TERMINATE;
- `supervisor_disabled` for internal self-disable.

CONTINUE and observation-only hypothetical decisions do not create per-event transcript lines.

## 27. Observation evidence

Hypothetical outputs belong in run-end aggregates or bench-only artifacts, for example first would-warn turn, counts, and reason codes.

A `would_terminate` result in pure observation mode is counterfactual only; it cannot establish that a worker would continue after actual corrective feedback.

## 28. Run-end evidence

Operational summary should include mode, enabled/disabled status, warnings, terminations, denial counts, semantic unknowns, normalized sandbox incidents, and worker timeouts. Build these aggregates from in-memory state; do not rescan the transcript.

## 29. Performance measurement

Primary performance gate uses deterministic replay of recorded provider responses. Measure prepare, action analysis, policy, Supervisor observe, batch processing, p50/p95/p99, CPU, and transcript bytes without model/network variance.

Secondary live dogfood runs catch integration regressions but live wall clock is not the primary gate.

## 30. Performance acceptance

Static-tool safety/supervision path target: p95 < 1 ms on reference development hardware, with zero new external/system calls for routine deterministic processing.

Dynamic bash classification gets a separate measured threshold after implementation.

## 31. Phase 0 — Baseline

Collect historical/current strays, resets, and timeouts; after Firewall evidence exists, add equivalent denials, total denials, and `semantic_unknown`. Build deterministic response-replay benchmark.

## 32. Phase 1 — Plumbing

Introduce `SupervisorEvent`, `SandboxIncident`, `RunEpisode`, `SupervisorDecision`, and `RuntimeSupervisor`. All decisions CONTINUE. Self-disable on internal error.

## 33. Phase 2 — Signal normalization

Connect normalized policy denial, semantic uncertainty, stray, sandbox reset, and worker timeout with strict adapter validation, bounded inputs, incident deduplication, and unified policy-warning state.

## 34. Phase 3 — Observation mode

Run candidate rules without worker-visible warnings. Measure candidate warning frequency, first would-warn turn, false-positive clean runs, rule overlap, and equivalent-vs-total denial signal.

Observation may suggest warning thresholds/windows. It cannot validate post-warning termination behavior.

## 35. Phase 4 — Select warning rules

Each warning proposal documents signal, identity, threshold, window, historical incidence, clean-run incidence, false-positive analysis, and worker-facing guidance.

No termination threshold is claimed as validated here.

## 36. Phase 5 — Warnings-enabled soak

Enable WARN behavior but no behavioral termination. Measure whether workers adapt after receiving actual warnings: warnings issued, post-warning eligible turns, cessation/repetition rates, model/provider differences, extra turns, and turns saved through adaptation.

Only events where `event.turn > policy_warning_turn` count as post-warning behavior.

## 37. Phase 6 — Select termination rules

Use warnings-enabled soak data to propose termination thresholds. Each proposal documents warning class, post-warning signal, continued-behavior threshold, window, soak sample size, adaptation rate, false-termination analysis, and useful model/provider breakdowns.

If evidence is insufficient, do not add termination. A warning-only Supervisor is an acceptable outcome.

## 38. Phase 7 — Enforcement

Add `supervisor_stopped` only for approved evidence-backed rules. Preserve triggering evidence, valid batch history, synthetic results for skipped calls, and normal finalization behavior where safe.

## 39. Phase 8 — Compound rules

Deferred. Potential inputs include semantic uncertainty, policy denials, sandbox resets, and timeout aggregates. Each requires its own observation and, when warning-dependent, warnings-on validation.

## 40. Issue decomposition

- Epic — Runtime Supervision and Behavioral Escalation
- A — Behavioral baseline
- B — Deterministic harness performance replay
- C — RuntimeSupervisor core
- D — Normalize sandbox/Runner incidents
- E — Connect Worker Action Firewall
- F — Observation-mode evidence
- G — Select and implement warning thresholds
- H — Warnings-enabled soak
- I — Select termination thresholds
- J — `supervisor_stopped` + batch short-circuit
- K — Compound episode rules

## 41. Required tests

At minimum:

1. Observation Supervisor failure disables only Supervisor.
2. Enforcement Supervisor failure disables only Supervisor by default.
3. Enforcement disable is clearly surfaced in machine evidence.
4. Supervisor adapters bound/reject malformed internal inputs before rule code.
5. Raw worker argument structures never enter Supervisor rules.
6. Multiple calls may exist in one turn.
7. Same-turn calls do not count as post-warning.
8. Total-denial and equivalent-denial rules share one policy warning class.
9. Only one semantically identical policy warning is emitted unless guidance materially changes.
10. Triggering action result survives post-action termination.
11. Remaining batch calls do not execute after termination.
12. Remaining calls receive synthetic blocked results.
13. Failed stray cleanup + reset counts only as reset incident.
14. Successful stray cleanup counts only as stray incident.
15. Existing Sandbox notices are not duplicated.
16. Existing Runner timeout nudge remains the only timeout nudge.
17. Worker timeout cannot independently WARN or TERMINATE.
18. Exact-equivalent identity comes directly from Firewall.
19. Differently canonicalized bash calls are not exact-equivalent.
20. Total-denial rule captures varied unauthorized calls.
21. Turn-window storage retains all relevant evidence through the whole window.
22. Large multi-call batches cannot evict same-window evidence.
23. Observation hypothetical outputs do not create transcript spam.
24. Observation `would_terminate` is documented as counterfactual only.
25. Termination thresholds cannot be enabled without warnings-on evidence.
26. `semantic_unknown` alone does not warn/terminate.
27. Supervisor creates no background thread.
28. Supervisor performs no Docker, filesystem, or transcript scan.
29. Static-tool replay meets p95 performance gate.
30. Hard Watchdog semantics remain unchanged.
31. New run/resume starts fresh Supervisor state.

## 42. Resume semantics

Supervisor state does not inherit across runs or resumes. A resume is a new execution episode. Prior evidence remains available to the orchestrator through lineage.

## 43. Architectural invariants

1. Action Firewall owns authority.
2. Sandbox owns containment.
3. Watchdog owns hard resource enforcement.
4. Supervisor owns episode correlation.
5. Supervisor consumes only normalized harness inputs.
6. Raw worker structures never enter rule evaluation.
7. Supervisor is event-driven and introduces no new polling.
8. One physical incident advances one primary incident class.
9. Existing nudges have one owner.
10. Policy-denial rules share one policy warning class.
11. Warning visibility begins only on the next model turn.
12. Same-turn calls cannot constitute ignoring a warning.
13. Termination stops later unexecuted calls in the current batch.
14. Executed triggering results remain auditable.
15. Skipped calls receive structural synthetic results.
16. Observation errors self-disable without worker-visible behavioral effect.
17. Enforcement Supervisor failure defaults to self-disable, not global run failure.
18. Enforcement self-disable is an orchestrator review signal.
19. Observation validates warnings, not post-warning termination.
20. Termination thresholds require warnings-enabled evidence.
21. A warning-only production Supervisor is acceptable.
22. Windows are represented by their actual unit.
23. Turn windows evict by turn, not arbitrary event count.
24. Worker timeout is aggregate-only initially.
25. Equivalent-denial matching is intentionally narrow.
26. Total-denial detection is expected to carry broader policy signal.
27. Hypothetical observation output is aggregate/bench evidence, not transcript spam.
28. Performance is measured primarily through deterministic harness replay.
29. DirtyWork remains a bounded executor.

## 44. Final rollout

```text
Action Firewall deterministic core
        |
        v
Supervisor plumbing
        |
        v
Observation mode
        |
        | validates warning candidates
        v
Warning thresholds
        |
        v
Warnings-enabled soak
        |
        | measures real adaptation
        v
Termination threshold decision
        |
        +---- insufficient evidence ----> remain warning-only
        |
        v
supervisor_stopped enforcement
```

A counterfactual `would_terminate` is useful for rule mechanics, but cannot answer what a model would do after receiving corrective feedback. DirtyWork should terminate for continued-after-warning behavior only after measuring actual continued behavior after warnings were delivered.

## 45. Final design principle

The Runtime Supervisor should judge only behavior the worker had a fair opportunity to correct. It should consume only data the harness has already validated. It should never allow malformed worker input to become an easy way to disable monitoring.

The enforcement policy should be based on observed causal behavior:

> first observe the anomaly, then warn the worker, then measure whether workers adapt, and only then decide whether continued behavior justifies stopping execution.
