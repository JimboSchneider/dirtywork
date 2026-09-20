# Worker Action Firewall E: structural DENY results and Runner batch integration

Issue #138. Parent design: `2026-09-06-worker-action-firewall-design.md` (§4, §6, §10, §11, §12, §14, §16, §19). Predecessors: `2026-09-08-issue-135-firewall-schema-design.md` (`ActionRequest`, `CanonicalAction`, `FirewallEvent`, the stage split), `2026-09-19-issue-136-firewall-normalization-design.md` (`canonicalize`, the field table) and `2026-09-19-issue-137-firewall-policy-design.md` (`PolicyContext`, `decide`, `decide_batch`, the fail-closed contract). Baseline behavior: `2026-09-06-worker-action-firewall-inventory.md` §5 and §6. The strict-template invariant this issue extends is `2026-08-23-harness-followups-after-tool-results-design.md` (issue #60).

Written 2026-09-20 against `main` at `ebd15dc`, where `dirtywork/firewall/` re-exports 48 names, `decide` and `decide_batch` exist and nothing outside the package imports it. The Runner's per-call loop (`dirtywork/runner.py`, `one_turn`) dispatches every ordinary call through `ToolRegistry.execute` and short-circuits `finish` before the registry; the legacy guardrails run inside the tool functions and signal a denial with a `BLOCKED:` text prefix that the registry sniffs afterwards.

## 1. Scope

This issue puts the Firewall's decision in the Runner's dispatch path. After it, every addressable worker call is decided before it can have a side effect, a denied call never reaches the executor or the finish branch, every addressable call the loop reaches still receives exactly one tool result keyed by its original id, and a batch keeps its order and identity. It ships:

- a Runner-side module, `dirtywork/firewall_gate.py`, the package's first runtime importer: the context builder, the turn gate, the denial text, strike and event helpers, and the canonical handoff;
- one optional constructor argument on `Runner`, one `decide_batch` call per model turn, and one branch in the per-call loop that sits above the `finish` special case;
- the CLI building the `PolicyContext` once per run from its sandbox mode and worktree and passing it in;
- a `firewall_denial` transcript event and two run-end counters, preserved on the CLI's failure path;
- tests that prove zero executor invocation on every denied and internal-error path, and that canonical arguments are what the executor receives.

