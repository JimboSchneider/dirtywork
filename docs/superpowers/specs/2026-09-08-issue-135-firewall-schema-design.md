# Worker Action Firewall B: canonical schema, bounds, capabilities and reason codes

Date: 2026-09-08
Status: design for review
Issue: [#135](https://github.com/JimboSchneider/dirtywork/issues/135), part of [#133](https://github.com/JimboSchneider/dirtywork/issues/133); depends on [#134](https://github.com/JimboSchneider/dirtywork/issues/134) (merged as the [inventory](2026-09-06-worker-action-firewall-inventory.md))
Parent design: [Worker Action Firewall](2026-09-06-worker-action-firewall-design.md)
Baseline: `main` at `d3c7ce8` (package `0.13.1`)

## 1. Scope

This issue defines the closed, harness-owned contract that every later Firewall issue builds on. It ships types, enums, bounds, one boundary validator and their tests. It ships **no** normalization (#136), **no** policy rules (#137), **no** Runner wiring (#138) and changes **no** runtime behavior: nothing in `dirtywork/` imports the new package until #136.

In scope:

- a new package `dirtywork/firewall/` (section 2);
- the boundary type `ActionRequest` and a tool-agnostic structural validator that rejects malformed, unknown and oversized worker input with deterministic reason codes (sections 3, 6);
- the immutable `CanonicalAction` and its per-tool argument shapes (section 4);
- the closed `ActionKind`, `Capability`, `Decision`, `ReasonCode`, `ReasonClass` and `SemanticStatus` vocabularies (sections 4, 5, 7);
- the checked-in capability table mapping the complete #134 inventory (section 5);
- `PolicyDecision` and the versioned `FirewallEvent` skeleton, covering both canonicalized actions and request-stage rejections, with `action_identity` and `rejection_identity` (sections 8, 9);
- the failure and versioning contracts (sections 6, 10);
- tests that pin every closed vocabulary (section 11).

Out of scope, with the owning issue: tool-specific coercion, path normalization and `recover_name` placement (#136); rule precedence and the bash analyzer (#137); structural DENY tool results and batch handling (#138); routing static tools (#139); legacy `guardrail_block` migration (#140); emitting events to the transcript or Supervisor (#141); custom `ToolSpec` capability registration (deferred, section 5.4).

## 2. Package layout

```text
dirtywork/firewall/
  __init__.py        a docstring only until the final brief, which adds the re-exports below
  errors.py          FirewallInternalError; imports nothing from the package
  bounds.py          integer constants only (section 6.1); imports nothing from the package
  reasons.py         ReasonClass, ReasonCode, reason_class()
  capabilities.py    ActionKind, Capability, BASE_CAPABILITIES, LEGACY_RULES, FILE_TARGET_RULES
  schema.py          ActionRequest, Edit and the ten per-kind args classes, CanonicalArgs,
                     CanonicalAction, Decision, SemanticStatus, PolicyDecision, FirewallEvent,
                     action_identity(), rejection_identity()
  request.py         Rejection, check_request()
```

Import order is strictly downward: `errors` and `bounds` import nothing; `reasons` imports nothing; `capabilities` imports `reasons`; `schema` imports `errors`, `bounds`, `reasons`, `capabilities`; `request` imports all of them. No file imports a later one, so each brief's tests pass before the next file exists.

Importing any submodule executes `__init__.py` first, so eager re-exports would break every brief before the last one. Therefore `__init__.py` holds only a docstring until the `request.py` brief, which replaces it with the explicit imports and this `__all__`. Tests always import from submodules (`from dirtywork.firewall.bounds import ...`), never from the package root, so they pass at every stage:

```python
__all__ = [
    "FIREWALL_SCHEMA_VERSION", "IDENTITY_VERSION",
    "FirewallInternalError",
    "ReasonClass", "ReasonCode", "reason_class",
    "ActionKind", "Capability", "BASE_CAPABILITIES", "LEGACY_RULES", "FILE_TARGET_RULES",
    "ActionRequest", "Edit", "ReadFileArgs", "WriteFileArgs", "AppendFileArgs", "EditFileArgs",
    "ApplyEditsArgs", "InsertArgs", "ListDirArgs", "GrepArgs", "BashArgs", "FinishArgs",
    "CanonicalArgs", "CanonicalAction", "Decision", "SemanticStatus", "PolicyDecision",
    "FirewallEvent", "action_identity", "rejection_identity",
    "Rejection", "check_request",
]
```

Bound constants other than the two versions are imported from `dirtywork.firewall.bounds` directly.

**Package registration.** `pyproject.toml` enumerates packages explicitly (`[tool.setuptools] packages = [...]`), so the first brief adds `"dirtywork.firewall"` to that list. Without it the source tree would pass tests while the wheel shipped no Firewall. Section 11 pins the list; the plan's host checklist also builds the wheel and imports `dirtywork.firewall` from it before the PR opens.

Rules for the package:

- Python 3.9 compatible (`pyproject.toml` says `>=3.9`): `class X(str, enum.Enum)`, not `StrEnum`; `@dataclass(frozen=True)` without `kw_only` or `slots`; `from __future__ import annotations`.
- Standard library only. `hashlib`, `json`, `enum`, `dataclasses`, `typing`.
- `dirtywork/firewall/` may import `dirtywork.providers.ToolCall` (the boundary input) and nothing else from `dirtywork/`. Tests, not the package, cross-check constants against `builtin_tools` and `guardrails` (section 11) so the Firewall never depends on executor modules.
- No module in `dirtywork/` outside the package imports it in this issue.

`errors.py`, `bounds.py` and the `pyproject.toml` line share the first brief; every other file is one brief; `schema.py` may be two. The `__init__.py` re-exports ride with `request.py`.

## 3. `ActionRequest`: the boundary object

`ActionRequest` is the only way worker input enters the Firewall. It exists at the adapter/Firewall seam and is never passed to policy rules, executors or the Supervisor.

```python
@dataclass(frozen=True)
class ActionRequest:
    call_id: str            # ToolCall.id; printable ASCII, no whitespace (check 1)
    tool_name: str          # ToolCall.name as the adapter parsed it; #136 inserts recover_name before this
    arguments: Any          # ToolCall.arguments: the decoded object, or None
    parse_error: str | None # ToolCall.error
    raw_chars: int          # len(ToolCall.raw_arguments); the payload itself is not copied
    turn: int               # 1-based Runner turn
    batch_index: int        # 0-based position in the provider batch
    batch_size: int         # calls in the batch
```

Design points:

- **Worker provenance by construction.** Only the adapter seam builds an `ActionRequest`. Harness-originated invocations (`--verify`, `changes.fingerprint`, export, resume) never construct one, so trusted invocations cannot be mistaken for worker actions and never require a worker dictionary. The inventory's "trusted invocation" rows are satisfied by absence, not by a flag a caller could forget.
- **`arguments` is typed `Any` on purpose.** It is the one field that holds untrusted structure. Section 6 bounds it; #136 types it per tool. Nothing downstream of `check_request` sees it.
- **`call_id` is provider-influenced and is allowed into evidence.** It is the one worker-facing identifier the Firewall keeps verbatim, because every structural tool result must be addressed by it. It is bounded in length and charset (section 6.2) and carries no meaning to policy; that is the documented exception to "only harness-generated identifiers enter policy and Supervisor rule code", and the Supervisor design keys nothing on it.
- **`raw_chars` instead of the raw string.** The transcript already keeps the model's bytes; the Firewall needs only the size to enforce the payload bound in O(1).
- Constructor: `ActionRequest.from_tool_call(tc, *, turn, batch_index, batch_size)` copies the four `ToolCall` fields and computes `raw_chars`. It performs no validation; that is `check_request`'s job, so a construction failure can only be a programming error.

## 4. `CanonicalAction` and the closed argument shapes

### 4.1 `ActionKind`

One member per built-in tool, values equal to the registered tool names, in `BUILTIN_SPECS` order:

```text
read_file, write_file, append_file, edit_file, apply_edits,
insert_before, insert_after, list_dir, grep, bash, finish
```

A tool name that is not an `ActionKind` value is rejected with `tool_unknown` (section 6). `insert_before` and `insert_after` are distinct kinds so canonical identity keeps them apart (inventory §1).

### 4.2 Per-kind argument shapes

Each kind has exactly one frozen dataclass. Field names match the advertised parameter names so #136's mapping is mechanical. All strings are bounded by section 6.1.

| Kind | Args class | Fields | Notes |
| --- | --- | --- | --- |
| `read_file` | `ReadFileArgs` | `path: str`, `offset: int`, `limit: int` | defaults filled by #136 (`0`, `400`) |
| `write_file` | `WriteFileArgs` | `path: str`, `content: str` | |
| `append_file` | `AppendFileArgs` | `path: str`, `text: str` | |
| `edit_file` | `EditFileArgs` | `path: str`, `old_string: str`, `new_string: str` | |
| `apply_edits` | `ApplyEditsArgs` | `path: str`, `edits: tuple[Edit, ...]` | `Edit(old: str, new: str)`; 1..`MAX_COLLECTION_ITEMS` |
| `insert_before` | `InsertArgs` | `path: str`, `anchor: str`, `text: str` | shared class; kind carries placement |
| `insert_after` | `InsertArgs` | same | |
| `list_dir` | `ListDirArgs` | `path: str` | default `"."` |
| `grep` | `GrepArgs` | `pattern: str`, `path: str`, `glob: str | None` | the registry-injected `timeout` is harness data and is not part of the canonical action |
| `bash` | `BashArgs` | `command: str`, `timeout: int` | `timeout` in `1..MAX_BASH_TIMEOUT` after #136 coercion |
| `finish` | `FinishArgs` | `summary: str` | missing summary canonicalizes to `""` (preserves Runner behavior) |

`CanonicalArgs = Union[...]` of the ten classes. There is no generic `dict`-backed variant; a tool with no shape here cannot be canonicalized, which is the point.

Annotations enforce nothing at runtime, so every args class has a `__post_init__` that establishes the bounded, immutable contract and raises `FirewallInternalError` otherwise:

- every `str` field is a `str` (not a subclass surprise like `bytes`, not `None` unless the table says `| None`) and within its bound: `path` ≤ `MAX_PATH_CHARS`, `command` ≤ `MAX_COMMAND_CHARS`, `pattern` ≤ `MAX_PATTERN_CHARS`, `glob` ≤ `MAX_GLOB_CHARS`, `summary` ≤ `MAX_SUMMARY_CHARS`, all other strings ≤ `MAX_STRING_CHARS`;
- every `int` field is an `int` and not a `bool`, and within its domain: `offset >= 0`, `limit >= 1`, `timeout` in `1..MAX_BASH_TIMEOUT`, all within `[MIN_INT, MAX_INT]`;
- `edits` is a `tuple` (a `list` is rejected, so a frozen dataclass cannot smuggle a mutable container), of length `1..MAX_COLLECTION_ITEMS`, every item an `Edit`, and `Edit.old` nonempty.

One private helper per check (`_str_field`, `_int_field`, `_tuple_field`) in `schema.py` keeps the ten classes to one line per field. These constructors are the last line, not the worker-facing one: #136 must reject a worker value with the precise reason code (`string_too_long`, `number_out_of_range`, `argument_type_invalid`) before constructing, so a constructor raise can only mean a harness bug and it still fails closed.

### 4.3 `CanonicalAction`

```python
@dataclass(frozen=True)
class CanonicalAction:
    schema_version: int          # == FIREWALL_SCHEMA_VERSION
    call_id: str
    turn: int
    kind: ActionKind
    args: CanonicalArgs          # instance must match kind (checked in __post_init__)
    capabilities: frozenset[Capability]   # nonempty; BASE_CAPABILITIES[kind] plus analyzer additions
    semantic_status: SemanticStatus
```

Invariants enforced in `__post_init__` (raise `FirewallInternalError`, never silently coerce):

- `kind` is an `ActionKind` member and `semantic_status` a `SemanticStatus` member (identity checks, not value equality, so a look-alike string fails);
- `type(args)` is the class the table assigns to `kind`;
- `call_id` satisfies check 2 of section 6.2 and `turn` is an `int` in `1..MAX_INT`;
- `capabilities` is a nonempty `frozenset` whose members are all `Capability` members;
- `BASE_CAPABILITIES[kind] <= capabilities` (an analyzer may add authority, never remove the base);
- `schema_version == FIREWALL_SCHEMA_VERSION`;
- every string field is within its bound (belt and braces: `check_request` bounds raw input, this bounds the canonical form).

`CanonicalAction` carries `content`/`text`/`old`/`new` because the executor receives the canonical object (design §6: "execution handoff"). Those fields are excluded from identity and from `FirewallEvent` (sections 8, 9).

### 4.4 `SemanticStatus`

```text
semantic_known     the deterministic analyzer fully classified the action's effect
semantic_unknown   permitted input whose exact effect the analyzer cannot classify
```

Static tools and `finish` are always `semantic_known`: their effect is their argument shape. `bash` is `semantic_known` only when #137's analyzer says so; otherwise `semantic_unknown`. This is a property of the action, so it lives on `CanonicalAction` and is copied into the event. Per the parent design §7, no code in this package or any later issue may map `semantic_unknown` to `DENY`; section 11 pins that with a test on `PolicyDecision`.

## 5. Capabilities

### 5.1 `Capability`

Closed, eleven members. Values are the lowercase names.

| Member | Authority it names | Inventory source |
| --- | --- | --- |
| `WORKSPACE_READ` | read or list files inside the worktree | `read_file`, `list_dir`, `grep` rows |
| `WORKSPACE_WRITE` | create or modify files inside the worktree | six mutating tool rows |
| `SHELL` | run an arbitrary command in the sandbox | `bash` row |
| `RUN_CONTROL` | request completion of the run | `finish` row |
| `REPO_CONTROL` | write shared repository control state: root `.git` targets, parent-repo refs/config | file-tool `.git` refusal; guardrail rule 3 |
| `REPO_PUBLISH` | publish repository changes outward | guardrail rule 2 |
| `HOST_FS` | read, write, move or enter paths outside the worktree, including operator toolchain roots | guardrail rules 4, 7, 8 |
| `PRIVILEGE` | escalate OS privilege | guardrail rule 1 |
| `SYSTEM_CONTROL` | control the host system or unrelated processes | guardrail rule 6 |
| `NETWORK` | deterministically recognized network use | `Caps.network` metadata; reserved for #137's analyzer |
| `REMOTE_CODE_EXEC` | execute content fetched from the network | guardrail rule 5 |

`NETWORK` is reserved so the vocabulary is complete against `Caps.network`, the only network authority the inventory names. #137 decides which command shapes set it; nothing in #135 sets it.

### 5.2 `BASE_CAPABILITIES`

The authority every action of a kind requires before any argument analysis:

| Kind | Base set |
| --- | --- |
| `read_file`, `list_dir`, `grep` | `{WORKSPACE_READ}` |
| `write_file`, `append_file`, `edit_file`, `apply_edits`, `insert_before`, `insert_after` | `{WORKSPACE_WRITE}` |
| `bash` | `{SHELL}` |
| `finish` | `{RUN_CONTROL}` |

`append_file`, `edit_file`, `apply_edits` and the inserts read the file before writing it. That read is part of the write authority, not a separate `WORKSPACE_READ`, so a denied write never records read authority it did not have.

### 5.3 Legacy rule and file-target tables

`LEGACY_RULES` is a tuple of eight `(index, capability, reason_code)` entries in `guardrails._RULES` order, giving #140 a checked-in mapping from each ordered regex rule to its Firewall vocabulary:

| Rule | Capability | `ReasonCode` |
| --- | --- | --- |
| 1 sudo | `PRIVILEGE` | `privilege_escalation` |
| 2 git push | `REPO_PUBLISH` | `repo_publish` |
| 3 shared refs/config | `REPO_CONTROL` | `repo_control` |
| 4 destructive outside worktree | `HOST_FS` | `host_fs_destructive` |
| 5 download into interpreter | `REMOTE_CODE_EXEC` | `remote_code_exec` |
| 6 system control | `SYSTEM_CONTROL` | `system_control` |
| 7 redirect outside worktree | `HOST_FS` | `host_fs_redirect` |
| 8 cd outside worktree | `HOST_FS` | `host_fs_chdir` |

`FILE_TARGET_RULES` names the two file-tool refusals the backends make today, so #137/#139 have codes to deny with **before** the executor rather than after a pre-read:

| Refusal | Capability | `ReasonCode` |
| --- | --- | --- |
| write under root `.git` | `REPO_CONTROL` | `repo_metadata_target` |
| absolute or escaping path (Docker `_rel`; host `resolve_in_worktree`) | `HOST_FS` | `path_outside_workspace` |

Neither table changes behavior in this issue. Mode scoping (host vs Docker) stays where it is today and is #137's input, not part of the vocabulary.

### 5.4 Custom `ToolSpec` (deferred)

`ToolRegistry.register` accepts caller-supplied tools, but the shipped CLI has no loader. Under this contract an unregistered name is `tool_unknown` and is denied. A registration API for embedders (`declare_capabilities(name, frozenset[Capability])` plus an args shape) is a later issue; adding it must not reopen `ActionKind` as a free string.

## 6. Bounds and the boundary validator

### 6.1 Constants (`bounds.py`)

| Constant | Value | Rationale |
| --- | --- | --- |
| `FIREWALL_SCHEMA_VERSION` | `1` | section 10 |
| `MAX_CALL_ID_CHARS` | `256` | OpenAI `call_…`, Anthropic `toolu_…` are well under; transcript caps ids nowhere today |
| `MAX_TOOL_NAME_CHARS` | `512` | marker-polluted names are recovered before this check (#136); the transcript already truncates at 200 for display |
| `MAX_RAW_ARGUMENT_CHARS` | `32 * 1024 * 1024` | worst-case JSON escaping (`\\u00XX`, six chars per char) of one `MAX_STRING_CHARS` string; still under the 64 MiB transport ceiling |
| `MAX_STRING_CHARS` | `5 * 1024 * 1024` | characters, not bytes: admits every string the backends' 5 MiB byte caps admit; those byte checks stay authoritative for file content |
| `MAX_PATH_CHARS` | `4096` | `PATH_MAX` |
| `MAX_COMMAND_CHARS` | `32_768` | new policy number; nothing bounds command input today (`tools.MAX_BASH_CHARS` caps bash *output*). Sized so a large heredoc still passes |
| `MAX_PATTERN_CHARS` | `4096` | grep pattern |
| `MAX_GLOB_CHARS` | `1024` | grep glob |
| `MAX_SUMMARY_CHARS` | `64_000` | new policy number, chosen to match the transcript's assistant-text cap so a summary is never longer than a reply |
| `MAX_ARGUMENT_KEYS` | `32` | top-level keys before the unknown-key drop |
| `MAX_NESTED_KEYS` | `8` | keys in any nested object (`apply_edits` edits have 2) |
| `MAX_NESTING_DEPTH` | `4` | `apply_edits` needs 3 |
| `MAX_COLLECTION_ITEMS` | `100` | equals `builtin_tools.MAX_APPLY_EDITS` |
| `MAX_INT` | `2**31 - 1` | numeric domain; `MIN_INT = -MAX_INT - 1` |
| `MAX_BASH_TIMEOUT` | `600` | equals `Caps.timeout_max` on `bash` |
| `MAX_BATCH_CALLS` | `32` | adapters bound nothing; Runner sees at most this many addressable calls per response |
| `MAX_DETAIL_CHARS` | `200` | `Rejection.detail`; one line of harness prose |
| `IDENTITY_VERSION` | `1` | section 9 |

Two values are the same quantity as an existing executor constant (`MAX_COLLECTION_ITEMS` and `MAX_BASH_TIMEOUT`); the test suite asserts those equalities (section 11) so they cannot drift. The other values are Firewall policy numbers and are pinned as literals. The Firewall does not import executor modules.

### 6.2 `check_request(request) -> Rejection | None` (`request.py`)

Tool-agnostic and structural. It knows the closed tool set and the bounds, and nothing about what any parameter means. Checks run in this fixed order and stop at the first failure; the order is part of the contract and is tested:

1. `batch_size <= MAX_BATCH_CALLS`. Else `batch_too_large`. (O(1) and known before any per-call work.)
2. `call_id`: nonempty `str`, `<= MAX_CALL_ID_CHARS`, every character in `0x21..0x7e` (printable ASCII, no whitespace). Else `call_id_invalid`.
3. `tool_name`: nonempty `str`, `<= MAX_TOOL_NAME_CHARS`. Else `tool_name_invalid`.
4. `tool_name` is an `ActionKind` value. Else `tool_unknown`.
5. `parse_error is None` and `arguments is not None`. Else `arguments_unparseable`.
6. `raw_chars <= MAX_RAW_ARGUMENT_CHARS`. Else `payload_too_large`.
7. `arguments` is a `dict`. Else `arguments_not_object`.
8. Structural walk of `arguments`, depth-first, in key order. Depth counts containers: the `arguments` dict is depth 1, and each nested `dict` or `list` adds 1, so an `apply_edits` payload is depth 3 (`arguments` → `edits` list → edit dict) and `{"a": [[[{}]]]}` is depth 5. Rules at every level:
   - depth `> MAX_NESTING_DEPTH` → `nesting_too_deep`, checked on entering a container before its children are visited;
   - a top-level object with more than `MAX_ARGUMENT_KEYS` keys, or a nested object with more than `MAX_NESTED_KEYS` → `collection_too_large`;
   - a list longer than `MAX_COLLECTION_ITEMS` → `collection_too_large`;
   - each key is checked together with its value, in key order, so the first failure met is the first reported; a key that is not a `str` → `argument_type_invalid`; a key longer than `MAX_STRING_CHARS` → `string_too_long`;
   - a `str` longer than `MAX_STRING_CHARS` → `string_too_long`;
   - an `int` outside `[MIN_INT, MAX_INT]` or a `float` that is not finite → `number_out_of_range` (`bool` passes here; parameter typing is #136's);
   - `None` passes: JSON `null` is a valid value today (`grep(glob=null)` succeeds and has a regression test), and null-valued extras are dropped by #136; field-level nullability is #136's;
   - any other type (`bytes`, custom objects) → `argument_type_invalid`.

Size checks on a container (key count, item count) run before its children are walked, so an oversized structure is rejected without traversing it.

Not checked here, by design: per-parameter limits and domains (`MAX_PATH_CHARS`, `offset >= 0`, nullability), required parameters, parameter types, unknown top-level keys, duplicate ids within a batch. Those need the tool's shape or the whole batch and belong to #136, which rejects with `argument_missing`, `argument_type_invalid`, `argument_unexpected`, `string_too_long`, `number_out_of_range` or `call_id_duplicate` before it constructs an args class (section 4.2). The codes are defined now so #136 adds no vocabulary.

`Rejection` is frozen: `reason_code: ReasonCode`, `detail: str` (`<= MAX_DETAIL_CHARS`, harness-composed, names a field or a limit, never quotes worker text). A `Rejection` becomes a `PolicyDecision(DENY, reason_code)` in #137; in this issue it is the validator's return value.

### 6.3 `FirewallInternalError` (`errors.py`)

Raised when the package finds its own invariant violated: an enum value that is not a member, a `CanonicalAction` whose `args` class does not match `kind`, a missing table entry. It subclasses `Exception`, not `ValueError`, so a `try/except ValueError` in an adapter cannot swallow it. The parent design §11 rule applies: whoever catches it (#137) must produce `DENY` with `firewall_internal_error`. It is never mapped to `ALLOW`, and section 11 tests that `PolicyDecision` cannot be built as `ALLOW` with any reason code at all.

## 7. Reason codes

### 7.1 `ReasonClass`

```text
malformed   the request could not be understood
bounds      the request exceeded a size, count or numeric limit
authority   the request asked for authority policy does not grant
internal    the Firewall failed; fail closed
```

`reason_class(code) -> ReasonClass` is a total function; the test in section 11 proves every code has a class.

### 7.2 `ReasonCode`

Values are the lowercase snake-case names. Never renamed, never reused (section 10).

| Class | Codes |
| --- | --- |
| `malformed` | `call_id_invalid`, `tool_name_invalid`, `tool_unknown`, `arguments_unparseable`, `arguments_not_object`, `argument_missing`, `argument_type_invalid`, `argument_unexpected` |
| `bounds` | `payload_too_large`, `string_too_long`, `collection_too_large`, `nesting_too_deep`, `number_out_of_range`, `batch_too_large`, `call_id_duplicate` |
| `authority` | `privilege_escalation`, `repo_publish`, `repo_control`, `host_fs_destructive`, `remote_code_exec`, `system_control`, `host_fs_redirect`, `host_fs_chdir`, `repo_metadata_target`, `path_outside_workspace` |
| `internal` | `firewall_internal_error` |

Twenty-six codes. The eight authority codes in guardrail order plus the two file-target codes give #140 a one-to-one parity key that is independent of the reason prose in `_RULES`; the prose stays the `guardrail_block.reason` transcript contract until #140 migrates it.

There is no `semantic_unknown` reason code. It is a `SemanticStatus`, and it cannot appear in `ReasonCode` by construction.

## 8. `Decision` and `PolicyDecision`

```python
class Decision(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"

@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reason_code: ReasonCode | None   # None iff ALLOW
    detail: str                      # <= MAX_DETAIL_CHARS; "" on ALLOW
```

`__post_init__` raises `FirewallInternalError` unless exactly one of these holds: `decision is ALLOW and reason_code is None`, or `decision is DENY and reason_code is a ReasonCode`. There is no third state and no WARN (parent design §10). An internal error is `DENY` + `firewall_internal_error`; #138 chooses the tool-result wording from the reason class.

## 9. `action_identity` and `FirewallEvent`

### 9.1 Identity

`action_identity(action: CanonicalAction) -> str` returns the lowercase hex SHA-256 of a versioned, deterministic serialization of the policy-relevant fields only:

```text
"dirtywork-firewall-identity/" IDENTITY_VERSION "\n"
kind "\n"
field "=" json(value) "\n"   for each identity field of that kind, in the order below
```

`json` is `json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)`. The serialization is encoded with `surrogatepass` before hashing: a raw tool name from a provider can carry a lone surrogate that strict UTF-8 refuses, and a rejection must still get an identity rather than raise. Identity fields per kind:

| Kind | Identity fields | Excluded |
| --- | --- | --- |
| `read_file` | `path` | `offset`, `limit` |
| `write_file`, `append_file` | `path` | `content`, `text` |
| `edit_file`, `apply_edits`, `insert_before`, `insert_after` | `path` | old/new/anchor/text |
| `list_dir` | `path` | |
| `grep` | `pattern`, `path`, `glob` | |
| `bash` | `command` | `timeout` |
| `finish` | (none: kind only) | `summary` |

Why targets and not contents: identity exists for exact-equivalent denial tracking (Supervisor design §11). A denied write to `.git/config` is the same denial whatever the content; a `bash` denial is keyed on the full canonical command, which is intentionally narrow (differently flagged commands do not compare equal). Two kinds with the same path hash differently because `kind` is serialized first. `capabilities` and `reason_code` are not in the hash; the Supervisor already keys on `reason_code + capability_set + action_identity`.

**Rejection identity.** A rejected request has no `CanonicalAction`, but the Supervisor still needs an exact-equivalent key for repeated denials of the same malformed or unknown call. `rejection_identity(request, rejection) -> str` hashes, with the same prefix and `IDENTITY_VERSION`:

```text
"rejection" "\n"
reason_code "\n"
tool_name "\n"     the bounded raw name when it passed check 3, else ""
```

So a model that keeps calling `read_files` produces one identity, and one that keeps sending a 6 MiB `write_file` produces another. The raw name is hashed, never emitted.

The hash is unsalted and gives no confidentiality: a consumer who can guess a command can confirm it from the identity. It exists for equality and dedup, not to hide its inputs. Contents are excluded for narrowness, not for secrecy.

### 9.2 `FirewallEvent`

One event shape covers both outcomes, because a DENY at the request stage has no kind, args, capabilities or semantic status and must not invent them:

```python
@dataclass(frozen=True)
class FirewallEvent:
    schema_version: int              # FIREWALL_SCHEMA_VERSION
    stage: str                       # "request" (rejected before canonicalization) | "action"
    turn: int
    call_id: str                     # "" when the rejection is call_id_invalid
    kind: ActionKind | None          # None at the request stage
    capabilities: tuple[str, ...]    # sorted Capability values; () at the request stage
    decision: Decision
    reason_code: ReasonCode | None   # None iff ALLOW
    reason_class: ReasonClass | None # reason_class(reason_code); None iff ALLOW
    action_identity: str             # 64 hex chars: action_identity() or rejection_identity()
    semantic_status: SemanticStatus | None   # None at the request stage
```

Two `@classmethod` constructors, and `__post_init__` enforces the stage rules:

- `FirewallEvent.from_action(action, policy)`: `stage="action"`; `kind`, `capabilities`, `semantic_status` copied from the action; `decision`/`reason_code` from the policy; `action_identity = action_identity(action)`.
- `FirewallEvent.from_rejection(request, rejection)`: `stage="request"`; `decision=DENY`; `reason_code` from the rejection; `kind=None`, `capabilities=()`, `semantic_status=None`; `action_identity = rejection_identity(request, rejection)`; `call_id` is the request's when it passed check 2, else `""`. This constructor also serves an internal error caught before an action exists (`reason_code=firewall_internal_error`); an internal error after an action exists goes through `from_action` with a `DENY` policy.
- Invariants: `stage="request"` ⇒ `decision is DENY`, `kind is None`, `capabilities == ()`, `semantic_status is None`; `stage="action"` ⇒ `kind`, `semantic_status` set and `capabilities` nonempty; `reason_code is None` ⇔ `decision is ALLOW` ⇔ `reason_class is None`.

**Supervisor counters.** Both stages are DENY evidence: a request-stage rejection counts toward total denial volume and toward exact-equivalent tracking under `reason_code + capability_set + action_identity` with an empty capability set. Request-stage events contribute nothing to the `semantic_unknown` aggregate. Whether the Supervisor weights the `malformed`/`bounds` classes differently from `authority` is issue #126's decision; `reason_class` is on the event so it can, without re-deriving anything.

`to_dict()` emits the field names above with enum members as their string values, `None` as JSON `null`, and `capabilities` as a list of strings, suitable for `transcript.write("firewall", **event.to_dict())` in #141. No raw arguments, no `args` dict, no detail prose: the Supervisor consumes only the five fields its design §4.1 lists, and they are all here. Where and how often events are written (no per-ALLOW spam, parent design §14) is #141's decision.

## 10. Versioning and compatibility

- `FIREWALL_SCHEMA_VERSION` starts at `1` and is stamped on `CanonicalAction` and `FirewallEvent`. Adding a field with a default, or adding an enum member, is additive and does not bump it. Removing or renaming a field or enum member, or changing the identity serialization, bumps it.
- `IDENTITY_VERSION` bumps whenever the serialization in 9.1 changes, so identities from different versions never compare equal by accident. `IDENTITY_VERSION` is included in the hashed text, so a bump changes every identity.
- `ReasonCode` values are append-only. A code that stops being emitted stays a member, marked deprecated in a comment, so stored evidence still decodes.
- Existing run artifacts (`schema_version: 2` on `run.json` and the transcript) are unaffected: this issue writes nothing to them. When #141 adds a `firewall` event, existing readers ignore unknown events by convention (inventory §5.7).
- `guardrail_block` events, their `reason` prose and rule order are untouched.
- `pyproject.toml` gains `"dirtywork.firewall"` in the explicit package list; no other packaging change.

## 11. Tests

All under `tests/test_firewall_*.py`, host-runnable with the standard suite, no Docker, no model. The pins below are the closed-contract mechanism the issue asks for: each is a literal list in the test, so growing a vocabulary means editing the test on purpose.

1. **Vocabulary pins.** `sorted(m.value for m in ActionKind)`, `Capability`, `ReasonCode`, `ReasonClass`, `Decision`, `SemanticStatus` each equal a literal list. `len(ReasonCode) == 26`.
2. **Lockstep with the registry.** `[k.value for k in ActionKind] == [s.name for s in builtin_tools.BUILTIN_SPECS]`.
3. **Lockstep with guardrails.** `len(LEGACY_RULES) == len(guardrails._RULES)`; the capability and reason code at each index equal the table in 5.3, and the eight reason codes are distinct.
4. **Constant pins.** `MAX_COLLECTION_ITEMS == builtin_tools.MAX_APPLY_EDITS` and `MAX_BASH_TIMEOUT == BASH_SPEC.caps.timeout_max` (same quantity, must not drift); every other constant in 6.1 equals its literal from the table.
5. **Totality.** Every `ActionKind` has a `BASE_CAPABILITIES` entry and an args class; every `ReasonCode` has a `reason_class`; every `Capability` appears in at least one of `BASE_CAPABILITIES`, `LEGACY_RULES`, `FILE_TARGET_RULES`, or the documented `NETWORK` reservation.
6. **`check_request` malformed input.** (Also: `{"pattern": "x", "glob": None}` and `{"path": "a", "extra": None}` return `None`.) One case per code it can return, in order: `batch_size=33`; empty/oversized/whitespace-containing id; empty/oversized name; unknown name (including a marker-polluted name, which #136 will recover but #135 rejects); `parse_error` set; `arguments=None`; `raw_chars` over; `arguments` a list/str/int; depth 5; 33 top-level keys; a nested object with 9 keys; a 101-item list; a non-str key; a 5 MiB + 1 string; `2**31` int; `float("inf")`; `bytes` value; a key of `MAX_STRING_CHARS + 1` characters. Plus a well-formed `apply_edits` payload with 100 edits at depth 3 returns `None`.
7. **First-failure order.** A request that is both unknown-tool and oversized reports `tool_unknown`; one that is both non-object and oversized reports `payload_too_large`; `{"first": float("inf"), 1: "a"}` reports `number_out_of_range`, not the later bad key.
8. **Rejection detail** never contains worker-supplied text: build a request whose key, value and tool name are a sentinel string and assert the sentinel is absent from `detail`, and `len(detail) <= MAX_DETAIL_CHARS`.
9. **`PolicyDecision` invariants.** `ALLOW` with any reason code raises; `DENY` with `None` raises; `DENY` with a bare string raises; `FirewallInternalError` is not a `ValueError`.
10. **`semantic_unknown` cannot deny.** `PolicyDecision(DENY, reason_code=...)` accepts only `ReasonCode` members and `"semantic_unknown"` is not one; `CanonicalAction(semantic_status=UNKNOWN)` constructs fine and carries no reason code.
11. **Constructor invariants.** `CanonicalAction`: wrong args class for kind raises; a string `"bash"` for `kind` raises; empty capabilities raises; capabilities missing the base raises; a `str` inside `capabilities` raises; `schema_version=2` raises; `turn=0` raises. Args classes: `ApplyEditsArgs(edits=[Edit(...)])` (a list) raises; an empty tuple raises; 101 edits raises; `Edit(old="")` raises; `ReadFileArgs(offset=-1)` raises; `BashArgs(timeout=601)` and `timeout=True` raise; `path` of `MAX_PATH_CHARS + 1` raises; `GrepArgs(glob=None)` constructs; `bytes` for any `str` field raises. Every raise is `FirewallInternalError`.
12. **Identity.** Same kind and path with different content hash equal; `insert_before` vs `insert_after` same path differ; `bash` commands differing by one flag differ; `read_file` with different `offset` hash equal; the hash of a fixed action equals a literal 64-hex string (pins `IDENTITY_VERSION` and serialization), where the plan computes that literal once on the host from the reviewed implementation and pastes it into the brief rather than asking the worker to derive it; changing `IDENTITY_VERSION` changes it.
13. **`FirewallEvent`.** `to_dict()` keys equal the literal field list; each value is a `str`, an `int`, `None`, or (for `capabilities`) a list of `str`; `capabilities` sorted; no key named `args`, `arguments`, `content`, `command` or `path`. `from_action` on an ALLOW policy gives `reason_code`, `reason_class` `None`; `from_rejection` for `tool_unknown` gives `stage="request"`, `kind=None`, `capabilities=[]`, `semantic_status=None`, and a `call_id_invalid` rejection gives `call_id=""`. Building a request-stage event with `decision=ALLOW`, or an action-stage event with `kind=None`, raises.
14. **Rejection identity.** Two `tool_unknown` rejections with the same raw name hash equal, with different names differ, and a `payload_too_large` rejection of the same name differs from both; the raw name is absent from the hex string and from `to_dict()`; a name of `"\ud800"` still yields a 64-hex identity and a request-stage event.
15. **Package registration.** Parse `pyproject.toml` (stdlib `tomllib` on 3.11+, else a line scan of the `packages = [...]` entry) and assert `"dirtywork.firewall"` is listed. The plan's host checklist additionally builds a wheel and runs `python -c "import dirtywork.firewall"` against it in a clean venv.
16. **Isolation.** `import dirtywork.firewall` succeeds with `sys.modules` free of `dirtywork.tools`, `dirtywork.builtin_tools`, `dirtywork.guardrails`, `dirtywork.runner` and `dirtywork.sandbox` (checked in a subprocess).

## 12. Decisions taken that Jim may want to override

1. **Package, not module.** `dirtywork/firewall/` with six small files rather than one `firewall.py`, so #136–#141 each add a file instead of growing one, and each worker brief is one file plus one test file.
2. **Unknown top-level keys are dropped and counted, not rejected.** Today's registry drops them and providers sometimes add extras; rejecting would change worker-visible behavior before #140's parity work. Nested unknown keys are rejected (also today's behavior).
3. **Identity excludes content.** Denial tracking keys on targets; contents would make every retry a "new" denial and defeat the Supervisor's exact-equivalent count.
4. **`HOST_FS` covers rules 4, 7 and 8 with three reason codes.** One capability for "outside the worktree", three codes so parity stays one-to-one with the ordered rules.
5. **`NETWORK` is reserved but unset.** Keeps the vocabulary complete against `Caps.network`; #137 decides the analyzer.
6. **`check_request` is tool-agnostic.** Per-field limits and typing wait for #136 where the tool shape is known, so #135 never grows a per-tool switch that #136 would rewrite.
7. **`MAX_COMMAND_CHARS` is 32,768, a new number, provisional.** Nothing bounds command input today. Recorded transcripts cap raw arguments at 2,000 characters, so no existing run can show how long real worker commands get; the number is chosen to be far above any heredoc seen in review, and issue #142's replay harness must record the command-length distribution before it is treated as final.
8. **Custom tools are denied as `tool_unknown` until a registration API exists.** The CLI ships no loader, so this changes nothing for users; embedders get an explicit later issue.
