# Worker Action Firewall D: deterministic policy engine and fail-closed behavior

Issue #137. Parent design: `2026-09-06-worker-action-firewall-design.md` (§8, §10, §11, §12, §13, §19). Predecessors: `2026-09-08-issue-135-firewall-schema-design.md` (vocabulary, `PolicyDecision`, `FirewallEvent`, the legacy and file-target tables) and `2026-09-19-issue-136-firewall-normalization-design.md` (`canonicalize`, `canonicalize_batch`, target classes). Inventory: `2026-09-06-worker-action-firewall-inventory.md` §3 and §4.

Written 2026-09-19 against `main` at `d8638e4`, where `dirtywork/firewall/` holds `errors`, `bounds`, `reasons`, `capabilities`, `schema`, `request`, `paths`, `normalize` and re-exports 40 names, and nothing outside the package imports it.

## 1. Scope

This issue adds the decision: from a `CanonicalAction` and a run context to `ALLOW` or `DENY`, deterministically, with a stable reason code and bounded detail, and a wrapper that can never raise and can never answer `ALLOW` by accident. It ships:

- a `PolicyContext` carrying the sandbox mode and the worktree root forms the caller has already computed;
- three ordered file-target rules over the #136 target classes, backend-aware for `.git` and `..`;
- the deterministic shell analyzer: the eight guardrail rules copied by value with their scope, pattern, legacy reason text, capability and reason code, a drift test that pins them to the live `guardrails.py`, and the worktree-reference rewrite without filesystem access;
- `evaluate(action, context) -> Verdict`, pure, first match wins, precedence documented and tested;
- `decide(request, context) -> Outcome` and `decide_batch`, the fail-closed entry points #138 will call;
- tests for allow, deny, malformed and internal-error paths, and one named test per invariant in the parent design §19 that this layer owns.

It changes **no** runtime behavior and touches **no** file outside `dirtywork/firewall/` and `tests/`. Nothing in `dirtywork/` imports the package after this issue; the first runtime import is #138's. Out of scope, with the owning issue: structural DENY tool results and Runner batch wiring (#138); routing static tools through `decide` (#139); shadow/parity against `guardrail_block`, the compatibility mapping of `guardrail_block.reason`, and removing the registry's copies (#140); writing events to the transcript or Supervisor, and per-ALLOW event volume (#141); any `semantic_known` classification of shell commands (a future analyzer, not this one); the `NETWORK` capability, still reserved and unset.

## 2. Package layout and public surface

Two new modules, each with one test file:

| File | Holds | Imports from `dirtywork/` |
| --- | --- | --- |
| `dirtywork/firewall/shell.py` | `ShellRule`, `SHELL_RULES`, `ROOT_BOUNDARY`, `rewrite_worktree_refs`, `ShellMatch`, `analyze_command` | `.capabilities`, `.errors`, `.reasons` |
| `dirtywork/firewall/policy.py` | `PolicyContext`, `Verdict`, `Outcome`, `WRITE_KINDS` (re-used from `.normalize`), `evaluate`, `decide`, `decide_batch` | `.bounds`, `.capabilities`, `.errors`, `.normalize`, `.paths`, `.reasons`, `.request`, `.schema`, `.shell` |

One existing file changes inside the package: `normalize.py` gains `duplicate_positions` (section 7), which `canonicalize_batch` and `decide_batch` share. The package `__init__.py` re-exports `PolicyContext`, `Verdict`, `Outcome`, `evaluate`, `decide`, `decide_batch`, `analyze_command` and `SHELL_RULES`; `__all__` grows from 40 to 48. The import-isolation rule holds: a test parses both modules' imports and fails on any `dirtywork.` import outside the package.

```python
@dataclass(frozen=True)
class PolicyContext:
    mode: str                          # "host" | "docker"
    worktree_roots: "tuple[str, ...]"  # host: the worktree's absolute path and its resolved form, longest first; docker: ()

@dataclass(frozen=True)
class Verdict:
    action: CanonicalAction            # the input, with the matched shell capability added on a DENY
    policy: PolicyDecision

@dataclass(frozen=True)
class Outcome:
    action: Optional[CanonicalAction]  # None on a request-stage rejection or an internal error
    policy: PolicyDecision
    event: Optional[FirewallEvent]     # None only when building the event itself failed (section 7)
    dropped_keys: int

def evaluate(action: CanonicalAction, context: PolicyContext) -> Verdict: ...
def decide(request: ActionRequest, context: PolicyContext) -> Outcome: ...
def decide_batch(requests: "Sequence[ActionRequest]", context: PolicyContext) -> "list[Outcome]": ...
def analyze_command(command: str, *, mode: str, worktree_roots: "tuple[str, ...]") -> Optional[ShellMatch]: ...
```