Out of scope, with the owning issue: result completeness for calls after a budget or sandbox exit (existing semantics, now pinned by a test; `resume` starts a fresh conversation and never replays history, so no strict template ever sees the cut batch); shadow or parity comparison against `guardrail_block` and the compatibility mapping of its `reason` (#140); Supervisor evidence and counters (#141); removing or de-duplicating the legacy in-tool checks (#143); static-tool-specific hardening (#139, which this issue reduces to regression proof, since the gate covers every kind); any CLI flag or environment switch.

## 2. Module layout and public surface

`dirtywork/firewall_gate.py` imports from `dirtywork.firewall` and from `dirtywork.providers` (the `ToolCall` type) only. It never imports `dirtywork.toolspec`, `dirtywork.tools`, `dirtywork.guardrails` or `dirtywork.sandbox`, so the registry's tool list and the transcript name cap are passed in by the Runner. The package's own import isolation is unchanged: `dirtywork.firewall` still imports nothing outside itself, and the existing subprocess test keeps proving it.

```python
FIREWALL_DENIAL_EVENT = "firewall_denial"

def policy_context_for(sandbox_mode: str, worktree: Optional[Path]) -> PolicyContext
def decide_turn(tool_calls: Sequence[ToolCall], *, turn: int, context: PolicyContext) -> list[Outcome]
def denial_text(outcome: Outcome, tc: ToolCall, *, available_tools: str) -> str
def denial_strike(outcome: Outcome) -> tuple[str, Optional[str]]
def denial_event_fields(outcome: Outcome, tc: ToolCall, *, turn: int, tool: str) -> dict
def execution_args(action: CanonicalAction) -> dict
```

`denial_strike` returns one of three verbs, because "no strike" is two different behaviors: `("strike", kind)` where `kind` is a member of the Runner's `FAILURE_KINDS`, `("reset", None)` for an authority denial, which clears the failure counters exactly as a `BLOCKED:` result does today, and `("hold", None)` for `firewall_internal_error`, which leaves the counters untouched so a harness fault neither scores against the worker nor clears a real streak. It raises `FirewallInternalError` on a reason code it does not know.

`denial_event_fields` returns the record's fields only: no `ts` and no `event` key, since `Transcript.write` supplies both and would raise on a duplicate.

`Runner.__init__` gains `policy_context: PolicyContext | None = None` after `no_change_turns`. `Runner` exposes two integer attributes, `firewall_denials` and `firewall_internal_errors`, that start at 0 and are readable at any time, including after an exception escapes `run()`.

In `dirtywork/__main__.py`: `cmd_run` initializes `runner = None` beside `sandbox = None` (line 913); it computes `policy_context = policy_context_for(ctx.sandbox_mode, ctx.worktree)` immediately after the `_build_sandbox(...)` call (lines 917-919) and passes `policy_context=policy_context` to `Runner(...)` (line 941); `_build_sandbox`'s signature and return are unchanged. `_emit_result` seeds `"firewall_denials": 0` and `"firewall_internal_errors": 0` after `truncations` (line 593). `_contract_fields` (line 601), which is what actually reaches `run.json`, the failure `run_end` record and the stdout payload, adds `"firewall_denials": extra.get("firewall_denials", 0)` and `"firewall_internal_errors": extra.get("firewall_internal_errors", 0)` after `truncations` (line 614). `_fail_run` gains `runner=None` after `transcript_path`, is called as `_fail_run(e, ..., runner=runner)` (line 973), and when `runner is not None` overwrites those two keys in its contract dict from `runner.firewall_denials` and `runner.firewall_internal_errors`. `_fail_setup`, where no runner can exist, keeps the zero seed.

## 3. Context: mode and the worktree roots

`policy_context_for` is the only place the CLI's sandbox vocabulary meets the Firewall's.

- `"docker"` returns `PolicyContext("docker", ())`.
- `"none"` returns host mode with the worktree's two root forms: the given form, `posixpath.normpath(worktree.as_posix())`, and the resolved form, `posixpath.normpath(os.path.realpath(worktree))`. Equal forms collapse to one root. Two forms are ordered longest first, the given form first on a tie. `worktree` must not be `None`, and each form must start with `/`; otherwise `ValueError`.
- Any other mode string raises `ValueError`.

`PolicyContext.__post_init__` then validates the roots again (nonempty, absolute, normalized, not `/`), so a bad worktree fails at construction with `FirewallInternalError`, never at the first call.

When no context is passed, `Runner.__init__` builds `policy_context_for("none", getattr(sandbox, "worktree", None))`, which raises `ValueError` when the sandbox has no `worktree`. That default is a compatibility affordance for direct construction against a `HostSandbox` (the `parts` and `git_parts` fixtures), not a discovery step, and it never falls back to docker mode or to an empty root set. Runner tests that pass a double without a `worktree` attribute must supply a context explicitly: `tests/test_runner.py` gains a module-level `GATE_CTX = PolicyContext("docker", ())` and passes `policy_context=GATE_CTX` at the 22 `Runner(...)` sites on lines 813, 1705, 1783, 1794, 1825, 1840, 1863, 1888, 1909, 1937, 2022, 2214, 2249, 2270, 2293, 2499, 2533, 2550, 2575, 2634, 2946 and 2967, and `tests/test_transcript_schema.py` does the same at line 214: 23 sites in all. Three of them are shared constructors rather than one test each (the `box or sandbox` sites at 2249 and 2499, the latter being `_run_scenario`, which every `SCENARIOS` case goes through, and the local `Box` double at 2634), so a missed one fails many tests at once. Their calls are relative-path file tools, `grep`, `finish` and benign `bash`, all allowed in docker mode. The CLI never relies on the default. Windows stays unsupported: a drive-letter worktree gives a non-absolute POSIX form and the constructor raises, which the advisory Windows CI leg will show and the supported legs never will.

## 4. The turn gate: evaluate once, apply when reached

Immediately before `pending_finish = None` (`dirtywork/runner.py:1077`), after the malformed-entry handling and the assistant append, the Runner calls

```python
outcomes = decide_turn(tool_calls, turn=turns, context=self.policy_context)
```

where `turns` is the counter the turn's `nudge` events already carry, and the loop below becomes `for i, tc in enumerate(tool_calls):` reading `outcome = outcomes[i]`. The gate builds `ActionRequest.from_tool_call(tc, turn=turn, batch_index=i, batch_size=len(tool_calls))` for every call in order, calls `decide_batch` once, and returns its list. Outcomes are matched to calls by position, never by call id, because ids can repeat. The gate raises `FirewallInternalError` if the list length ever differs from the batch length; nothing in the gate or the Runner catches around it. `decide_batch` never raises by contract, so a raise here is a harness bug that ends the run through the CLI's existing unhandled-exception path, loudly, and never as a bypass.

Evaluation and application are distinct steps. Every addressable call is evaluated up front, which is sound because `evaluate` is pure and depends only on the immutable run context. An outcome is applied only when the loop reaches its call. The per-call order is:

1. **Truncation classification, unchanged.** A call with a decode error on a reply that hit the token cap, and a call whose required parameter is absent on such a reply, get the existing truncation result and nudge. Neither consults its outcome: no denial event, no counter increment, no strike, no reset. The call stays in the batch for evaluation, so a later call reusing its id is still `call_id_duplicate`.
2. **The outcome, read by position.** On `DENY` the Runner sets `result` to the text of §5, applies `denial_strike`'s verb (`("strike", kind)` → `abort_reason = failures.record(kind)`; `("reset", None)` → `failures.reset()`; `("hold", None)` → neither), writes the event of §7, increments the counters of §7, and runs neither the finish branch nor `registry.execute`. `ALLOW` proceeds to step 3.
3. **Execution, unchanged in shape.** A terminal kind sets the pending finish from the canonical summary; every other kind goes to `registry.execute` with the canonical name and arguments of §6. `BudgetExceeded` and `SandboxError` end the run exactly as today, leaving the outcomes after that call unapplied.

After the branch, the loop's existing tail (`runner.py:1132-1160`: `progress.note_call`, the `bash` repeat and timeout check, the `tool_result` event, `note_last_tool_result`, `tool_message`, the terminal and abort handling) runs for a denied call exactly as for an executed one, with the loop's `name` (the registry-recovered name) and `args` (`tc.arguments`) unchanged and `result` holding the denial text. A denied `finish` therefore keeps `name == FINISH_TOOL` and is not recorded as the last tool result.

The two non-truncation branches that exist today for an addressable call with a decode error (`malformed_args` strike, `ERROR: <decode error>`) are replaced by step 2: the Firewall rejects the same call at the request stage with `arguments_unparseable`, and §5 keeps both the strike kind and the decode error in the text. `check_request` runs its checks in a fixed order, so a decode-error call whose batch is oversize, whose id is invalid or duplicated, or whose name is unknown or malformed takes that earlier code and its strike instead, and the decode error is not echoed.

A `DENY` does not cancel later calls. The loop continues, one tool message per call in provider order, until the batch ends or an existing terminating condition (a strike threshold, a budget or sandbox error, the deadline) ends the run. A denied `finish` is not a completion: `pending_finish` stays unset and the run continues.

## 5. The denial contract toward the model

The result text is `PREFIX: detail`, where `detail` is `outcome.policy.detail` (bounded by `MAX_DETAIL_CHARS`, never quoting worker text). The prefix follows the reason class:

| Reason class | Prefix | Why |
|---|---|---|
| authority | `BLOCKED` | the legacy convention every worker model has seen; transcript readers keyed on `BLOCKED:` keep working |
| malformed, bounds | `ERROR` | the registry's own prefix for the same faults today |
| internal | `ERROR` | a harness fault reads as an error to the worker; the reason code, not the prefix, marks it internal |

Two request-stage codes append guidance: `arguments_unparseable` appends `: <tc.error>` when the provider recorded a decode error, and `tool_unknown` appends `. Available: <available_tools>. To end the run call finish(summary=...).`, the registry's own sentence, where `available_tools` is `", ".join(self.registry.names())`, computed once per turn before the loop. The registry's unknown-tool text also echoes the offending name; the gate drops that echo deliberately, since the worker's own call is in the history directly above the result and the name can be arbitrarily long and marker-polluted.

Strikes are mapped exhaustively by reason code onto the Runner's existing `FAILURE_KINDS`. Prefixes may depend on the reason class; strikes may not, because `call_id_duplicate` is a bounds-class code with a malformed-class strike.

| Reason code | `denial_strike` |
|---|---|
| `arguments_unparseable`, `arguments_not_object`, `call_id_invalid`, `call_id_duplicate` | `("strike", "malformed_args")` |
| `tool_unknown`, `tool_name_invalid` | `("strike", "unknown_tool")` |
| `argument_missing`, `argument_type_invalid`, `argument_unexpected` | `("strike", "bad_args")` |
| `payload_too_large`, `string_too_long`, `collection_too_large`, `nesting_too_deep`, `number_out_of_range`, `batch_too_large` | `("strike", "bad_args")` |
| `privilege_escalation`, `repo_publish`, `repo_control`, `host_fs_destructive`, `remote_code_exec`, `system_control`, `host_fs_redirect`, `host_fs_chdir`, `repo_metadata_target`, `path_outside_workspace` | `("reset", None)` |
| `firewall_internal_error` | `("hold", None)` |

A test pins this table to `ReasonCode` exhaustively, so a new code cannot fall through to "no strike" silently. An internal error is bounded by the existing stall, stuck and max-turn detection and surfaced by the counters of §7.

## 6. ALLOW handoff: canonical name and arguments

The executor receives the canonical action, not the raw call. The ALLOW branch calls

```python
self.registry.execute(action.kind.value, execution_args(action), sandbox=self.sandbox, deadline=deadline)
```

and leaves the loop's `name` and `args` locals untouched, so the tail's bookkeeping and the `tool_result` event still see the registry-recovered name and `tc.arguments`. `action.kind.value` is the registered name with any marker pollution already removed by `canonicalize`.

`execution_args(action)` is `dataclasses.asdict(action.args)` with the `apply_edits` `edits` tuple converted to a list of `{"old", "new"}` dicts, and optional fields (`grep.glob`) passed through as `None`. That is the shape `tests/test_firewall_parity.py` pins to `toolspec._validate_args`, so the registry's own validation, which stays in place until #143, is idempotent on it: unexpected keys are already gone, `timeout` is already an integer clamped to the field table maximum, paths are already lexically normalized.

A `finish` call sets the pending summary from `action.args.summary`, a string that the field table defaults to `""` when the worker omitted it.

Identity is untouched: the tool message uses the original `tc.id`; the transcript's `tool_result.args` still carries the raw argument string, and `tool_raw` still carries the raw name through the existing `recover_name` call.

## 7. Evidence

**The `firewall_denial` transcript event.** Written by the Runner, not the registry, for every `DENY` outcome at the moment its call is reached, immediately before that call's own `tool_result` event, so a reader sees the decision and then the result the worker received. Never written for `ALLOW`. Its fields are `outcome.event.to_dict()` (`schema_version`, `stage`, `turn`, `call_id`, `kind`, `capabilities`, `decision`, `reason_code`, `reason_class`, `action_identity`, `semantic_status`) plus `tool`, the capped name the `tool_result` event of the same call uses, and the Runner writes it as `self.transcript.write(FIREWALL_DENIAL_EVENT, **denial_event_fields(...))`.

`outcome.event` is `None` only when every event factory the outcome's own path reaches has failed, and which factories those are depends on the stage. A request-stage rejection, where `canonicalize` returned a `Rejection` and no canonical action exists, only ever calls `FirewallEvent.from_rejection`, so that one raising is enough. An action-stage failure, where a canonical action exists, calls `FirewallEvent.from_action` first and falls back to `from_rejection`, so both must raise (the #137 design's double and triple failure cases). The record is then written anyway, without retrying any factory and without inventing an action identity:

