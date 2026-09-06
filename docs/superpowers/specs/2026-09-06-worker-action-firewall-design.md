# Worker Action Firewall Design

Date: 2026-09-06
Status: implementation design

## 1. Purpose

Worker Action Firewall is DirtyWork's authoritative worker-authority boundary. It sits between provider/tool-call parsing and action execution and decides whether a requested action is permitted before any side effect occurs.

The core invariant is:

> No worker-requested action reaches an executor or Runtime Supervisor as raw worker-controlled input.

The Firewall converts an untrusted worker request into a bounded canonical action, evaluates deterministic policy against that representation, emits machine-readable evidence, and only then permits executor handoff.

This layer is distinct from containment. The Firewall answers "may the worker attempt this action?" Sandbox answers "where can execution affect?" Watchdog enforces hard resource limits. Runtime Supervisor correlates normalized behavior across actions. `--verify` checks correctness, and the orchestrator decides acceptance/review.

## 2. Goals

- One authoritative normalization pass for worker-requested actions.
- Deterministic, auditable allow/deny policy.
- No side effect before an ALLOW decision.
- Bounded, versioned evidence suitable for transcript/run artifacts and Runtime Supervisor.
- Stable canonical action identity for exact-equivalent behavioral correlation.
- Preserve existing DirtyWork tool-result/chat-history contracts, including multi-tool batches.
- Keep the hot path local and cheap enough for the deterministic performance replay in Runtime Supervisor #123.
- Make policy failures explicit rather than silently weakening authority controls.

## 3. Non-goals

- The Firewall is not an OS sandbox and does not replace Docker/process/filesystem containment.
- It is not a semantic model classifier or general prompt-injection detector.
- It does not assign a universal trust/suspicion score.
- It does not perform episode-level escalation; that belongs to Runtime Supervisor.
- It does not rescan transcripts, inspect Docker state, or poll the filesystem.
- It does not try to enumerate every possible shell effect as a security boundary. Existing best-effort bash guardrails remain defense in depth until deliberately migrated.

## 4. Placement and ownership

Conceptual flow:

`provider response -> tool-call adapter -> Firewall normalize -> Firewall policy -> evidence -> ALLOW executor / DENY structural tool result`

For every addressable worker tool call, Firewall analysis happens before the executor. Runtime Supervisor may observe the Firewall-owned normalized evidence, but it must never receive raw arguments and must never perform a second canonicalization pass.

The Firewall owns:

- canonical action schema;
- argument validation and normalization needed for policy;
- capability classification;
- canonical action identity;
- policy decision and `reason_code`;
- bounded policy evidence.

Executors own execution semantics after ALLOW. Existing Sandbox/Runner layers retain containment and lifecycle ownership.

## 5. Trust boundary

Everything originating in a model/tool-call payload is untrusted, including tool name, argument names, values, nested structures, strings, paths, command text, and provider-specific extras.

The adapter may parse provider syntax, but parsing is not authorization. The Firewall must construct its own closed, typed representation and discard/reject unexpected fields rather than forwarding arbitrary worker dictionaries.

Only harness-generated enums/identifiers may enter policy and Supervisor rule code.

## 6. Canonical action contract

Introduce conceptual immutable types similar to:

- `ActionRequest`: addressable tool-call identity plus raw parsed request; exists only at the adapter/Firewall boundary.
- `CanonicalAction`: bounded Firewall-owned representation.
- `Capability`: closed harness enum describing authority required by the action.
- `PolicyDecision`: `ALLOW` or `DENY` plus harness-owned reason code and evidence.
- `FirewallEvent`: bounded, versioned machine evidence derived from `CanonicalAction` and `PolicyDecision`.

A canonical action contains only fields required for policy, execution handoff, evidence, and stable identity. It must not retain arbitrary provider extras.

All strings and collections have explicit size/count bounds. Invalid types, excessive nesting, oversized values, unknown tools, and unsupported argument shapes fail closed with deterministic harness-owned reason codes.

## 7. Normalization rules

Normalization is tool-specific and deterministic.

The Firewall must distinguish syntactic validity from semantic certainty:

