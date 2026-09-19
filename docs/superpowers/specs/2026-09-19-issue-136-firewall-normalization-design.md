# Worker Action Firewall C: tool normalization and canonical action identity

Issue #136. Parent design: `2026-09-06-worker-action-firewall-design.md` (§6, §7, §9, §12, §13). Predecessor: `2026-09-08-issue-135-firewall-schema-design.md`, whose vocabulary, bounds, argument classes, validator and identity this issue builds on without extending. Inventory: `2026-09-06-worker-action-firewall-inventory.md`.

Written 2026-09-19 against `main` at `ec16d8b`, where `dirtywork/firewall/` holds `errors.py`, `bounds.py`, `reasons.py`, `capabilities.py`, `schema.py`, `request.py` and the package re-exports, and nothing outside the package imports it.

## 1. Scope

This issue adds the per-tool step the Firewall was missing: from a validated `ActionRequest` to a `CanonicalAction`, or to a `Rejection` carrying one of the codes #135 already defined for it (`argument_missing`, `argument_type_invalid`, `argument_unexpected`, `string_too_long`, `number_out_of_range`, `call_id_duplicate`). It ships:

- deterministic, filesystem-free path normalization with a target class per path;
- a declarative per-kind field table and one generic pass that fills defaults, coerces what the tool registry coerces today, drops and counts unknown top-level keys, rejects with the precise code, and builds the closed argument classes;
- name recovery for marker-polluted tool names, over the closed `ActionKind` set;
- capability assembly from the kind's base set and the path's target class;
- a batch pass that detects duplicate call ids;
- tests, including parity tests that pin the pass to the registry's behavior on a shared fixture corpus.

It changes **no** runtime behavior and touches **no** file outside `dirtywork/firewall/` and `tests/`. Nothing in `dirtywork/` imports the package after this issue either; the first runtime import is #138's. Out of scope, with the owning issue: the bash analyzer and any `semantic_known` for shell (#137); policy rules, mode scoping and the `Rejection` to `PolicyDecision` mapping (#137); structural DENY tool results and Runner batch wiring (#138); routing static tools (#139); legacy guardrail parity and any tightening of the leniencies recorded in section 14 (#140); events and Supervisor evidence (#141); removing the registry's copy of name recovery (#143).

## 2. Package layout and public surface

Two new modules, each with one test file:

| File | Holds | Imports from `dirtywork/` |
| --- | --- | --- |
| `dirtywork/firewall/paths.py` | `TargetClass`, `NormalizedPath`, `normalize_path` | `.bounds` only |
| `dirtywork/firewall/normalize.py` | `TOOL_CALL_MARKERS`, `recover_name`, `FIELD_TABLE`, `Normalization`, `canonicalize`, `canonicalize_batch` | `.bounds`, `.capabilities`, `.errors`, `.paths`, `.reasons`, `.request`, `.schema` |

The package `__init__.py` re-exports `TargetClass`, `NormalizedPath`, `normalize_path`, `recover_name`, `Normalization`, `canonicalize` and `canonicalize_batch`, and `__all__` grows by those seven names. The import-isolation rule of #135 §2 holds: a test parses both modules' import statements and fails on any `dirtywork.` import outside the package.

```python
@dataclass(frozen=True)
class Normalization:
    action: Optional[CanonicalAction]   # set iff rejection is None
    rejection: Optional[Rejection]      # set iff action is None
    dropped_keys: int                   # unknown top-level keys dropped (section 5, step 3)

def canonicalize(request: ActionRequest) -> Normalization: ...
def canonicalize_batch(requests: Sequence[ActionRequest]) -> list[Normalization]: ...
def recover_name(name: str) -> tuple[str, Optional[str], int]: ...
```

`Normalization.__post_init__` raises `FirewallInternalError` unless exactly one of `action` and `rejection` is set and `dropped_keys` is a non-negative `int`. `dropped_keys` is `0` on a rejection.

`canonicalize` is the one entry point. It runs name recovery and `check_request` itself, so no caller can reach the per-tool step with an unchecked request. It never catches `FirewallInternalError`: a constructor raise inside the pass means a harness bug, and #137 is the layer that turns it into `DENY` with `firewall_internal_error`.

## 3. Name recovery