```
{"event": "firewall_denial", "schema_version": 1, "turn": <int>,
 "call_id": <tc.id, first 256 chars>, "tool": <capped name>,
 "decision": "deny", "reason_code": "firewall_internal_error",
 "reason_class": "internal", "event_missing": true}
```

**Counters.** `firewall_denials` counts every `DENY` outcome applied, including request-stage rejections and internal errors; `firewall_internal_errors` counts the subset whose reason code is `firewall_internal_error`, so it is never greater than `firewall_denials`. Both count applied outcomes only, so a batch cut short by an early exit never implies that later outcomes were applied. They ride on `RunResult.extra` into the `run_end` record and, through `_contract_fields`, into `run.json` and the stdout payload on every path the Runner returns. When an exception escapes `run()`, `_fail_run` overwrites them from the runner instance, so a denial recorded before the exception survives; when no runner exists, the zero seed stands.

**Unchanged.** `guardrail_block` is still written by the registry when a tool's own check blocks a call the Firewall allowed; that overlap is #140's parity subject and #143's de-duplication subject. Nothing new is written on `ALLOW`, per the parent design's rule against per-allow spam.

## 8. Invariants

- **Zero executor invocation on every non-ALLOW path.** A denied call, an internal-error call and a truncated call never reach `registry.execute`, `spec.fn`, a sandbox method or the finish branch.
- **One result per addressable call the loop reaches, original id, provider order.** The loop shape is unchanged; the gate adds a branch, not a reordering.
- **Enforcement cannot be disabled silently.** No flag, no environment switch. A context that cannot be built raises at construction (no `worktree` on the sandbox, a `None` or relative worktree, an unknown mode); no path defaults to docker mode or to an empty root set; a gate failure raises through the run.
- **Evaluation is pure and up front; application is positional and when reached.**
- **The registry's validation and the legacy in-tool guardrails stay in place** as a second line until parity (#140) and de-duplication (#143) retire them.

