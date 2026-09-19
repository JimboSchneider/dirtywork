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

`recover_name` produces the same result as the registry's `ToolRegistry.recover_name` with the registry table replaced by the eleven `ActionKind` values, and `TOOL_CALL_MARKERS` copied by value: the five raw markers (`[TOOL_CALLS]`, `<tool_call>`, `<function=`, `<function_call>`, `<|tool_call|>`) plus their sanitised forms with every character outside `[A-Za-z0-9_-]` replaced by `_`. The contract, unchanged from the registry:

- a name that is already an `ActionKind` value is returned as-is with marker `None` and cut `0`;
- otherwise, if some marker occurrence leaves a suffix that strips to an `ActionKind` value, the occurrence with the latest end wins (the longest marker among ties), returning `(suffix, marker, position)`;
- otherwise the name is returned unchanged with marker `None`; `check_request` then reports `tool_unknown` or `tool_name_invalid`.

The algorithm is not the registry's. The registry collects every marker occurrence, sorts them, and slices a suffix for each, which is quadratic on a name made of repeated markers (measured 2026-09-19: 22K characters 6 ms, 88K 69 ms, 352K 1.1 s). This issue's implementation is linear and reaches the same answer by working from the end: strip trailing whitespace; the remaining tail must end in an `ActionKind` value; walk back over the whitespace before that value; a marker must end exactly there, checked longest first. A winning candidate in the registry's search must leave a suffix that strips to a kind value, so its marker ends exactly at the start of the whitespace run before that value, and the latest-end candidate is that one; the two algorithms therefore agree on every input, and a test proves it on a fixture of registry-shaped names plus pathological repeated-marker strings with and without a valid tail.

Recovery runs only when `tool_name` is a `str`; anything else goes straight to `check_request`, which reports `tool_name_invalid`. It runs before the length check because it is linear and the name is already in memory (section 14, decision 3); a test canonicalizes a one-mebibyte marker-only name in well under a second. A second test asserts the marker tuple equals `dirtywork.toolspec.TOOL_CALL_MARKERS` element for element, so the two copies cannot drift until #143 removes one.

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
| `command` | a `str` | the string unchanged, byte for byte |
| `edits` | a `list` of 1 to `MAX_COLLECTION_ITEMS` objects, each with exactly the keys `old` and `new`, both `str`, `old` nonempty | a `tuple[Edit, ...]` |

Anything a value kind does not accept is `argument_type_invalid`. `finish` is the one kind whose only parameter is optional here although the registry requires it: the Runner already canonicalizes a missing summary to `""`, and #135 §4.2 pins that. This is the one declared difference between `FIELD_TABLE` and the registry's specs, and the cross-check test in section 12 carries it as its single exception.

The registry's `timeout` on `grep` is harness data injected after validation and is not a field; a worker-supplied `timeout` on `grep` is an unknown top-level key and is dropped. A test cross-checks `FIELD_TABLE` against the live `builtin_tools` specs: same parameter names in the same order, same required set, same defaults, for every kind. That test, not the package, imports the registry.

## 5. The pass

`canonicalize(request)` runs these steps in order and stops at the first failure. The order is part of the contract and is tested.