`recover_name` is the registry's `ToolRegistry.recover_name` algorithm with the registry table replaced by the eleven `ActionKind` values, and `TOOL_CALL_MARKERS` copied by value: the five raw markers (`[TOOL_CALLS]`, `<tool_call>`, `<function=`, `<function_call>`, `<|tool_call|>`) plus their sanitised forms with every character outside `[A-Za-z0-9_-]` replaced by `_`. Behavior, unchanged from the registry:

- a name that is already an `ActionKind` value is returned as-is with marker `None` and cut `0`;
- otherwise every occurrence of every marker is a candidate, tried latest end first, and the first whose stripped suffix is an `ActionKind` value wins, returning `(suffix, marker, position)`;
- otherwise the name is returned unchanged with marker `None`; `check_request` then reports `tool_unknown` or `tool_name_invalid`.

Recovery runs on any length of name before the length check, because it is linear in the name and the name is already in memory (section 14, decision 3). A test asserts the marker tuple equals `dirtywork.toolspec.TOOL_CALL_MARKERS` element for element, so the two copies cannot drift until #143 removes one.

`canonicalize` applies recovery to `request.tool_name` and proceeds with a copy of the request carrying the recovered name. The marker and cut are not part of the canonical action or its identity; they are available to #141 if evidence wants them, through the return value of `recover_name`, not through `Normalization`.

## 4. The field table

`FIELD_TABLE: dict[ActionKind, tuple[Field, ...]]`, one frozen `Field` per parameter, in the order the registry advertises them. A `Field` names the parameter, its value kind, whether it is required, its default, and its bound:

| Kind | Fields (name: value kind, required or default, bound) |
| --- | --- |
| `read_file` | `path: path, required, MAX_PATH_CHARS`; `offset: int, default 0, 0..MAX_INT`; `limit: int, default 400, 1..MAX_INT` |
| `write_file` | `path`; `content: str, required, MAX_STRING_CHARS` |
| `append_file` | `path`; `text: str, required, MAX_STRING_CHARS` |
| `edit_file` | `path`; `old_string: str, required, MAX_STRING_CHARS`; `new_string: str, required, MAX_STRING_CHARS` |
| `apply_edits` | `path`; `edits: edits, required, 1..MAX_COLLECTION_ITEMS items` |
| `insert_before`, `insert_after` | `path`; `anchor: str, required, MAX_STRING_CHARS`; `text: str, required, MAX_STRING_CHARS` |
| `list_dir` | `path: path, default "."` |
| `grep` | `pattern: str, required, MAX_PATTERN_CHARS`; `path: path, default "."`; `glob: str, default None, nullable, MAX_GLOB_CHARS` |
| `bash` | `command: command, required, MAX_COMMAND_CHARS`; `timeout: duration, default 120, clamped 1..MAX_BASH_TIMEOUT` |
| `finish` | `summary: str, default "", MAX_SUMMARY_CHARS` |

Value kinds and what they accept, in the order the registry accepts them today:

| Value kind | Accepts | Produces |
| --- | --- | --- |
| `str` | a `str` | the string |
| `int` | an `int` that is not a `bool`; or a `str` that `int()` parses | an `int` |
| `duration` | an `int` that is not a `bool`; a `str` that `int()` parses; a `str` matching the registry's duration pattern (1 to 9 digits, optional whitespace, a seconds or minutes unit, case-insensitive) | seconds as `int`, then clamped |
| `path` | a `str` | the normalized path and its target class (section 6) |
| `command` | a `str` | the string with leading and trailing whitespace stripped |
| `edits` | a `list` of 1 to `MAX_COLLECTION_ITEMS` objects, each with exactly the keys `old` and `new`, both `str`, `old` nonempty | a `tuple[Edit, ...]` |

Anything a value kind does not accept is `argument_type_invalid`. `finish` is the one kind whose only parameter is optional here although the registry requires it: the Runner already canonicalizes a missing summary to `""`, and #135 §4.2 pins that.

The registry's `timeout` on `grep` is harness data injected after validation and is not a field; a worker-supplied `timeout` on `grep` is an unknown top-level key and is dropped. A test cross-checks `FIELD_TABLE` against the live `builtin_tools` specs: same parameter names in the same order, same required set, same defaults, for every kind. That test, not the package, imports the registry.

## 5. The pass

`canonicalize(request)` runs these steps in order and stops at the first failure. The order is part of the contract and is tested.