## 9. Decisions taken that Jim may want to override

1. **The Firewall owns request-stage faults the registry used to answer.** Unknown tools, undecodable and mistyped arguments and oversize payloads are now denied by the gate before the registry sees them, with the same strike kinds. The text is not identical: the gate's detail replaces the registry's sentence, so an unknown tool now reads `ERROR: tool_name is not a known action kind. Available: ...` instead of `ERROR: unknown tool 'x'. Available: ...`. The one existing test that asserts on that wording, `test_unknown_tool_counts_as_strike_but_recovers`, changes its assertion to the new phrase in the same task as the wiring. The registry's own paths for those faults remain reachable only through direct `registry.execute` callers and tests.
2. **Absolute paths in docker mode are denied.** Inherited from #137 (rule 2, `Docker absolute denied`), not chosen here; the dogfood worker sends relative paths, and the parity subject belongs to #140.
3. **A batch over 32 calls is rejected whole.** `batch_too_large` is a request-stage bound from #135: every call that is not already a `call_id_duplicate` is denied `batch_too_large` with a `bad_args` strike, so the third ends the run `model_error`. Today the Runner would execute all of them.
4. **An internal error takes no strike and does not reset the counters.** The alternative, a dedicated run status, would add a status to the transcript schema for a case that should never happen; the counters and the event make it visible, and the existing stall detection bounds it.
5. **A non-string `finish` summary is now denied.** `argument_type_invalid` instead of today's silent completion with an empty summary; an omitted or null summary still completes with `""`, per the field table's declared exception.
6. **The Windows advisory leg goes red for host-mode Runner tests.** Windows is unsupported and the leg is advisory; making `policy_context_for` accept drive letters would invent a path form the policy never compares against.
7. **Default context for direct Runner construction.** The alternative, a required argument, touches all 189 `Runner(...)` sites; the default plus the 23 explicit sites of §3 touches 23.