- syntactically invalid or unsupported input -> DENY;
- deterministically unauthorized input -> DENY;
- permitted and deterministically understood input -> ALLOW with normal semantic classification;
- permitted input whose exact effect cannot be safely classified by the deterministic analyzer -> policy decides conservatively and may emit `semantic_unknown` only where execution is otherwise allowed by the relevant containment/authority model.

`semantic_unknown` is evidence, not wrongdoing. It must never itself mean DENY, WARN, or TERMINATE unless a future policy design explicitly changes its authorization semantics with tests and evidence.

Normalization must be idempotent at the contract level: downstream code consumes the canonical object rather than reparsing raw worker arguments.

## 8. Capability model

Policy is expressed in terms of a closed capability set rather than ad-hoc free-text labels. Capabilities should be narrow enough to audit but stable enough that equivalent actions classify consistently.

Initial implementation should derive the concrete capability vocabulary from current DirtyWork tools and existing guardrails. Likely categories include read-only workspace access, workspace mutation, process/shell execution, network-related authority where applicable, and harness/repository-control operations that workers must not perform.

The checked-in capability table is the contract. Adding a capability or changing a tool's capability mapping requires explicit tests.

Unknown capability values are impossible from worker input because only the Firewall creates them. Unknown internal enum/state is an internal Firewall failure, not a worker-selectable bypass.

## 9. Canonical action identity

Firewall produces a bounded `action_identity` used for audit and exact-equivalent Runtime Supervisor policy-denial tracking.

Identity must be intentionally narrow: two actions are identical only when their policy-relevant canonical form is equivalent. Do not collapse materially different commands/paths/targets merely to increase behavioral signal.

Identity should be derived from the canonical representation, never raw provider JSON. If hashing is used, hash a versioned deterministic serialization and retain enough non-sensitive structured evidence to explain the decision without requiring reverse reconstruction.

Runtime Supervisor #126 tracks exact-equivalent denial identity as:

`reason_code + capability_set + action_identity`

and separately tracks total denial volume. Firewall therefore must keep all three fields stable and bounded.

## 10. Policy decision semantics

The decision surface is deliberately small:

- `ALLOW`: executor may receive the canonical execution payload.
- `DENY`: executor must not run; DirtyWork returns an addressable structural tool result explaining the denial and records machine evidence.

There is no Firewall WARN state. Worker guidance for a single denial belongs to the denial result; repeated-pattern warnings belong to Runtime Supervisor.

Policy order is part of the observable contract when multiple rules could reject one action. The implementation must define deterministic precedence and test it, similar to the existing ordered guardrail reason contract.

Reason codes are closed harness-owned identifiers. Human-readable text may evolve, but automation should key on reason codes rather than prose.

## 11. Fail-closed semantics

Unlike Runtime Supervisor, which may self-disable because it is an advisory/correlation layer, the Action Firewall is an authority boundary and must not silently self-disable.

If Firewall normalization or policy evaluation encounters an internal error for a worker action:

- do not execute that action;
- produce a structural blocked/error tool result that preserves chat-history validity;
- record a bounded `firewall_error`/internal reason code and machine evidence;
- surface the run for orchestrator review according to existing Runner failure/finalization semantics.

A Firewall exception must never become implicit ALLOW.

## 12. Multi-tool batch semantics

Each call in a provider batch is independently addressable and passes through the Firewall immediately before its potential execution.

A DENY has no side effect from the denied call. By default it does not cancel unrelated later calls in the same batch; existing Runner batch semantics continue unless another layer, such as evidence-backed Runtime Supervisor termination, short-circuits them.

Denied calls still receive structural tool results so strict provider history remains valid.

Runtime Supervisor warning visibility remains turn-based: a policy denial on turn T can contribute to a warning queued for the next model turn, but later calls in the same batch cannot be treated as ignoring that warning.

## 13. Static tools and shell actions

All worker-addressable tools, including apparently safe/static file tools, must cross the same Firewall boundary. Static tools should not bypass policy merely because they do not invoke bash.

Shell analysis remains deterministic and bounded. Existing `guardrails.py` rules are explicitly documented as best-effort accident guards rather than a security boundary. Firewall implementation should reuse/migrate deterministic policy knowledge without pretending regex analysis can prove arbitrary shell semantics.