1. **Name recovery**, then **`check_request`** (#135 §6.2). A `Rejection` from it is returned as-is with `dropped_keys = 0`.
2. **Kind.** `kind = ActionKind(request.tool_name)`; `check_request` step 4 guarantees membership.
3. **Required fields.** For each required field in table order, the key must be present in `arguments`, else `argument_missing` with `detail` naming the field. Presence, not value: a present `null` is step 5's business. Required fields are checked before unknown keys so a misspelled required key reports the missing name rather than an unexpected one.
4. **Unknown top-level keys** are dropped and counted; the count becomes `dropped_keys`. They are never rejected at the top level (#135 §12, decision 2).
5. **Per field, in table order:**
   - absent optional field: the default;
   - present `null`: the default if the field is optional (nullable or not), `argument_type_invalid` if it is required;
   - otherwise the value kind's acceptance rule, else `argument_type_invalid` naming the field.
6. **Bounds**, per field, immediately after its coercion, so the first bad field in table order is the one reported:
   - a string over its bound is `string_too_long` naming the field and the limit;
   - `offset < 0` or `limit < 1` is `number_out_of_range`;
   - `timeout` is clamped into `1..MAX_BASH_TIMEOUT` and never rejected (section 14, decision 2);
   - `edits`: an empty list is `argument_type_invalid` (a list longer than `MAX_COLLECTION_ITEMS` never reaches this step; `check_request` rejects it as `collection_too_large`); an item that is not an object, or lacks `old` or `new`, or whose `old` or `new` is not a `str`, or whose `old` is empty, is `argument_type_invalid` with `detail` giving the index and field (`edits[3].old`); an item with any other key is `argument_unexpected` with the index. Item strings are bounded by `MAX_STRING_CHARS` (`string_too_long`).
7. **Paths.** Every `path` field goes through `normalize_path`; the canonical string replaces the raw one and the target class is kept for step 9.
8. **Args class.** The kind's class is constructed from the coerced fields. Its `__post_init__` is the last line (#135 §4.2); after steps 5 and 6 it cannot raise on worker input, and if it does, the raise propagates.
9. **Capabilities and status** per section 8, then `CanonicalAction(schema_version=FIREWALL_SCHEMA_VERSION, call_id, turn, kind, args, capabilities, semantic_status)`.

`Rejection.detail` is composed by the harness, is at most `MAX_DETAIL_CHARS`, and names a field, an index or a limit. It never quotes worker text, including key names: an unexpected nested key is reported by position, not by name.

## 6. Paths

`normalize_path(raw: str) -> NormalizedPath(path: str, target: TargetClass)` is pure string work and never touches the filesystem.

Normalization: the input is split on `/`; empty components and `.` are dropped; `..` pops the previous component when there is one to pop, and is kept when there is none; the result is joined with `/`; an empty result is `.`. A trailing slash therefore disappears, and `src//x.py`, `src/./x.py` and `src/a/../x.py` all become `src/x.py`, while `./../x` becomes `../x`. An input that begins with `/` is absolute: the same collapse is applied, a `..` with nothing to pop is dropped rather than kept (there is nothing above the root, and the executors' resolution does the same), and the leading `/` is kept, so `/work/../etc/passwd` becomes `/etc/passwd` and `/../etc` becomes `/etc`. This is `posixpath.normpath` for every input except that an empty input becomes `.` explicitly and a leading `//` is not preserved. Bytes are never changed: no case folding, no Unicode normalization, no `~` expansion, no backslash conversion, because the file executors do none of those (section 14, decisions 4 and 5).

`TargetClass` is a three-member `str` enum:

| Target | When | Capability it adds |
| --- | --- | --- |
| `outside` | the input is absolute, or a `..` pops with nothing to pop | `HOST_FS`, every kind |
| `repo_metadata` | the first component of the normalized relative path is exactly `.git` | `REPO_CONTROL`, write kinds only |
| `workspace` | otherwise | none |

For an escaping relative path the popped-past-root components are kept in the canonical string (`../../etc/passwd` stays `../../etc/passwd`), so identity distinguishes escape targets and #141's evidence can say which one without the raw input. `.git` matches the component exactly: `.gitignore` and `src/.git/x` are `workspace`. The write kinds are `write_file`, `append_file`, `edit_file`, `apply_edits`, `insert_before` and `insert_after`; the other kinds reading under `.git` stay `workspace`-capable because the executors allow those reads today.

The Firewall does not know where the worktree is, so an absolute path is `outside` even when it names a file inside the worktree. #137 owns the mode-scoped decision that the host backend accepts such a path today and the Docker backend does not (#135 §5.3); the Firewall's job is to make the target class deterministic and the path canonical.

## 7. Per-kind notes

- **`bash`.** `command` keeps every interior byte; only leading and trailing whitespace is stripped, so `ls` and `ls\n` share an identity while `echo "a  b"` and `echo "a b"` do not. `timeout` follows the duration rule and the clamp. Capabilities are the base `SHELL` set only. `semantic_status` is `semantic_unknown` for every bash action produced here; #137's analyzer is the only code that may set `semantic_known`, and the #135 test that `PolicyDecision` cannot deny on `semantic_unknown` continues to hold.
- **`finish`.** `summary` defaults to `""`.
- **`grep`.** `pattern` and `glob` are bounded and kept verbatim; they are not paths and are not normalized. `glob` may be `null`.
- **`list_dir`.** `path` defaults to `.`.
- **`apply_edits`.** Section 5, step 6.
- **`read_file`.** `offset` and `limit` are coerced and bounded but are excluded from identity (#135 §9.1).

## 8. Capabilities and semantic status

Capability assembly is one expression per action: `BASE_CAPABILITIES[kind]`, plus `HOST_FS` when any path field's target is `outside`, plus `REPO_CONTROL` when a path field's target is `repo_metadata` and the kind is a write kind. Every kind has at most one path field today, so "any" and "a" are the same thing; the rule is written for the set so a future kind with two paths needs no rewrite. `NETWORK`, `PRIVILEGE`, `REPO_PUBLISH`, `SYSTEM_CONTROL` and `REMOTE_CODE_EXEC` are never set in this issue; they are #137's.

`semantic_status` is `semantic_known` for the nine static kinds and `finish`, and `semantic_unknown` for `bash`.

## 9. Batch

`canonicalize_batch(requests)` walks the batch in order. The first request carrying a given `call_id` proceeds through `canonicalize`; every later request with an equal `call_id` (exact string equality on the id as given, before any validation) receives `Rejection(call_id_duplicate)` with `detail` giving its batch index, and is not canonicalized. All other requests are independent: a rejection never affects its neighbours (parent design §12). `batch_size` bounds stay in `check_request`, and consistency between each request's `batch_size` and `len(requests)` is the adapter's responsibility in #138, not checked here.

## 10. Idempotence and identity

Normalization is idempotent at the contract level (parent design §7): building an `ActionRequest` from a `CanonicalAction`'s own canonical fields and canonicalizing it again yields an equal `CanonicalAction` and an equal `action_identity`. Tests pin the equivalences the design wants and the distinctions it forbids collapsing:

| Same identity | Different identity |
| --- | --- |
| `src/x.py`, `./src/x.py`, `src/./x.py`, `src/a/../x.py`, `src/x.py/` | `src/x.py` vs `src/y.py` |
| `read_file(path, offset=0)` vs `read_file(path, offset=10)` | `list_dir(".")` vs `list_dir("src")` |
| `bash("ls")` vs `bash("ls\n")` vs `bash("  ls  ")` | `bash("ls")` vs `bash("ls -l")`; `bash('echo "a  b"')` vs `bash('echo "a b"')` |
| `grep(pattern, path=".")` vs `grep(pattern)` (default) | `grep(p, glob=None)` vs `grep(p, glob="*.py")` |
| `../x` vs `./../x` | `../x` vs `x` |

`action_identity` and `rejection_identity` themselves are unchanged from #135; this issue only makes their inputs canonical.

## 11. Errors, bounds and performance

- Every bound the pass applies is a `bounds.py` constant already pinned in #135; this issue adds no constant.
- `FirewallInternalError` propagates out of `canonicalize` and `canonicalize_batch`; neither catches anything.
- The pass is a single walk over at most a handful of fields plus one linear path normalization; the only regular expression is the registry's duration pattern, applied to a string the registry would apply it to today. No allocation is proportional to anything but the input, and no step re-reads `arguments` after step 5. The static-tool target of p95 under 1 ms on reference hardware (parent design §17) is met by construction; #142 measures it.

## 12. Tests

Three files, all under `tests/`. Tests may import `dirtywork.builtin_tools` and `dirtywork.toolspec`; the package may not.

`tests/test_firewall_paths.py`:

- one table of `(input, expected path, expected target)` covering every rule in section 6: empty, `.`, trailing slash, repeated slashes, `.` and `..` in every position, escapes of every depth, absolute inputs with and without `..`, `.git` as first component versus elsewhere versus `.gitignore`, `~`, backslashes, non-ASCII bytes, and a path of exactly `MAX_PATH_CHARS`;
- the identity equivalence and distinctness sets of section 10 that concern paths, checked through `action_identity` on real actions;
- `normalize_path` is total on `str`: no input raises.

`tests/test_firewall_normalize.py`:

- `recover_name` against the registry's fixture shapes, and the marker-tuple equality test of section 3;
- per kind: a minimal accepted call with defaults filled, and the resulting `CanonicalAction`'s `kind`, `args`, `capabilities` and `semantic_status`;
- every rejection code the pass can emit, once per code, with `detail` naming the field or index and never containing the worker's value;
- the step order: an unknown key beside a missing required key reports `argument_missing`; a `null` required field reports `argument_type_invalid`, not `argument_missing`; a `string_too_long` on the first field is reported before an `argument_type_invalid` on the second; `check_request`'s own rejections come first;
- `dropped_keys` counts, including a worker-supplied `timeout` on `grep` and `null`-valued extras;
- `apply_edits` nested rules, each once, with indexes in `detail`;
- the coercion table of section 4 in both directions: every accepted form and the first rejected neighbour (`"1.5"` for an `int`, `"60ms"` and `"-5s"` for a `duration`, `True` for an `int`);
- the `timeout` clamp at both ends;
- capability assembly for `outside` and `repo_metadata` targets on a read kind and a write kind;
- `semantic_status` per kind;
- idempotence per section 10, for every kind;
- `Normalization` invariants;
- `FIELD_TABLE` versus the live registry: names in order, required set, defaults, for every kind;
- import isolation of `paths.py` and `normalize.py`.

`tests/test_firewall_parity.py`:

- a fixture corpus of raw argument dicts per kind, run through both `toolspec._validate_args(spec, args)` and `canonicalize`, asserting the same accept-or-reject outcome and, on accept, equal coerced values for every field the registry produces (paths compared after the registry's value is passed through `normalize_path`, since the registry does not normalize);
- the corpus covers every value kind's accepted forms, unknown keys, missing required keys, and numeric-string coercion;
- the two leniencies of section 14 (decisions 1 and 2) are excluded from the corpus and pinned by their own tests, which also assert that the registry rejects those inputs today, so the difference is on the record.

Expected counts per brief are recorded in the plan from the dry run.

## 13. Compatibility

No runtime path changes: the Runner, registry and executors do not import the package. The registry's `recover_name` and `_validate_args` keep working exactly as before; this issue adds a second implementation of each rule inside the Firewall and pins the two together with tests, so #138 can switch the Runner to the Firewall without a worker-visible change, and #143 can delete the registry's copies. Run artifacts, the transcript and `guardrail_block` events are untouched.

## 14. Decisions taken that Jim may want to override

1. **`null` on an optional parameter means its default.** The registry rejects `offset: null` today with a `bad_args` strike. A model that spells out "no offset" is asking for the default, and the #135 validator already lets `null` through. Recorded as a deliberate leniency; #140 can tighten it.
2. **`timeout` below 1 is clamped to 1, not rejected.** The registry passes `0` through and the command times out at once; nothing intends that. `#135 §4.2` already requires `1..MAX_BASH_TIMEOUT` after coercion.
3. **Name recovery runs before the length check and on any length.** Linear, in-memory, and the registry does the same.
4. **Absolute paths are `outside` regardless of mode.** The Firewall does not know the worktree's location and should not; #137 scopes the denial by backend as #135 §5.3 says.
5. **`~` is a literal component.** The file executors never expand it; treating it as home here would make the Firewall stricter than the containment behind it on a path that is harmless today.
6. **`.git` is matched as a first component only.** A nested `.git` directory inside the worktree is an ordinary directory to the executors today.
7. **Unknown nested keys are `argument_unexpected` by position, not name.** Naming them would put worker text in `detail`.
8. **`grep`'s `timeout` is an unknown key.** It is harness-injected after validation today; the canonical action does not carry it.