`evaluate` is pure: no I/O, no filesystem, no clock. It raises `FirewallInternalError` on any input outside its contract and on any internal inconsistency; it never catches anything. `decide` is the opposite by design: it never raises (section 7).

## 3. Context, mode and the worktree roots

`PolicyContext.__post_init__` raises `FirewallInternalError` unless `mode` is exactly `"host"` or `"docker"` and `worktree_roots` is a tuple of `str`, nonempty in host mode and empty in Docker mode, and every root is a nonempty absolute posix path equal to its own `posixpath.normpath` and not `/`. Review of the first reference found that an empty root would have made every absolute path "inside" and rewritten every shell command into harmlessness, so the root shape is part of the contract, not a caller courtesy. An unknown mode therefore fails closed instead of falling through to the permissive branch (parent design §8: unknown internal state is a Firewall failure, not a bypass). Host-only rules run only when `mode == "host"`.

The roots exist for two reasons and are supplied, not discovered: the shell analyzer rewrites absolute references to the worktree to `.` before its patterns run, exactly as `guardrails._rewrite_worktree_refs` does, and the file-target rules treat an absolute path under a root as inside the worktree in host mode, as `resolve_in_worktree` does. The legacy rewrite calls `Path.resolve()` to get the second form; this package never touches the filesystem, so the caller (#138) computes both forms once per run and passes them longest first. A test builds a symlinked temporary worktree, passes both forms, and asserts the rewrite matches the legacy function's output on a command corpus.

No reason code is added. An earlier draft of this spec denied option-like paths (a first component beginning with `-`), citing the inventory §4 gap where the Docker backend passed `list_dir(path="-delete")` to GNU `find` as an expression; #145 and #153 closed that gap before this issue by anchoring every Docker operand with `./` and terminating options with `--`, and the host backend never exposes a path as an argv option, so the rule would have restricted nothing real and would have denied any worktree whose own name begins with `-`. The vocabulary stays at 26 codes.

## 4. File-target rules

For every kind with a `path` field (all but `bash` and `finish`), `evaluate` applies these rules in order and the first match wins. Every rule reads only the canonical path, its `TargetClass` from #136, a lexical `posixpath.normpath` where stated, and the context; none touches the filesystem. One private helper carries the two checks that recur: given a relative path whose `..` have been resolved lexically, a first component of `..` is `path_outside_workspace`, and a first component of exactly `.git` on a write kind is `repo_metadata_target`.

| Order | Condition | Reason code | Class | Mode |
| --- | --- | --- | --- | --- |
| 1 | target is `repo_metadata` and the kind is in `WRITE_KINDS` | `repo_metadata_target` | `authority` | both |
| 2 | target is `outside`: absolute, or a leading `..`. In host mode an absolute path that equals a context root or begins with a root followed by `/` is inside; the remainder after the root goes through the helper, so `/wt/.git/config` written is `repo_metadata_target` and `/wt/../x` is outside. Every other `outside` path is denied. | `path_outside_workspace` or `repo_metadata_target` | `authority` | both, with the host exception |
| 3 | target is `parent_ref`: `posixpath.normpath(path)` goes through the helper, so `a/../../x` is outside and `src/../.git/config` written is `repo_metadata_target`, while `src/a/../x.py` and `src/../..cache/data` are allowed (`..cache` is a name, not a `..` component) | `path_outside_workspace` or `repo_metadata_target` | `authority` | both |

Everything else is `ALLOW`. Reading under `.git` is allowed for the read kinds, as the executors allow it today. Rule 2's host exception mirrors `resolve_in_worktree`, which accepts an absolute path that lands inside the worktree and still refuses a root `.git` write on it, and the Docker backend's `_rel`, which refuses every absolute path. Rule 3 mirrors `_rel` exactly in Docker mode (normalize, then refuse a remaining leading `..` or a root `.git` write). In host mode the same lexical classification is applied on purpose: the executor resolves on disk and would refuse the same targets in every case except one where a symlink sits before the `..`, and there the Firewall's lexical denial is the conservative side; the #136 rule that keeps `..` verbatim in the canonical path is untouched, because that rule protects the execution target, while this one only classifies. A component test, not a string-prefix test, decides `..`: review of the first reference found `startswith("..")` rejecting `..cache`.

Rules 1 to 3 key on different target classes, so at most one applies. A test pins each on a read kind and a write kind, in both modes.

Detail strings are fixed harness sentences per reason (`"write under the repository's .git"`, `"path resolves outside the worktree"`), within `MAX_DETAIL_CHARS`, never containing the path.

## 5. The shell analyzer

`SHELL_RULES` is a tuple of eight frozen `ShellRule(index, scope, pattern, legacy_reason, capability, reason_code)` in `guardrails._RULES` order, copied by value: `scope` and `legacy_reason` are the tuple's first two elements, `pattern` its third, exactly as the source spells it including the `_GIT_OPTS`, `_HOME_ESCAPE_TARGET` and `_HOME_KEYED_VARS` fragments it is built from (they are copied as the same Python expressions so the module reads like the original); `capability` and `reason_code` come from `LEGACY_RULES`. Patterns are compiled once at import with `re.IGNORECASE`, as the legacy module does. A drift test asserts, for every index, that `scope`, `legacy_reason` and `pattern` equal the live `guardrails._RULES[index]` and that `capability` and `reason_code` equal `LEGACY_RULES[index]`, so neither copy can move without the other.

`rewrite_worktree_refs(command, roots)` substitutes each root, longest first, followed by the legacy boundary `(?=[/\s'"]|$)`, with `.`, in the checked string only; the command that executes is never changed. It is the legacy function with the `Path.resolve()` call replaced by the caller-supplied second form.

`analyze_command(command, mode=..., worktree_roots=...)` takes the context's two fields as plain arguments, because `shell.py` sits below `policy.py` in the import order and must not import `PolicyContext`; `evaluate` passes them through. It runs the rewrite in host mode, skips host-scoped rules in Docker mode, and returns the first matching rule as `ShellMatch(index, capability, reason_code, legacy_reason)` or `None`. Only the first match is reported, mirroring `check_bash_command`; a second matching rule contributes nothing to the decision or the evidence (section 10, decision 2). A test pins multi-match precedence: `sudo git push` is rule 1; a command that both destroys outside the worktree and pipes a download into an interpreter is rule 4 in host mode and rule 5 in Docker mode, where rule 4 is skipped.

The analyzer never classifies a command as `semantic_known`: a denylist match proves a denial, not an understanding of a permitted command, so every `bash` action keeps the `semantic_unknown` #136 gave it, and the #135 test that `PolicyDecision` cannot deny on `semantic_unknown` continues to hold. The shell guardrails remain what `guardrails.py` says they are, best-effort accident guards and not the OS boundary; this issue moves the knowledge, not the claim.

## 6. `evaluate`

In order:

1. `action` must be a `CanonicalAction` and `context` a `PolicyContext`, else `FirewallInternalError`. A raw worker dictionary therefore cannot reach a rule (parent design §19).
2. `finish`: `ALLOW`.
3. A kind with a `path` field: section 4. The returned action is the input unchanged.
4. `bash`: `analyze_command(args.command, mode=context.mode, worktree_roots=context.worktree_roots)`. A match is `DENY` with the rule's `reason_code` and its `legacy_reason` as `detail`, and the returned action is the input with the rule's capability added to its set (`dataclasses.replace(action, capabilities=action.capabilities | {capability})`), so a `sudo` denial's evidence says `{SHELL, PRIVILEGE}`. No match is `ALLOW` with the action unchanged.
5. Any other kind is unreachable (`ActionKind` is closed and `CanonicalAction` checks membership), and the fall-through raises `FirewallInternalError` rather than allowing.

The result is `Verdict(action, PolicyDecision)`. `ALLOW` decisions have `reason_code None` and `detail ""`; `DENY` decisions have a code and a detail within `MAX_DETAIL_CHARS` that never quotes worker text. Precedence, in one sentence: request-stage rejections from `check_request` and `canonicalize` come before anything here (they never reach `evaluate`); within a path kind the four rules in order; within `bash` the eight rules in order with host-only rules skipped in Docker mode; nothing overlaps across kinds.

## 7. `decide` and `decide_batch`: fail closed

`decide(request, context)` is the one function #138 calls per addressable call. It:

1. calls `canonicalize(request)`; a `Rejection` becomes `PolicyDecision(DENY, rejection.reason_code, rejection.detail)` with `action None`, `event = FirewallEvent.from_rejection(request, rejection)`, and the normalization's `dropped_keys` (always `0` on a rejection);
2. otherwise calls `evaluate(action, context)` and builds `event = FirewallEvent.from_action(verdict.action, verdict.policy)`, returning the verdict's action, decision and event with the normalization's `dropped_keys`;
3. wraps steps 1 and 2 in one guard: any `Exception` (a `FirewallInternalError` from a constructor or an unknown state, or anything else, since a bug is exactly what this guard is for) becomes `PolicyDecision(DENY, FIREWALL_INTERNAL_ERROR, "firewall internal error: <exception class name>")` with `action None` and `dropped_keys 0`. The event is built from a synthetic `Rejection(FIREWALL_INTERNAL_ERROR, <same detail>)` through `from_rejection(request, ...)` inside its own guard; if that raises as well, `event` is `None` and the detail gains the suffix `"; no event"`. The guard catches `Exception`, not `BaseException`, so `KeyboardInterrupt` and `SystemExit` still stop the run.

`decide` never raises, never returns `ALLOW` from step 3, and never lets a worker input become `ALLOW` through exception handling (parent design §11, §19). The detail on an internal error names the exception class and nothing else: no message text, since an exception message can carry worker bytes.

`decide_batch(requests, context)` first computes `duplicate_positions(requests)`, a new `normalize.py` helper that returns the batch indexes whose `str` `call_id` repeats an earlier one (never raises; `canonicalize_batch` is refactored onto it so the two agree by construction), under a guard that turns any failure there into an internal-error outcome for every request. Then each request is handled under its own guard: a duplicate position becomes a `DENY` with `call_id_duplicate` and a request-stage event; every other request goes through `canonicalize` and the same steps as `decide`. Review of the first reference found the batch normalized as one guarded operation, so one request's normalization failure denied its neighbours; normalizing per request under the per-request guard is what makes the isolation promise true. Every request gets an `Outcome`, in order.

## 8. Evidence

Nothing new is defined. `FirewallEvent` carries the enriched capabilities, the decision, the reason code and class, the identity and the semantic status; `to_dict()` is what #141 writes. Because the shell capability is added before the event is built, the Supervisor's exact-equivalent key `reason_code + capability_set + action_identity` distinguishes a `sudo` denial from a `git push` denial of the same command text by both code and capability. When and how often events are recorded remains #141's decision.

## 9. Errors, bounds and performance

- No new bound; `MAX_DETAIL_CHARS` bounds every detail, and a test asserts every fixed sentence and every legacy reason fits.
- `evaluate` raises; `decide` catches. The two are never confused: `decide` is the only caller of `evaluate` inside the package, and tests call both.
- The analyzer is eight compiled regexes over a command already bounded by `MAX_COMMAND_CHARS`; the file rules are string operations on a path bounded by `MAX_PATH_CHARS`. Nothing here is claimed against the parent design's latency target; #142 measures.

## 10. Decisions taken that Jim may want to override

1. **No shell command becomes `semantic_known`.** The regex analyzer can prove a denial, not an understanding; a future analyzer may say more.
2. **Only the first matching shell rule contributes a capability.** Mirrors the legacy first-match contract; scanning for every match would cost little but would change the Supervisor's key for multi-match commands relative to what the legacy reason implies.
3. **`parent_ref` paths are classified lexically in both modes.** `normpath`, then the same `..` and `.git` checks as any other path. The first draft left host mode to on-disk resolution; review showed that let a `src/../.git/config` write through as `ALLOW` where the executor refuses it, so the evidence would have lied. The symlink case where lexical and on-disk disagree resolves to the conservative side.
4. **The caller supplies the worktree root forms.** The Firewall never calls `resolve()`; #138 computes the given and resolved forms once per run.
5. **Worktree roots are validated, not trusted.** Nonempty, absolute, normalized, not `/`. The first draft accepted an empty root and it disabled every host check.
6. **Shell denial details reuse the legacy reason text verbatim.** #140's compatibility mapping from `guardrail_block.reason` becomes a lookup on `SHELL_RULES`.
7. **`decide` catches `Exception`, not `BaseException`.** Interrupts must still interrupt.
8. **`Outcome.event` may be `None` on a double failure.** Denying is the invariant; the event is evidence, and a harness bug in the event code must not turn into a raise from the one function that promises never to raise.
9. **No option-like path rule.** Its hazard was closed by #145 and #153 (section 3); a rule without a hazard is a false positive waiting for a worktree named `-wt`.
10. **The `NETWORK` capability stays unset.** The download-into-interpreter rule maps to `REMOTE_CODE_EXEC` per #135; no rule here recognizes network use as such.

## 11. Tests

Two new files under `tests/`, which may import `dirtywork.guardrails`; the package may not.

`tests/test_firewall_shell.py`:

- the drift test of section 5, one assertion per field per index, against the live `guardrails._RULES` and `LEGACY_RULES`;
- parity on a command corpus of at least thirty commands (every legacy reason at least twice, including the git global-option forms, the `/dev/null` redirect exception, the toolchain-root variables, `$HOME` not matched, and ten benign commands): in host mode with a real temporary worktree's two root forms, `analyze_command` matches exactly when `check_bash_command(command, worktree)` returns a reason, with the same legacy text; in Docker mode, exactly when `check_bash_command(command, sandboxed=True)` does;
- the rewrite: a symlinked temporary path, both forms passed, output equal to `guardrails._rewrite_worktree_refs` on the corpus; `cd <abs worktree>/sub` allowed in host mode, `cd /elsewhere` denied;
- multi-match precedence per section 5;
- `analyze_command` returns `None` on an empty command and on the ten benign commands, and never raises on any `str`;
- vocabulary pins: eight rules, indexes 0 to 7, scopes in `{"always", "host"}`, the four Docker-scanned indexes `{0, 1, 4, 5}`.

`tests/test_firewall_policy.py`:

- `PolicyContext` invariants: bad mode, non-tuple roots, host with no roots, Docker with roots, and each bad root shape (empty, relative, `/`, trailing slash, non-normalized), each `FirewallInternalError`;
- every file-target rule on one read kind and one write kind in both modes, including host absolute-inside allowed (root itself and a path under it), host absolute-outside denied, Docker absolute denied, leading `..` denied in both, `parent_ref` classified by `normpath` in both modes (`a/../../x` outside, `src/a/../x.py` and `src/../..cache/data` allowed), the `.git` aliases (`/wt/.git/config` written under root `/wt` in host mode, `src/../.git/config` written in both modes) denied as `repo_metadata_target`, `.git` read allowed and write denied, `.gitignore` write allowed, and every path kind allowing a benign relative path;
- `finish` allowed; `bash` allowed with the action unchanged and denied with the capability added, for every legacy rule once;
- `evaluate` raises on a `dict`, a `Normalization`, a `Rejection` and a context of the wrong type;
- `decide`: accept path (action, ALLOW, event stage `action`), rejection path (event stage `request`, `action None`), internal error path with `canonicalize` monkeypatched to raise `RuntimeError` and with `evaluate` monkeypatched to raise `FirewallInternalError` (both `DENY`, `firewall_internal_error`, detail naming the class only, event stage `request`), and the double failure with `FirewallEvent.from_rejection` monkeypatched to raise (`event None`, detail suffix `"; no event"`); never `ALLOW` from any of these;
- `decide_batch`: mixed batch of allow, deny, rejection and duplicate, in order; a normalization failure injected on the middle request leaves both neighbours unaffected; duplicates still detected; a failure in `duplicate_positions` denies every request;
- the parent design §19 invariants this layer owns, each as a named test: raw dicts cannot reach rules; malformed input cannot become `ALLOW` through exception handling; precedence is deterministic (the same batch evaluated twice gives equal outcomes); reason code, capability set and identity on every event are members of their closed vocabularies; `semantic_unknown` never denies (every `bash` `DENY` here has a reason code from `SHELL_RULES`, and an allowed `bash` action keeps `semantic_unknown`); internal failure fails closed;
- import isolation of `shell.py` and `policy.py`.

The one #136 pin that grows is the `__all__` list (48 names), edited in the last brief. The reasons vocabulary and its pins are untouched.

## 12. Compatibility

No runtime path changes: the Runner, registry, executors and `guardrails.py` do not import the package, and `check_bash_command` keeps running exactly as before. This issue adds a second copy of the eight rules inside the Firewall and pins the two together, and adds `duplicate_positions` to `normalize.py` with `canonicalize_batch` refactored onto it and its tests unchanged, so #140 can shadow-compare, prove parity on the known-denied fixtures, and only then retire the legacy path. Run artifacts, the transcript and `guardrail_block` events are untouched.