Where command semantics cannot be deterministically classified, record the bounded semantic status required by policy/evidence; rely on Sandbox for containment rather than expanding an unbounded parser or model classifier.

## 14. Evidence contract

For each policy-relevant action, expose a bounded versioned event containing only harness-normalized fields needed by consumers, conceptually:

- schema version;
- turn/call identity;
- normalized tool/action kind;
- capability set;
- decision;
- reason code when denied/error;
- canonical action identity;
- semantic classification, including `semantic_unknown` where applicable.

Do not copy raw worker argument dictionaries into Supervisor events. Transcript evidence may retain existing user-visible/tool-result information required for debugging, but the Supervisor-facing event is a separate hardened contract.

Avoid per-ALLOW transcript spam. Aggregate or existing tool-call evidence should carry normal allowed-path information where practical. Denials/errors must remain auditable.

## 15. Runtime Supervisor integration

Runtime Supervisor #126 depends on deterministic Firewall policy/evidence.

The Supervisor consumes only:

- policy denial;
- `semantic_unknown` aggregate;
- normalized capability set;
- harness `reason_code`;
- Firewall-owned canonical action identity.

The Firewall does not know Supervisor thresholds or episode state. The Supervisor does not reinterpret Firewall arguments or policy.

This separation prevents malformed worker-controlled data from reaching Supervisor rule code and avoids divergent canonicalization between authorization and behavioral monitoring.

## 16. Compatibility

Preserve:

- provider-specific assistant/tool history shapes;
- addressable result for every tool call;
- current Runner finalization/export behavior where safe;
- additive machine-evidence compatibility for existing readers;
- current sandbox-mode distinctions unless policy explicitly supersedes them.

Changes to existing `guardrail_block.reason` behavior require deliberate migration because rule order/reason text has been treated as a transcript contract. Prefer introducing stable Firewall reason codes while preserving compatibility during migration.

## 17. Performance

Firewall analysis is on the hot path. It must use only in-memory/local deterministic computation during action analysis: no model call, network call, transcript scan, Docker query, process scan, or filesystem walk added merely for authorization.

Runtime Supervisor benchmark #123 is the shared performance gate. Static-tool safety/supervision should remain capable of meeting the design target of p95 < 1 ms on reference development hardware, with Firewall normalization/policy latency measured separately.

## 18. Rollout

Implement in layers so policy can be proven without accidentally changing authority semantics:

1. Inventory current worker tools and existing guardrail/containment policy.
2. Define closed canonical schema, capabilities, reason codes, and identity.
3. Add deterministic normalization and contract tests.
4. Add policy evaluation with parity fixtures for existing behavior.
5. Route all worker-addressable tools through Firewall before execution.
6. Emit bounded evidence and integrate Runtime Supervisor #126.
7. Migrate/de-duplicate overlapping legacy guardrail paths only after parity and performance evidence.

For authority rules, do not use an observation mode that would permit known-denied actions merely to collect data. Shadow/parity comparison is acceptable only when the existing authority control still blocks the action.

## 19. Security invariants

Tests must demonstrate:

- raw worker dictionaries cannot reach policy rule functions or Supervisor;
- unknown/oversized/malformed action input cannot become ALLOW through exception handling;
- DENY causes zero executor side effects;
- every addressable denied call gets a structural result;
- policy precedence is deterministic;
- capability set/reason code/action identity are bounded harness-owned values;
- canonical identity is stable for equivalent canonical actions and distinct for materially different policy-relevant actions;
- `semantic_unknown` cannot independently escalate behavior;
- static tools cannot bypass Firewall;
- Firewall internal failure fails closed;
- Sandbox/Watchdog remain active and independent.

## 20. Done criteria

Worker Action Firewall is complete when every worker-addressable action crosses one authoritative normalization/policy boundary before execution, deterministic authority decisions have stable reason codes and canonical identities, denied actions cannot side-effect, bounded evidence feeds Runtime Supervisor without raw worker input, compatibility/performance gates pass, and legacy overlapping policy paths are either intentionally retained or safely de-duplicated with tests.