1. **Name recovery** when `tool_name` is a `str`, then **`check_request`** (#135 §6.2). A `Rejection` from it is returned as-is with `dropped_keys = 0`, so the validator's documented precedence holds for every malformed request, including a non-string name.
2. **Kind.** `kind = ActionKind(request.tool_name)`; `check_request` step 4 guarantees membership.
3. **Required fields.** For each required field in table order, the key must be present in `arguments`, else `argument_missing` with `detail` naming the field. Presence, not value: a present `null` is step 5's business. Required fields are checked before unknown keys so a misspelled required key reports the missing name rather than an unexpected one.
4. **Unknown top-level keys** are dropped and counted; the count becomes `dropped_keys`. They are never rejected at the top level (#135 §12, decision 2).
5. **Per field, in table order:**
   - absent optional field: the default;
   - present `null`: the default if the field is optional (nullable or not), `argument_type_invalid` if it is required;
   - otherwise the value kind's acceptance rule, else `argument_type_invalid` naming the field.
6. **Bounds**, per field, immediately after its coercion, so the first bad field in table order is the one reported:
   - a string over its bound is `string_too_long` naming the field and the limit;
   - an `int` field outside its domain after coercion is `number_out_of_range`: `offset` in `0..MAX_INT`, `limit` in `1..MAX_INT`. The domain is checked on the coerced value, so `"2147483648"` for `offset`, which `check_request` cannot see as a number, is rejected here rather than raising inside `ReadFileArgs`;
   - `timeout` is clamped into `1..MAX_BASH_TIMEOUT` from either side once it reaches this step; the pass itself never rejects it. `check_request` runs first, so an integer `timeout` outside `[MIN_INT, MAX_INT]` is already `number_out_of_range` from step 1, while the string `"2147483648"` passes the structural check, coerces, and clamps to `600` here. Both are tested as a pair (section 14, decision 2);
   - `edits`: an empty list is `argument_type_invalid` (a list longer than `MAX_COLLECTION_ITEMS` never reaches this step; `check_request` rejects it as `collection_too_large`); an item that is not an object, or lacks `old` or `new`, or whose `old` or `new` is not a `str`, or whose `old` is empty, is `argument_type_invalid` with `detail` giving the index and field (`edits[3].old`); an item with any other key is `argument_unexpected` with the index. Item strings are bounded by `MAX_STRING_CHARS` (`string_too_long`).
7. **Paths.** Every `path` field goes through `normalize_path`; the canonical string replaces the raw one and the target class is kept for step 9.
8. **Args class.** The kind's class is constructed from the coerced fields. Its `__post_init__` is the last line (#135 §4.2); after steps 5 and 6 it cannot raise on worker input, and if it does, the raise propagates.
9. **Capabilities and status** per section 8, then `CanonicalAction(schema_version=FIREWALL_SCHEMA_VERSION, call_id, turn, kind, args, capabilities, semantic_status)`.

`Rejection.detail` is composed by the harness, is at most `MAX_DETAIL_CHARS`, and names a field, an index or a limit. It never quotes worker text, including key names: an unexpected nested key is reported by position, not by name.

## 6. Paths

`normalize_path(raw: str) -> NormalizedPath(path: str, target: TargetClass)` is pure string work and never touches the filesystem.

Normalization: the input is split on `/`; empty components and `.` components are dropped; every `..` component is kept exactly where it is; the result is joined with `/`; an empty result is `.`; an input that begins with `/` keeps a single leading `/`. So a trailing slash disappears, `src//x.py` and `src/./x.py` become `src/x.py`, `./../x` becomes `../x`, and `src/a/../x.py` stays `src/a/../x.py`. Bytes are never changed: no case folding, no Unicode normalization, no `~` expansion, no backslash conversion, because the file executors do none of those (section 14, decisions 4 and 5).

`..` is never collapsed because collapsing it lexically can change the execution target. With a symlink `link -> nested/child` in the worktree, the host resolves `link/../target.txt` to `nested/target.txt`, while `posixpath.normpath` gives `target.txt`; a canonical action carrying the collapsed path would read or write a different file than the worker asked for once #138 hands canonical arguments to the executor. Dropping `.` and empty components and a trailing slash cannot cross a symlink, so those are safe. A test with a real symlink in a temporary directory pins this: it asserts the two resolutions differ and that `normalize_path` keeps the `..`.

`TargetClass` is a four-member `str` enum:

| Target | When | Capability it adds |
| --- | --- | --- |
| `outside` | the input is absolute, or the normalized path's first component is `..` | `HOST_FS`, every kind |
| `repo_metadata` | the first component is exactly `.git` and no component is `..` | `REPO_CONTROL`, write kinds only |
| `parent_ref` | some component after the first is `..`, and the path is neither absolute nor leading-`..` | none; the executor's containment decides |
| `workspace` | no component is `..` and neither of the first two rows applies | none |

`outside` is a lexical classification, like the absolute-path row: a leading `..` is applied to the worktree root itself, so the path leaves the worktree before anything else happens, even though a later component can lead back in (`../<worktree-name>/x` names a file inside). The Firewall records the lexical fact; #137 decides what an `outside` target means per backend, and the executor's resolution still decides where the path really lands. An interior `..` cannot even be classified lexically, so `parent_ref` is honest about that: the Firewall adds no capability, and the executor's existing resolution keeps refusing real escapes exactly as it does today. #137 may treat `parent_ref` as it likes; nothing here pre-empts it. The canonical string keeps every component for all four classes (`../../etc/passwd` stays `../../etc/passwd`, `/work/../etc/passwd` stays `/work/../etc/passwd`), so identity distinguishes targets and #141's evidence can name one without the raw input. `.git` matches the component exactly: `.gitignore` and `src/.git/x` are `workspace`, and `.git/../x` is `parent_ref`. The write kinds are `write_file`, `append_file`, `edit_file`, `apply_edits`, `insert_before` and `insert_after`; the other kinds reading under `.git` stay `workspace`-capable because the executors allow those reads today.

The Firewall does not know where the worktree is, so an absolute path is `outside` even when it names a file inside the worktree. #137 owns the mode-scoped decision that the host backend accepts such a path today and the Docker backend does not (#135 §5.3); the Firewall's job is to make the target class deterministic and the path canonical.

## 7. Per-kind notes

- **`bash`.** `command` is the exact string the worker sent, every byte, including leading and trailing whitespace: a command ending in an escaped space (`printf 'x'\ `) means something different once the space is gone, so stripping can change what runs and would declare two different commands equivalent. The registry does strip commands, but only for its repeat-detection key, never for execution. `ls` and `ls\n` are therefore distinct identities here; `echo "a  b"` and `echo "a b"` are too. `timeout` follows the duration rule and the clamp. Capabilities are the base `SHELL` set only. `semantic_status` is `semantic_unknown` for every bash action produced here; #137's analyzer is the only code that may set `semantic_known`, and the #135 test that `PolicyDecision` cannot deny on `semantic_unknown` continues to hold.
- **`finish`.** `summary` defaults to `""`.
- **`grep`.** `pattern` and `glob` are bounded and kept verbatim; they are not paths and are not normalized. `glob` may be `null`.
- **`list_dir`.** `path` defaults to `.`.
- **`apply_edits`.** Section 5, step 6.
- **`read_file`.** `offset` and `limit` are coerced and bounded but are excluded from identity (#135 §9.1).

## 8. Capabilities and semantic status

Capability assembly is one expression per action: `BASE_CAPABILITIES[kind]`, plus `HOST_FS` when any path field's target is `outside`, plus `REPO_CONTROL` when a path field's target is `repo_metadata` and the kind is a write kind. A `parent_ref` or `workspace` target adds nothing. Every kind has at most one path field today, so "any" and "a" are the same thing; the rule is written for the set so a future kind with two paths needs no rewrite. `NETWORK`, `PRIVILEGE`, `REPO_PUBLISH`, `SYSTEM_CONTROL` and `REMOTE_CODE_EXEC` are never set in this issue; they are #137's.

`semantic_status` is `semantic_known` for the nine static kinds and `finish`, and `semantic_unknown` for `bash`.

## 9. Batch

`canonicalize_batch(requests)` walks the batch in order. The first request carrying a given `call_id` proceeds through `canonicalize`; every later request with an equal `call_id` (exact string equality on the id as given, before any validation) receives `Rejection(call_id_duplicate)` with `detail` giving its batch index, and is not canonicalized. All other requests are independent: a rejection never affects its neighbours (parent design §12). `batch_size` bounds stay in `check_request`, and consistency between each request's `batch_size` and `len(requests)` is the adapter's responsibility in #138, not checked here.

## 10. Idempotence and identity

Normalization is idempotent at the contract level (parent design §7): building an `ActionRequest` from a `CanonicalAction`'s own canonical fields and canonicalizing it again yields an equal `CanonicalAction` and an equal `action_identity`. Tests pin the equivalences the design wants and the distinctions it forbids collapsing:

| Same identity | Different identity |
| --- | --- |
| `src/x.py`, `./src/x.py`, `src/./x.py`, `src//x.py`, `src/x.py/` | `src/x.py` vs `src/y.py`; `src/x.py` vs `src/a/../x.py` |
| `read_file(path, offset=0)` vs `read_file(path, offset=10)` | `list_dir(".")` vs `list_dir("src")` |
| `bash("ls")` vs `bash("ls")` sent twice | `bash("ls")` vs `bash("ls\n")` vs `bash("  ls  ")`; `bash("ls")` vs `bash("ls -l")`; `bash('echo "a  b"')` vs `bash('echo "a b"')` |
| `grep(pattern, path=".")` vs `grep(pattern)` (default) | `grep(p, glob=None)` vs `grep(p, glob="*.py")` |
| `../x` vs `./../x` | `../x` vs `x`; `link/../x` vs `x` |

`action_identity` and `rejection_identity` themselves are unchanged from #135; this issue only makes their inputs canonical.

## 11. Errors, bounds and performance

- Every bound the pass applies is a `bounds.py` constant already pinned in #135; this issue adds no constant.
- `FirewallInternalError` propagates out of `canonicalize` and `canonicalize_batch`; neither catches anything.
- The pass is a single walk over at most a handful of fields, one linear path normalization and one linear name recovery; the only regular expression is the registry's duration pattern, applied to a string the registry would apply it to today. No step re-reads `arguments` after step 5. Nothing here is claimed to meet the static-tool target of p95 under 1 ms on reference hardware (parent design §17); #142 measures it, and the tests in section 12 only guard against the superlinear cases found in review.

## 12. Tests

Three files, all under `tests/`. Tests may import `dirtywork.builtin_tools` and `dirtywork.toolspec`; the package may not.

`tests/test_firewall_paths.py`:

- one table of `(input, expected path, expected target)` covering every rule in section 6: empty, `.`, trailing slash, repeated slashes, `.` in every position, `..` leading, interior and trailing, absolute inputs with and without `..`, `.git` as first component versus elsewhere versus `.gitignore` versus `.git/../x`, `~`, backslashes, non-ASCII bytes, and a path of exactly `MAX_PATH_CHARS`;
- the symlink fixture of section 6: a temporary directory with `link -> nested/child`, asserting that `os.path.realpath` and `posixpath.normpath` disagree on `link/../target.txt` and that `normalize_path` keeps the `..`;
- the identity equivalence and distinctness sets of section 10 that concern paths, checked through `action_identity` on real actions;
- `normalize_path` is total on `str`: no input raises.

`tests/test_firewall_normalize.py`:

- `recover_name` against the registry's own `recover_name` on a fixture of registry-shaped names (plain, each marker, each sanitised marker, whitespace padding, nested markers, a marker with no valid tail, a valid tail with no marker) and on pathological repeated-marker strings with and without a valid tail, asserting equal `(name, marker, cut)`; a one-mebibyte marker-only name completes in under one second; the marker-tuple equality test of section 3;
- a non-string `tool_name` (`None`, a list) is `tool_name_invalid` from `check_request`, not an exception;
- `int` boundaries after coercion: `"2147483647"` accepted for `offset`, `"2147483648"` and `"-1"` rejected as `number_out_of_range`, `"0"` rejected for `limit`; `timeout` clamped for `0`, `-5`, `601` and the string `"2147483648"`, paired with the integer `2147483648` being `number_out_of_range` from `check_request` before the pass runs;
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
- `FIELD_TABLE` versus the live registry: names in order, required set, defaults, for every kind, with one declared exception: `finish.summary` is required with no default in the registry and optional with default `""` here (section 4);
- import isolation of `paths.py` and `normalize.py`.

`tests/test_firewall_parity.py` pins the pass to the registry's **validation** step, `toolspec._validate_args(spec, args)`, not to `ToolRegistry.execute`, which additionally clamps `timeout` at the top, applies the run deadline and byte caps, and runs the tool. The contract is: on the shared domain, the same accept-or-reject outcome, and on accept, equal values for every field the registry produces. Values are compared after these translations, because the registry does not normalize at validation: paths as strings after both sides drop `.` and empty components; `edits` by mapping each registry `dict` to `Edit(old, new)` and comparing the resulting tuple to the canonical `args.edits`; everything else by equality.

The shared domain is the set of inputs that satisfy every bound the Firewall applies and the registry does not check at validation. Concretely: every value kind's accepted forms; unknown top-level keys up to `MAX_ARGUMENT_KEYS` in total; missing required keys; numeric-string and duration coercion; the `apply_edits` schema; and every string within its section 4 bound, every `int` within its section 5 domain, every container within the #135 §6.2 structural limits. Inputs beyond those bounds are outside the domain by definition, since the registry has no opinion on them at validation; the table below lists them with the rest of the exceptions, and each row has its own test asserting both behaviors so the difference is on the record:

| Input | Registry validation | This issue | Why |
| --- | --- | --- | --- |
| `null` on an optional parameter whose default is not `None` (`offset`, `limit`, `list_dir.path`, `grep.path`, `timeout`) | rejects (`bad_args`) | the default | section 14, decision 1. `grep.glob = null` is not an exception: both sides accept it as `None` |
| `offset < 0`, `limit < 1` | accepts | `number_out_of_range` | section 5, step 6; the registry has no domain check |
| coerced `offset` or `limit` above `MAX_INT` (a numeric string) | accepts | `number_out_of_range` | section 5, step 6 |
| `bash` `timeout` of `0` or below | accepts unchanged; `execute` passes `0` through | clamped to `1` | section 14, decision 2 |
| `bash` `timeout` above `600`, including a numeric string above `MAX_INT` | accepts unchanged; `execute` clamps to `600` | clamped to `600` | same rule, applied at validation; the integer form above `MAX_INT` is rejected by `check_request` on both the shared and the exception side, so it is not in this row |
| a string over its section 4 bound (`path`, `command`, `pattern`, `glob`, `summary`, content strings) | accepts | `string_too_long` | the registry bounds only total input bytes, and only in `execute` |
| more than `MAX_ARGUMENT_KEYS` top-level keys, or any other #135 §6.2 structural limit | accepts and drops them | `check_request` rejects | structural bounds are the Firewall's; the registry drops any number of unknown keys |
| `finish` with no `summary` | rejects (`argument_missing`) | `summary = ""` | Runner canonicalizes it today; #135 §4.2 |
| `apply_edits` item with empty `old` | accepts; the tool fails at execution | `argument_type_invalid` | `Edit.old` must be nonempty (#135 §4.2) |
| unknown nested key in an `edits` item | rejects (`additionalProperties`) | `argument_unexpected` | same outcome, different label; asserted equal-outcome |

Expected counts per brief are recorded in the plan from the dry run.

## 13. Compatibility

No runtime path changes: the Runner, registry and executors do not import the package. The registry's `recover_name` and `_validate_args` keep working exactly as before; this issue adds a second implementation of each rule inside the Firewall and pins the two together with tests, so #138 can switch the Runner to the Firewall with only the section 12 exception table as worker-visible differences, and #143 can delete the registry's copies. Two registry behaviors are deliberately not part of the parity contract because they are not validation: `ToolRegistry.execute`'s timeout clamp, deadline clamp and byte caps stay in the executor, and the repeat-detection key's `normpath` and `strip` stay in the registry, since they exist to spot an idle loop and must not shape what runs or what a denial is keyed on. Run artifacts, the transcript and `guardrail_block` events are untouched.

## 14. Decisions taken that Jim may want to override

1. **`null` on an optional parameter means its default.** The registry rejects `offset: null` today with a `bad_args` strike. A model that spells out "no offset" is asking for the default, and the #135 validator already lets `null` through. Recorded as a deliberate leniency; #140 can tighten it.
2. **`timeout` is clamped into `1..MAX_BASH_TIMEOUT` from both sides after `check_request` has passed, and the pass never rejects it.** The registry's validation passes `0` and `601` through; its executor clamps the top only, and `0` times out at once, which nothing intends. `#135 §4.2` already requires `1..MAX_BASH_TIMEOUT` after coercion. #135's precedence is preserved: an integer `timeout` outside `[MIN_INT, MAX_INT]` is `number_out_of_range` from `check_request` before the pass sees it, while the same value as a numeric string coerces and clamps; the pair is tested together.
3. **Name recovery runs before the length check, on any string length, with a linear algorithm that is not the registry's.** The registry's search is quadratic on repeated markers (section 3); copying it would have made an unbounded pre-validation step. The result is proven equal on a fixture, and the registry's copy is left alone until #143.
4. **Absolute paths are `outside` regardless of mode.** The Firewall does not know the worktree's location and should not; #137 scopes the denial by backend as #135 §5.3 says.
5. **`~` is a literal component.** The file executors never expand it; treating it as home here would make the Firewall stricter than the containment behind it on a path that is harmless today.
6. **`.git` is matched as a first component only.** A nested `.git` directory inside the worktree is an ordinary directory to the executors today.
7. **Unknown nested keys are `argument_unexpected` by position, not name.** Naming them would put worker text in `detail`.
8. **`grep`'s `timeout` is an unknown key.** It is harness-injected after validation today; the canonical action does not carry it.
9. **Interior `..` is kept and classed `parent_ref` with no capability.** Collapsing it can change the execution target through a symlink (section 6), and classifying it needs the filesystem the Firewall must not touch. The executor's containment keeps refusing real escapes as today. The cost is that `src/a/../x.py` and `src/x.py` are different identities, which is the conservative side of the parent design's rule against collapsing materially different targets.
10. **Commands are canonical byte for byte.** Stripping can change shell meaning (section 7), so the Supervisor's exact-equivalent count sees `ls` and `ls\n` as different denials. Merging them would require a shell-aware normalizer, which is #137's analyzer territory at most and out of scope here.