## 10. Tests

Gate unit tests, `tests/test_firewall_gate.py`:

- `policy_context_for`: docker gives empty roots; host gives the given and resolved forms, longest first, collapsed when equal; a symlinked worktree gives two roots and the given form is first on a tie; `None` worktree, a relative worktree and an unknown mode raise `ValueError`.
- `execution_args` for every `ActionKind` except `finish`, over the parity corpus: `toolspec._validate_args(spec, execution_args(action))` returns `execution_args(action)` unchanged. Idempotence, not equality with the raw call, is the property the registry's second pass needs, and it is the only one that holds everywhere: canonicalization normalizes a path lexically (`./a//b` becomes `a/b`) and clamps `timeout` to the field table's maximum (`601` becomes `600`) before the registry ever sees the call, so the canonical dict legitimately differs from `_validate_args` on the raw arguments. Also: `edits` is a list of dicts, `glob` is `None` when absent, and the result carries no unexpected key.
- `denial_text`: one case per reason class for the prefix; `arguments_unparseable` with and without `tc.error`; `tool_unknown` carries the available-tools sentence and no name echo.
- `denial_strike`: one case per `ReasonCode`, generated from the enum so a new code fails the test; the three verbs are asserted by name; an unknown code raises.
- `denial_event_fields`: the normal shape equals `event.to_dict()` plus `tool` and carries no `event` or `ts` key; the `event_missing` shape is exactly §7's, with a 300-character call id cut to 256.
- Isolation: the module's imports are limited to `dirtywork.firewall` and `dirtywork.providers`, checked by AST like the package's own test; the subprocess import test for `dirtywork.firewall` still passes.

Runner tests, added to `tests/test_runner.py` beside the mixed-turn neighbours (`test_mixed_turn_finish_first_then_timeout` and the `SCENARIOS` harness), each against a sandbox subclass that records every method call:

- mixed batch: `bash git push` denied (`BLOCKED:` text, no strike, counters reset), then `write_file` allowed and executed; tool messages in provider order with the original ids; the denied command absent from the sandbox's command log; `firewall_denials == 1` on `run_end`.
- `.git/config` write denied with no sandbox call other than the run-start fingerprint script; a `../escape.txt` write denied likewise.
- canonical handoff: a `bash` call with `timeout: "2m"` and an unexpected key reaches the sandbox as `timeout=120` with no extra key; a name of the form marker-then-`write_file` (the only shape `recover_name` recovers, and built by concatenation in the brief) executes as `write_file` with `tool_raw` on the event.
- duplicate ids: two calls with one id; the first executes, the second is denied `call_id_duplicate` with a `malformed_args` strike; both get tool messages.
- truncation precedence: a token-capped reply with a decode-error call produces the truncation result and nudge, no `firewall_denial` event, no counter, no strike; a later call in the same batch reusing that id is denied `call_id_duplicate`.
- finish: an allowed finish with a summary completes the run with the canonical summary; a finish with a non-string summary is denied `argument_type_invalid` and the run continues to the next turn.
- internal error: `dirtywork.firewall.policy.evaluate` patched to raise; the call gets `ERROR:` with `firewall_internal_error`, no strike and no reset (a preceding streak of two `argument_missing` denials is still two `bad_args` afterwards), `firewall_denials == 3` and `firewall_internal_errors == 1` on `run_end`, and the event carries `stage: "action"`.
- missing event, three cases. Request stage: a `read_file` call with no `path`, with `from_rejection` alone patched to raise; the `event_missing` record lands with the call's turn and id, the denial text ends with `; no event`, and `from_action` is never called. Action stage: an allowed call with both factories patched, same record. Fallback: `from_action` alone patched, asserting the tier-2 request-stage event with no `event_missing` key.
- early exit: a batch of three where the second raises `BudgetExceeded` and the third would be denied; `run_end` says `firewall_denials == 0` and the third call has no event and no tool message (existing semantics, now pinned).
- docker context: `Runner(..., policy_context=PolicyContext("docker", ()))` with a `write_file` to `/work/a.py` denied `path_outside_workspace` and a relative write allowed.
- `Runner(...)` with a sandbox lacking `worktree` and no explicit context raises `ValueError`; with a `HostSandbox` the default host context is built.
- reason-code sweep: one crafted turn per `ReasonCode`, parametrized from the enum, asserting prefix, strike verb and event `reason_code`. Three codes need a turn shape of their own inside the same parametrization: `batch_too_large` a 33-call batch, `call_id_duplicate` a two-call batch sharing an id, `firewall_internal_error` the patched `evaluate`. One mixed case covers the ordering caveat of §4: a decode-error call whose name is unknown is denied `tool_unknown`, not `arguments_unparseable`.

CLI test, `tests/test_main.py` beside `test_main_docker_llm_error_after_start_finalizes_before_stop` (line 745): turn 1 denies `bash git push`, turn 2 the provider raises `LLMError`; `run.json` and the stdout payload carry `firewall_denials == 1` and `firewall_internal_errors == 0`.

Schema pins: `tests/test_transcript_schema.py` gains `firewall_denial` in `EVENT_NAMES` and both counters in `RUN_END_FIELDS`. Because that suite cross-checks every name against `docs/transcript-schema.md`, the same brief adds a minimal `firewall_denial` subsection and the two field rows to that document, in the table shapes the file already uses; Claude expands the prose after the run.

## 11. Compatibility

Provider history shapes are unchanged: one assistant message with its tool calls, one tool message per addressable call the loop reaches, in order, with the original ids; the placeholder and follow-up carrier rules of issue #60 are untouched. Legacy `BLOCKED:` and `ERROR:` prefixes stay what workers see. `guardrail_block` keeps its writer and shape. `run.json`, the stdout payload and `run_end` gain two integer fields with zero defaults on every path; no existing field changes. `ToolRegistry.execute`'s signature and behavior are unchanged. `dirtywork.firewall` still imports nothing outside itself.
