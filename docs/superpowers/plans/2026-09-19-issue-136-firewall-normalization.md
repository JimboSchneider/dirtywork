# Issue #136: Firewall tool normalization and canonical action identity — implementation plan

> **For agentic workers:** this plan is executed by the released dirtywork (repository `CLAUDE.md` dogfood rule), one run per task, each from the brief quoted verbatim under that task. The orchestrator dry-runs every brief on a scratch clone first, so each brief's files are the reviewed reference and the review gate is a byte comparison. Claude plans, briefs and reviews; the worker writes the files.

**Goal:** land the per-tool normalization step from the spec as two new modules in `dirtywork/firewall/`, with tests that pin the pass to the registry's validation on a shared domain, and no runtime behavior change.

**Architecture:** `paths.py` (pure lexical path normalization with four target classes) and `normalize.py` (linear name recovery, a declarative per-kind field table, the ordered single-call pass, the batch pass), imports strictly downward onto the #135 modules. Three tasks, each one file plus one test file, each green before the next exists; the third also adds the package re-exports and the parity tests.

**Tech Stack:** Python >=3.9 (stdlib only: `enum`, `dataclasses`, `re`, `posixpath` is not used), pytest. No dependencies added.

**Spec:** [`docs/superpowers/specs/2026-09-19-issue-136-firewall-normalization-design.md`](../specs/2026-09-19-issue-136-firewall-normalization-design.md) (approved 2026-09-19 after two review rounds). Section numbers below refer to it.

## Global Constraints

- Repository `CLAUDE.md`: the latest released dirtywork plus a local worker implements code; Claude plans, briefs and reviews. PyPI checked 2026-09-19: `dirtywork==0.13.2`. Worker: `qwen3.6-35b-a3b-splash` (Inco AI Splash engine) served by LM Studio Bionic 1.1.5 on `http://localhost:1234/v1`; the model reasons by default and the released dirtywork cannot send `reasoning_effort` (issue #173), so every run passes `--max-tokens 16384`. Docker sandbox, image `dirtywork-worker-pytest:0.13`, network off.
- Python 3.9 compatible source: `from __future__ import annotations` first in every module and test; `class X(str, enum.Enum)`; no `StrEnum`, `match`, `kw_only`, `slots=True`.
- Package rules (spec §2): stdlib only; `dirtywork/firewall/paths.py` and `normalize.py` import nothing from `dirtywork/` outside the package; tests may import `dirtywork.builtin_tools` and `dirtywork.toolspec`; tests import from submodules, never the package root, until Task 3 adds the re-exports.
- No runtime behavior change: nothing outside the package imports it after this issue (spec §1, §13). The registry's `recover_name` and `_validate_args` are untouched.
- Every task's brief names its files; the worker touches only those. New files are written with `write_file`; the one existing-file change (`__init__.py`, Task 3) is an exact `edit_file` pair with its base line numbers. Brief blocks below are the reference files: re-extract them to compare a worker diff.
- Each task runs from `main` at the head the dry run used, after the previous task's PR has merged, or stacked with `--branch-from @<previous slug>` when the owner prefers to review the series together (then retarget each PR to `main` after the one below merges, close/reopen for CI). One PR per task, one ledger row per run under `docs/superpowers/bench/`, sampler on.
- No merge and no release without the owner's explicit per-action go.

## Invocation (every task)

```bash
S=<scratchpad>; tools/soak_sampler.sh $S/2026-09-XX-issue-136-w<N>-sampler.csv &
pipx run --spec 'dirtywork==0.13.2' dirtywork run "$(cat $S/brief-136-w<N>.txt)" \
  --repo /Users/jimschneider/repos/dirtywork [--branch-from @<previous slug>] \
  --model qwen3.6-35b-a3b-splash --max-tokens 16384 \
  --sandbox docker --image dirtywork-worker-pytest:0.13 \
  --verify "python3 -m pytest -q -p no:cacheprovider" --verify-rounds 2 \
  --max-turns 60 --timeout 1800
tools/soak_sampler.sh $S/2026-09-XX-issue-136-w<N>-sampler.csv --stop
```

The brief is passed as one argv element. Load the model in the same command as the run (`lms load qwen3.6-35b-a3b-splash -y`) because Bionic idle-unloads it. The sampler CSV is committed beside the ledger on the run branch.

## Review gates (every task)

- Worker diff matches the dry-run files byte for byte (the Splash workers have landed files byte-exact so far, with one extra blank line once; a trailing-newline or blank-line fix by the orchestrator is allowed and must be disclosed in the commit and the ledger).
- `files_changed` lists only the brief's files.
- Host: `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider` all green with the expected count; `git diff --check` clean; `python3 -c "import ast,sys; [ast.parse(open(f).read(), feature_version=(3,9)) for f in sys.argv[1:]]" dirtywork/firewall/*.py tests/test_firewall_*.py` silent; the import-isolation test green.
- Copy `diff.patch` and `orchestrator/` receipts out of the run dir before any `runs clean`.
- Ledger row: status, turns, wall, prompt/completion tokens, tok/s, nudges, verdict, tool-call counts, sampler summary, diff-vs-brief result.

---
### Task 1: paths: lexical normalization and target classes

**Files:**
- Create: `dirtywork/firewall/paths.py`
- Test: `tests/test_firewall_paths.py`

**Interfaces:**
- Consumes: nothing in the package (`bounds` is imported by the test only, for `MAX_PATH_CHARS` and `FIREWALL_SCHEMA_VERSION`); the identity tests build `CanonicalAction`s with `ReadFileArgs` from `schema.py`.
- Produces: `TargetClass(str, enum.Enum)` with `WORKSPACE`, `REPO_METADATA`, `PARENT_REF`, `OUTSIDE`; `NormalizedPath(path: str, target: TargetClass)` frozen; `normalize_path(raw: str) -> NormalizedPath`, total on `str`, filesystem-free (spec §6). Task 2 calls `normalize_path` on every `path` field and reads `.target`.

- [x] **Dry-run on the scratch clone** (2026-09-19, clone of `main` at `ec16d8b`): the files below written, `tests/test_firewall_paths.py` 37 passed, full host suite 1866 passed (baseline 1829), `ast.parse(..., feature_version=(3, 9))` silent, `git diff --check` clean. The symlink fixture (`link -> nested/child`) confirms the host resolves `link/../target.txt` to `nested/target.txt` while `posixpath.normpath` gives `target.txt`, and that `normalize_path` keeps the `..`.
- [ ] **Confirm the base.** `main` at `ec16d8b` or later with `dirtywork/firewall/` unchanged since; `git log --oneline -1 -- dirtywork/firewall` must show `cd6a202` or `1dafd0b`.
- [ ] **Launch** the brief below verbatim through the invocation in the header, sampler on, model loaded in the same command.
- [ ] **Review** against the gates in the header: `cmp` every produced file against the brief's block (re-extract with the round-trip script); `files_changed` is exactly the two files; host suite 1866 passed; `diff --check`; 3.9 grammar; the vocabulary pin and the symlink test present in the produced test file.
- [ ] **Ledger** `docs/superpowers/bench/2026-09-XX-issue-136-w1-paths-ledger.md` plus the sampler CSV, committed on the run branch.
- [ ] **PR** `dirtywork/<slug>` → `main` (or stacked), titled `feat(firewall): issue #136 W1 — lexical path normalization and target classes`, body naming the plan, the spec and the ledger; part 1 of 3 for issue #136 (does not close it).

### Worker brief W1

```text
Issue #136 task W1 of 3 (Worker Action Firewall C): add dirtywork/firewall/paths.py, the deterministic, filesystem-free path normalizer with its four-member TargetClass, plus its test. dirtywork/firewall/errors.py, bounds.py, reasons.py, capabilities.py, schema.py and request.py already exist from issue #135. Nothing outside the package imports it. Spec: docs/superpowers/specs/2026-09-19-issue-136-firewall-normalization-design.md sections 6, 10 and 12 (the files below are its exact content).

Touch ONLY dirtywork/firewall/paths.py, tests/test_firewall_paths.py. Create each NEW file with ONE write_file call whose content is exactly the text between its BEGIN and END marker lines below (byte for byte; keep every blank line; the file ends with a newline after its last line; the marker lines themselves are not part of the file). Use relative paths exactly as written (never an absolute /work/... path). No other files, no docs, no commits, nothing else.

FILE dirtywork/firewall/paths.py (new, 50 lines) — write_file with exactly:
=== BEGIN dirtywork/firewall/paths.py ===
"""Deterministic, filesystem-free path normalization and target
classification (spec §6)."""
from __future__ import annotations

import enum
from dataclasses import dataclass


class TargetClass(str, enum.Enum):
    """Where a normalized path lexically points, before any capability is
    assigned (spec §6)."""

    WORKSPACE = "workspace"
    REPO_METADATA = "repo_metadata"
    PARENT_REF = "parent_ref"
    OUTSIDE = "outside"


@dataclass(frozen=True)
class NormalizedPath:
    """A canonical path string and its target class (spec §6)."""

    path: str
    target: TargetClass


def normalize_path(raw: str) -> NormalizedPath:
    """Split on '/', drop empty and '.' components, keep every '..'
    component exactly where it is, and join with '/' (spec §6). Pure string
    work: never touches the filesystem, never collapses '..' (a symlink can
    make that change the execution target), never case-folds, never expands
    '~', never converts backslashes. Total on `str`: never raises."""
    is_absolute = raw.startswith("/")
    kept = [part for part in raw.split("/") if part not in ("", ".")]
    joined = "/".join(kept)
    if is_absolute:
        path = "/" + joined if joined else "/"
    else:
        path = joined if joined else "."
    return NormalizedPath(path=path, target=_target_class(kept, is_absolute))


def _target_class(kept: list, is_absolute: bool) -> TargetClass:
    if is_absolute or (kept and kept[0] == ".."):
        return TargetClass.OUTSIDE
    if kept and kept[0] == ".git" and ".." not in kept:
        return TargetClass.REPO_METADATA
    if ".." in kept:
        return TargetClass.PARENT_REF
    return TargetClass.WORKSPACE
=== END dirtywork/firewall/paths.py ===

FILE tests/test_firewall_paths.py (new, 149 lines) — write_file with exactly:
=== BEGIN tests/test_firewall_paths.py ===
from __future__ import annotations

import os
import posixpath

import pytest

from dirtywork.firewall import bounds
from dirtywork.firewall.capabilities import ActionKind, Capability
from dirtywork.firewall.paths import NormalizedPath, TargetClass, normalize_path
from dirtywork.firewall.schema import (
    CanonicalAction,
    ReadFileArgs,
    SemanticStatus,
    action_identity,
)

W = TargetClass.WORKSPACE
R = TargetClass.REPO_METADATA
P = TargetClass.PARENT_REF
O = TargetClass.OUTSIDE

NORMALIZATION_TABLE = [
    ("", ".", W),
    (".", ".", W),
    ("src/x.py/", "src/x.py", W),
    ("src//x.py", "src/x.py", W),
    ("./x", "x", W),
    ("src/./x.py", "src/x.py", W),
    ("src/x.py/.", "src/x.py", W),
    ("../x", "../x", O),
    ("src/a/../x.py", "src/a/../x.py", P),
    ("src/x.py/..", "src/x.py/..", P),
    ("/etc/passwd", "/etc/passwd", O),
    ("/work/../etc/passwd", "/work/../etc/passwd", O),
    ("//x", "/x", O),
    ("/", "/", O),
    (".git/config", ".git/config", R),
    ("src/.git/x", "src/.git/x", W),
    (".gitignore", ".gitignore", W),
    (".git/../x", ".git/../x", P),
    ("~/x", "~/x", W),
    ("a\\b", "a\\b", W),
    ("src/ünïcode.py", "src/ünïcode.py", W),
    ("./../x", "../x", O),
    ("/../etc", "/../etc", O),
]


@pytest.mark.parametrize("raw,expected_path,expected_target", NORMALIZATION_TABLE)
def test_normalize_path_table(raw, expected_path, expected_target):
    result = normalize_path(raw)
    assert result == NormalizedPath(path=expected_path, target=expected_target)


def test_normalize_path_exact_max_path_chars():
    raw = "a" * bounds.MAX_PATH_CHARS
    result = normalize_path(raw)
    assert result.path == raw
    assert result.target == W


def test_symlink_realpath_diverges_from_lexical_normalization(tmp_path):
    nested = tmp_path / "nested"
    child = nested / "child"
    child.mkdir(parents=True)
    (nested / "target.txt").write_text("nested")
    (tmp_path / "target.txt").write_text("top")
    link = tmp_path / "link"
    try:
        os.symlink(child, link)
    except OSError:
        pytest.skip("symlinks not supported (e.g. Windows CI without privileges)")

    real = os.path.realpath(tmp_path / "link/../target.txt")
    assert real.endswith(os.path.join("nested", "target.txt"))

    lexical = posixpath.normpath("link/../target.txt")
    assert lexical == "target.txt"

    result = normalize_path("link/../target.txt")
    assert result.path == "link/../target.txt"
    assert result.target == P


def _read_action(raw_path):
    normalized = normalize_path(raw_path)
    return CanonicalAction(
        schema_version=bounds.FIREWALL_SCHEMA_VERSION,
        call_id="call_1",
        turn=1,
        kind=ActionKind.READ_FILE,
        args=ReadFileArgs(path=normalized.path, offset=0, limit=400),
        capabilities=frozenset({Capability.WORKSPACE_READ}),
        semantic_status=SemanticStatus.KNOWN,
    )


def _identity_for(raw_path):
    return action_identity(_read_action(raw_path))


@pytest.mark.parametrize(
    "group",
    [
        ["src/x.py", "./src/x.py", "src/./x.py", "src//x.py", "src/x.py/"],
        ["../x", "./../x"],
    ],
)
def test_identity_equivalence_through_normalization(group):
    identities = {_identity_for(raw) for raw in group}
    assert len(identities) == 1


@pytest.mark.parametrize(
    "a,b",
    [
        ("src/x.py", "src/y.py"),
        ("src/x.py", "src/a/../x.py"),
        ("../x", "x"),
        ("link/../x", "x"),
    ],
)
def test_identity_distinctness_through_normalization(a, b):
    assert _identity_for(a) != _identity_for(b)


@pytest.mark.parametrize(
    "raw",
    [
        "//",
        "...",
        "/..",
        "a/../../..",
        "../" * 10_000,
    ],
)
def test_normalize_path_is_total(raw):
    result = normalize_path(raw)
    assert isinstance(result, NormalizedPath)


def test_target_class_vocabulary_pin():
    assert sorted(t.value for t in TargetClass) == [
        "outside",
        "parent_ref",
        "repo_metadata",
        "workspace",
    ]
=== END tests/test_firewall_paths.py ===

VERIFY: run python3 -m pytest -q -p no:cacheprovider tests/test_firewall_paths.py and expect 37 passed. Then run python3 -m pytest -q -p no:cacheprovider with timeout=300 and expect all passed, 0 failed (1866 passed). Finish when both pass.
```

---
### Task 2: normalize: name recovery, the field table and the single-call pass

**Files:**
- Create: `dirtywork/firewall/normalize.py`
- Test: `tests/test_firewall_normalize.py`

**Interfaces:**
- Consumes: `normalize_path`, `TargetClass` (Task 1); `check_request`, `Rejection` (`request.py`); `ActionRequest`, `CanonicalAction`, `Edit`, `SemanticStatus`, `ARGS_FOR_KIND` (`schema.py`); `ActionKind`, `Capability`, `BASE_CAPABILITIES` (`capabilities.py`); `ReasonCode`; the bounds. The tests import `dirtywork.toolspec` and `dirtywork.builtin_tools` for the recovery-equivalence, marker-tuple and field-table cross-checks.
- Produces: `TOOL_CALL_MARKERS` (tuple, copied by value); `WRITE_KINDS` (frozenset of six `ActionKind`s); `Field(name, kind, required, default, limit, lo, hi)` frozen; `FIELD_TABLE: dict[ActionKind, tuple[Field, ...]]`; `Normalization(action, rejection, dropped_keys)` frozen with its invariants; `recover_name(name) -> (name, marker | None, cut)`; `canonicalize(request: ActionRequest) -> Normalization`. Task 3 appends `canonicalize_batch` after the last line of this file and re-exports six of these names plus it.

- [x] **Dry-run on the scratch clone** (2026-09-19, on top of Task 1): the files below written, `tests/test_firewall_normalize.py` 219 passed, full host suite 2085 passed, `ast.parse(..., feature_version=(3, 9))` silent, `git diff --check` clean. Recovery equivalence proven against `ToolRegistry.recover_name` on 129 fixture names plus two 33K-character pathological strings; a 1.1 MB marker-only name rejects as `tool_name_invalid` well under a second. Review of PR #175 added the 32-character bound on numeric strings before `int()` (spec §4, decision 11) with seven tests, including a one-million-digit `offset` and `timeout` rejected in well under half a second. Tie-break recorded: an `edits` item that both lacks `new` and carries an extra key reports `argument_type_invalid`, because the spec's step 6 bullets are checked in the order written.
- [ ] **Confirm the base.** Task 1's PR merged (or its run branch as `--branch-from`); `dirtywork/firewall/paths.py` present at the brief's content.
- [ ] **Launch** the brief below verbatim through the invocation in the header, sampler on, model loaded in the same command. The brief is the largest of the three (about 39 KB, two writes of about 16 KB and 23 KB); if either file lands truncated, rerun fresh rather than resume.
- [ ] **Review** against the gates in the header: `cmp` both files against the brief's blocks; `files_changed` is exactly the two files; host suite 2085 passed; `diff --check`; 3.9 grammar; the import-isolation test and the field-table cross-check green in the produced test file.
- [ ] **Ledger** `docs/superpowers/bench/2026-09-XX-issue-136-w2-normalize-ledger.md` plus the sampler CSV, committed on the run branch.
- [ ] **PR** titled `feat(firewall): issue #136 W2 — name recovery, the field table and the single-call pass`, body naming the plan, the spec and the ledger; part 2 of 3 for issue #136 (does not close it).

### Worker brief W2

```text
Issue #136 task W2 of 3 (Worker Action Firewall C): add dirtywork/firewall/normalize.py, the per-tool normalization pass (TOOL_CALL_MARKERS, linear recover_name, the Field table FIELD_TABLE, Normalization, canonicalize) plus its test. dirtywork/firewall/paths.py exists from task W1; errors.py, bounds.py, reasons.py, capabilities.py, schema.py and request.py exist from issue #135. Nothing outside the package imports it. canonicalize_batch and the package re-exports are task W3, not this one. Spec: docs/superpowers/specs/2026-09-19-issue-136-firewall-normalization-design.md sections 3, 4, 5, 7, 8, 10 and 12 (the files below are its exact content).

Touch ONLY dirtywork/firewall/normalize.py, tests/test_firewall_normalize.py. Create each NEW file with ONE write_file call whose content is exactly the text between its BEGIN and END marker lines below (byte for byte; keep every blank line; the file ends with a newline after its last line; the marker lines themselves are not part of the file). Use relative paths exactly as written (never an absolute /work/... path). Do not edit dirtywork/firewall/__init__.py. No other files, no docs, no commits, nothing else.

FILE dirtywork/firewall/normalize.py (new, 388 lines) — write_file with exactly:
=== BEGIN dirtywork/firewall/normalize.py ===
"""Per-tool canonicalization: ActionRequest to CanonicalAction, or to a
Rejection carrying one of #135's codes (spec §3, §4, §5, §7, §8)."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Optional

from .bounds import (
    FIREWALL_SCHEMA_VERSION,
    MAX_BASH_TIMEOUT,
    MAX_COMMAND_CHARS,
    MAX_GLOB_CHARS,
    MAX_INT,
    MAX_PATH_CHARS,
    MAX_PATTERN_CHARS,
    MAX_STRING_CHARS,
    MAX_SUMMARY_CHARS,
)
from .capabilities import ActionKind, BASE_CAPABILITIES, Capability
from .errors import FirewallInternalError
from .paths import TargetClass, normalize_path
from .reasons import ReasonCode
from .request import Rejection, check_request
from .schema import ARGS_FOR_KIND, ActionRequest, CanonicalAction, Edit, SemanticStatus

# Copied by value from dirtywork.toolspec (spec §3): these tags are built by
# concatenation ON PURPOSE, so a worker model editing this file through its
# own tool channel could not emit them literally.
_RAW_MARKERS = ("[" + "TOOL_CALLS]",) + tuple(
    "<" + m for m in ("tool_call>", "function=", "function_call>", "|tool_call|>")
)
TOOL_CALL_MARKERS = _RAW_MARKERS + tuple(
    re.sub(r"[^A-Za-z0-9_-]", "_", m) for m in _RAW_MARKERS
)

WRITE_KINDS = frozenset(
    {
        ActionKind.WRITE_FILE,
        ActionKind.APPEND_FILE,
        ActionKind.EDIT_FILE,
        ActionKind.APPLY_EDITS,
        ActionKind.INSERT_BEFORE,
        ActionKind.INSERT_AFTER,
    }
)


@dataclass(frozen=True)
class Field:
    """One parameter of one ActionKind, in the registry's own order (spec
    §4). `limit` bounds `str`/`path`/`command` characters after coercion;
    `lo`/`hi` bound `int` after coercion. Neither applies to `duration`
    (clamped, never rejected) or `edits` (its own rules, spec §5 step 6)."""

    name: str
    kind: str  # "str" | "int" | "duration" | "path" | "command" | "edits"
    required: bool
    default: Any  # ignored when required; may itself be None (grep.glob)
    limit: Optional[int]
    lo: Optional[int]
    hi: Optional[int]


FIELD_TABLE: "dict[ActionKind, tuple[Field, ...]]" = {
    ActionKind.READ_FILE: (
        Field("path", "path", True, None, MAX_PATH_CHARS, None, None),
        Field("offset", "int", False, 0, None, 0, MAX_INT),
        Field("limit", "int", False, 400, None, 1, MAX_INT),
    ),
    ActionKind.WRITE_FILE: (
        Field("path", "path", True, None, MAX_PATH_CHARS, None, None),
        Field("content", "str", True, None, MAX_STRING_CHARS, None, None),
    ),
    ActionKind.APPEND_FILE: (
        Field("path", "path", True, None, MAX_PATH_CHARS, None, None),
        Field("text", "str", True, None, MAX_STRING_CHARS, None, None),
    ),
    ActionKind.EDIT_FILE: (
        Field("path", "path", True, None, MAX_PATH_CHARS, None, None),
        Field("old_string", "str", True, None, MAX_STRING_CHARS, None, None),
        Field("new_string", "str", True, None, MAX_STRING_CHARS, None, None),
    ),
    ActionKind.APPLY_EDITS: (
        Field("path", "path", True, None, MAX_PATH_CHARS, None, None),
        Field("edits", "edits", True, None, None, None, None),
    ),
    ActionKind.INSERT_BEFORE: (
        Field("path", "path", True, None, MAX_PATH_CHARS, None, None),
        Field("anchor", "str", True, None, MAX_STRING_CHARS, None, None),
        Field("text", "str", True, None, MAX_STRING_CHARS, None, None),
    ),
    ActionKind.INSERT_AFTER: (
        Field("path", "path", True, None, MAX_PATH_CHARS, None, None),
        Field("anchor", "str", True, None, MAX_STRING_CHARS, None, None),
        Field("text", "str", True, None, MAX_STRING_CHARS, None, None),
    ),
    ActionKind.LIST_DIR: (
        Field("path", "path", False, ".", MAX_PATH_CHARS, None, None),
    ),
    ActionKind.GREP: (
        Field("pattern", "str", True, None, MAX_PATTERN_CHARS, None, None),
        Field("path", "path", False, ".", MAX_PATH_CHARS, None, None),
        Field("glob", "str", False, None, MAX_GLOB_CHARS, None, None),
    ),
    ActionKind.BASH: (
        Field("command", "command", True, None, MAX_COMMAND_CHARS, None, None),
        Field("timeout", "duration", False, 120, None, None, None),
    ),
    ActionKind.FINISH: (
        # Declared exception (spec §4): the registry requires `summary` with
        # no default; the Runner already canonicalizes a missing one to "".
        Field("summary", "str", False, "", MAX_SUMMARY_CHARS, None, None),
    ),
}


@dataclass(frozen=True)
class Normalization:
    """The result of one `canonicalize` call: exactly one of `action` and
    `rejection` is set (spec §2)."""

    action: Optional[CanonicalAction]
    rejection: Optional[Rejection]
    dropped_keys: int

    def __post_init__(self) -> None:
        action_set = self.action is not None
        rejection_set = self.rejection is not None
        if action_set == rejection_set:
            raise FirewallInternalError(
                "Normalization requires exactly one of action and rejection"
            )
        if isinstance(self.dropped_keys, bool) or not isinstance(self.dropped_keys, int):
            raise FirewallInternalError("dropped_keys must be an int")
        if self.dropped_keys < 0:
            raise FirewallInternalError("dropped_keys must be non-negative")
        if rejection_set and self.dropped_keys != 0:
            raise FirewallInternalError("dropped_keys must be 0 on a rejection")


_ACTION_KIND_VALUES = tuple(kind.value for kind in ActionKind)
_MARKERS_LONGEST_FIRST = tuple(sorted(TOOL_CALL_MARKERS, key=len, reverse=True))


def recover_name(name: str) -> tuple:
    """(name, marker, cut): the same answer as the registry's own
    `ToolRegistry.recover_name`, computed by a linear, end-anchored
    algorithm instead of the registry's quadratic marker search (spec §3).

    A name that already is an ActionKind value is returned as-is. Otherwise
    the trailing whitespace is stripped; the remaining tail must end in some
    ActionKind value `k`; walking back over any whitespace before `k` finds
    the position a marker must end at, checked longest-first. No two
    ActionKind values can both be a suffix of the tail (none is a suffix of
    another), so `k` is unique when it exists.
    """
    if name in _ACTION_KIND_VALUES:
        return name, None, 0
    tail = name.rstrip()
    matched_kind = None
    for kind_value in _ACTION_KIND_VALUES:
        if tail.endswith(kind_value):
            matched_kind = kind_value
            break
    if matched_kind is None:
        return name, None, 0
    marker_end = len(tail) - len(matched_kind)
    while marker_end > 0 and tail[marker_end - 1].isspace():
        marker_end -= 1
    for marker in _MARKERS_LONGEST_FIRST:
        start = marker_end - len(marker)
        if start >= 0 and tail[start:marker_end] == marker:
            return matched_kind, marker, start
    return name, None, 0


# Copied by value from dirtywork.toolspec._DURATION_REGEX / _coerce_duration
# (spec §4): digits, optional whitespace, a seconds or minutes unit.
_DURATION_REGEX = re.compile(
    r"^\s*(\d{1,9})\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes)\s*$",
    re.IGNORECASE | re.ASCII,
)


def _coerce_duration(value: Any) -> Optional[int]:
    """Seconds as an int, or None: a bool is never accepted; an int (not
    bool) passes through; a string is tried as a plain int first, then
    against `_DURATION_REGEX`, minutes multiplied by 60."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
        match = _DURATION_REGEX.match(value)
        if match:
            num = int(match.group(1))
            unit = match.group(2).lower()
            if unit in ("s", "sec", "secs", "second", "seconds"):
                return num
            if unit in ("m", "min", "mins", "minute", "minutes"):
                return num * 60
        return None
    return None


# An int in [MIN_INT, MAX_INT] needs at most 11 characters; the allowance
# covers the sign, whitespace and underscores that int() accepts. A longer
# numeric string cannot be in range and is rejected before int() runs, which
# is quadratic on Python 3.9 and raises past 4,300 digits on 3.11+ (spec §5).
_MAX_NUMERIC_CHARS = 32


def _clamp_timeout(value: int) -> int:
    return max(1, min(value, MAX_BASH_TIMEOUT))


def _missing(name: str) -> Rejection:
    return Rejection(ReasonCode.ARGUMENT_MISSING, f"missing required argument '{name}'")


def _type_invalid(name: str) -> Rejection:
    return Rejection(ReasonCode.ARGUMENT_TYPE_INVALID, f"argument '{name}' has an invalid type")


def _unexpected(location: str) -> Rejection:
    return Rejection(ReasonCode.ARGUMENT_UNEXPECTED, f"unexpected key in {location}")


def _too_long(name: str, limit: int) -> Rejection:
    return Rejection(ReasonCode.STRING_TOO_LONG, f"argument '{name}' exceeds {limit} chars")


def _out_of_range(name: str, lo: int, hi: int) -> Rejection:
    return Rejection(ReasonCode.NUMBER_OUT_OF_RANGE, f"argument '{name}' must be in [{lo}, {hi}]")


def _coerce_scalar(field: Field, raw: Any) -> "tuple[Any, Optional[Rejection]]":
    """Coerces and bounds one `str`/`path`/`command`/`int`/`duration` field,
    immediately after coercion (spec §5 steps 5 and 6). Returns
    `(value, None)` or `(None, rejection)`."""
    kind = field.kind
    if kind in ("str", "path", "command"):
        if not isinstance(raw, str):
            return None, _type_invalid(field.name)
        if len(raw) > field.limit:
            return None, _too_long(field.name, field.limit)
        return raw, None
    if kind == "int":
        if isinstance(raw, bool):
            return None, _type_invalid(field.name)
        if isinstance(raw, int):
            value = raw
        elif isinstance(raw, str):
            if len(raw) > _MAX_NUMERIC_CHARS:
                return None, _too_long(field.name, _MAX_NUMERIC_CHARS)
            try:
                value = int(raw)
            except ValueError:
                return None, _type_invalid(field.name)
        else:
            return None, _type_invalid(field.name)
        if not (field.lo <= value <= field.hi):
            return None, _out_of_range(field.name, field.lo, field.hi)
        return value, None
    if kind == "duration":
        if isinstance(raw, str) and len(raw) > _MAX_NUMERIC_CHARS:
            return None, _too_long(field.name, _MAX_NUMERIC_CHARS)
        value = _coerce_duration(raw)
        if value is None:
            return None, _type_invalid(field.name)
        return _clamp_timeout(value), None
    raise FirewallInternalError(f"unhandled field kind {kind!r}")


def _coerce_edits(raw: Any) -> "tuple[Any, Optional[Rejection]]":
    """The `edits` value-kind rule and bound in one pass (spec §5 step 6): a
    non-empty list of objects with exactly `old` and `new`, both `str`,
    `old` nonempty. An unknown key is reported by index only, never by
    name."""
    if not isinstance(raw, list) or not raw:
        return None, _type_invalid("edits")
    items = []
    for index, item in enumerate(raw):
        location = f"edits[{index}]"
        if not isinstance(item, dict):
            return None, _type_invalid(location)
        if "old" not in item or "new" not in item:
            missing_field = "old" if "old" not in item else "new"
            return None, _type_invalid(f"{location}.{missing_field}")
        if set(item) - {"old", "new"}:
            return None, _unexpected(location)
        old, new = item["old"], item["new"]
        if not isinstance(old, str) or not old:
            return None, _type_invalid(f"{location}.old")
        if not isinstance(new, str):
            return None, _type_invalid(f"{location}.new")
        if len(old) > MAX_STRING_CHARS:
            return None, _too_long(f"{location}.old", MAX_STRING_CHARS)
        if len(new) > MAX_STRING_CHARS:
            return None, _too_long(f"{location}.new", MAX_STRING_CHARS)
        items.append(Edit(old=old, new=new))
    return tuple(items), None


def _process_fields(fields: "tuple[Field, ...]", arguments: dict) -> "tuple[Any, Optional[Rejection]]":
    """Steps 3, 5 and 6 (spec §5): every required field must be present
    before any field is coerced, so a type problem on one required field
    never hides a missing later one; then each field, in table order, gets
    its default/null handling, its value-kind coercion and its bound."""
    for field in fields:
        if field.required and field.name not in arguments:
            return None, _missing(field.name)
    values = {}
    for field in fields:
        if field.name not in arguments:
            values[field.name] = field.default
            continue
        raw = arguments[field.name]
        if raw is None:
            if field.required:
                return None, _type_invalid(field.name)
            values[field.name] = field.default
            continue
        if field.kind == "edits":
            value, rejection = _coerce_edits(raw)
        else:
            value, rejection = _coerce_scalar(field, raw)
        if rejection is not None:
            return None, rejection
        values[field.name] = value
    return values, None


def canonicalize(request: ActionRequest) -> Normalization:
    """The one entry point from a validated-or-not `ActionRequest` to a
    `Normalization` (spec §5). Runs name recovery and `check_request`
    itself, then the field table pass, then builds capabilities and the
    `CanonicalAction`. Never catches `FirewallInternalError`."""
    if isinstance(request.tool_name, str):
        recovered, _marker, _cut = recover_name(request.tool_name)
        request = replace(request, tool_name=recovered)

    rejection = check_request(request)
    if rejection is not None:
        return Normalization(action=None, rejection=rejection, dropped_keys=0)

    kind = ActionKind(request.tool_name)
    arguments = request.arguments
    fields = FIELD_TABLE[kind]
    known_names = {field.name for field in fields}
    dropped_keys = sum(1 for key in arguments if key not in known_names)

    values, rejection = _process_fields(fields, arguments)
    if rejection is not None:
        return Normalization(action=None, rejection=rejection, dropped_keys=0)

    path_targets = []
    for field in fields:
        if field.kind == "path":
            normalized = normalize_path(values[field.name])
            values[field.name] = normalized.path
            path_targets.append(normalized.target)

    args = ARGS_FOR_KIND[kind](**values)

    capabilities = set(BASE_CAPABILITIES[kind])
    if TargetClass.OUTSIDE in path_targets:
        capabilities.add(Capability.HOST_FS)
    if TargetClass.REPO_METADATA in path_targets and kind in WRITE_KINDS:
        capabilities.add(Capability.REPO_CONTROL)

    semantic_status = SemanticStatus.UNKNOWN if kind is ActionKind.BASH else SemanticStatus.KNOWN

    action = CanonicalAction(
        schema_version=FIREWALL_SCHEMA_VERSION,
        call_id=request.call_id,
        turn=request.turn,
        kind=kind,
        args=args,
        capabilities=frozenset(capabilities),
        semantic_status=semantic_status,
    )
    return Normalization(action=action, rejection=None, dropped_keys=dropped_keys)
=== END dirtywork/firewall/normalize.py ===

FILE tests/test_firewall_normalize.py (new, 599 lines) — write_file with exactly:
=== BEGIN tests/test_firewall_normalize.py ===
from __future__ import annotations

import ast
import json
import pathlib
import time

import pytest

from dirtywork import builtin_tools, toolspec
from dirtywork.firewall import bounds
from dirtywork.firewall.capabilities import ActionKind, BASE_CAPABILITIES, Capability
from dirtywork.firewall.errors import FirewallInternalError
from dirtywork.firewall.normalize import (
    FIELD_TABLE,
    Normalization,
    TOOL_CALL_MARKERS,
    canonicalize,
    recover_name,
)
from dirtywork.firewall.reasons import ReasonCode
from dirtywork.firewall.schema import (
    ActionRequest,
    AppendFileArgs,
    ApplyEditsArgs,
    BashArgs,
    Edit,
    EditFileArgs,
    FinishArgs,
    GrepArgs,
    InsertArgs,
    ListDirArgs,
    ReadFileArgs,
    SemanticStatus,
    WriteFileArgs,
    action_identity,
)


def _req(tool, arguments, call_id="call_1", turn=1, batch_index=0, batch_size=1):
    return ActionRequest(
        call_id=call_id,
        tool_name=tool,
        arguments=arguments,
        parse_error=None,
        raw_chars=len(json.dumps(arguments)) if arguments is not None else 0,
        turn=turn,
        batch_index=batch_index,
        batch_size=batch_size,
    )


_REGISTRY = builtin_tools.default_registry()
_KIND_VALUES = [kind.value for kind in ActionKind]


# --- group 1/2 (recover_name and TOOL_CALL_MARKERS parity) ------------------

_RECOVER_NAME_FIXTURE = list(_KIND_VALUES)
for _marker in TOOL_CALL_MARKERS:
    for _name in _KIND_VALUES:
        _RECOVER_NAME_FIXTURE.append(_marker + _name)
_RECOVER_NAME_FIXTURE.extend(
    [
        "<tool_call> bash ",
        "<tool_call>\tbash",
        "<tool_call><tool_call>bash",
        "[TOOL_CALLS]<function=grep",
        "<tool_call>nope",
        "foobash",
        "call bash",
        "",
    ]
)


def _fixture_id(name):
    return f"len={len(name)}" if len(name) > 60 else repr(name)


@pytest.mark.parametrize("name", _RECOVER_NAME_FIXTURE, ids=_fixture_id)
def test_recover_name_matches_registry(name):
    assert recover_name(name) == _REGISTRY.recover_name(name)


@pytest.mark.parametrize(
    "name",
    ["<tool_call>" * 3000 + "nope", "<tool_call>" * 3000 + "bash"],
    ids=["repeated_marker_no_tail", "repeated_marker_bash_tail"],
)
def test_recover_name_matches_registry_pathological(name):
    assert recover_name(name) == _REGISTRY.recover_name(name)


def test_tool_call_markers_equals_toolspec():
    assert TOOL_CALL_MARKERS == toolspec.TOOL_CALL_MARKERS


# --- group 3 (performance on a pathological name) ---------------------------


def test_giant_marker_only_name_is_fast_and_tool_name_invalid():
    name = "<tool_call>" * 100_000
    assert len(name) > 1024 * 1024
    request = _req(name, {})
    t0 = time.perf_counter()
    result = canonicalize(request)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.TOOL_NAME_INVALID


# --- group 4 (non-string tool_name) ------------------------------------------


@pytest.mark.parametrize("bad_name", [None, ["bash"]])
def test_non_string_tool_name_is_tool_name_invalid(bad_name):
    result = canonicalize(_req(bad_name, {"command": "ls"}))
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.TOOL_NAME_INVALID


# --- group 5 (per-kind minimal accepted call) --------------------------------

_PER_KIND_MINIMAL = [
    (ActionKind.READ_FILE, {"path": "a"}, ReadFileArgs(path="a", offset=0, limit=400)),
    (ActionKind.WRITE_FILE, {"path": "a", "content": "c"}, WriteFileArgs(path="a", content="c")),
    (ActionKind.APPEND_FILE, {"path": "a", "text": "t"}, AppendFileArgs(path="a", text="t")),
    (
        ActionKind.EDIT_FILE,
        {"path": "a", "old_string": "o", "new_string": "n"},
        EditFileArgs(path="a", old_string="o", new_string="n"),
    ),
    (
        ActionKind.APPLY_EDITS,
        {"path": "a", "edits": [{"old": "o", "new": "n"}]},
        ApplyEditsArgs(path="a", edits=(Edit(old="o", new="n"),)),
    ),
    (
        ActionKind.INSERT_BEFORE,
        {"path": "a", "anchor": "x", "text": "t"},
        InsertArgs(path="a", anchor="x", text="t"),
    ),
    (
        ActionKind.INSERT_AFTER,
        {"path": "a", "anchor": "x", "text": "t"},
        InsertArgs(path="a", anchor="x", text="t"),
    ),
    (ActionKind.LIST_DIR, {}, ListDirArgs(path=".")),
    (ActionKind.GREP, {"pattern": "p"}, GrepArgs(pattern="p", path=".", glob=None)),
    (ActionKind.BASH, {"command": "ls"}, BashArgs(command="ls", timeout=120)),
    (ActionKind.FINISH, {}, FinishArgs(summary="")),
]


@pytest.mark.parametrize("kind,arguments,expected_args", _PER_KIND_MINIMAL, ids=[k.value for k, _, _ in _PER_KIND_MINIMAL])
def test_minimal_call_per_kind(kind, arguments, expected_args):
    result = canonicalize(_req(kind.value, arguments))
    assert result.rejection is None
    action = result.action
    assert action.kind is kind
    assert type(action.args) is type(expected_args)
    assert action.args == expected_args
    assert action.capabilities == BASE_CAPABILITIES[kind]
    expected_status = SemanticStatus.UNKNOWN if kind is ActionKind.BASH else SemanticStatus.KNOWN
    assert action.semantic_status is expected_status
    assert result.dropped_keys == 0


# --- group 6 (every rejection code the pass can emit, once each) ------------


def test_argument_missing_names_field():
    result = canonicalize(_req("write_file", {}))
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_MISSING
    assert "path" in result.rejection.detail


def test_argument_type_invalid_names_field_and_excludes_value():
    result = canonicalize(_req("read_file", {"path": 12345}))
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID
    assert "path" in result.rejection.detail
    assert "12345" not in result.rejection.detail


def test_argument_unexpected_names_index_not_key():
    result = canonicalize(
        _req("apply_edits", {"path": "a", "edits": [{"old": "o", "new": "n", "sneaky": 1}]})
    )
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_UNEXPECTED
    assert "edits[0]" in result.rejection.detail
    assert "sneaky" not in result.rejection.detail


def test_string_too_long_names_field_and_limit():
    result = canonicalize(_req("read_file", {"path": "a" * (bounds.MAX_PATH_CHARS + 1)}))
    assert result.rejection.reason_code is ReasonCode.STRING_TOO_LONG
    assert "path" in result.rejection.detail
    assert str(bounds.MAX_PATH_CHARS) in result.rejection.detail


def test_number_out_of_range_names_field():
    result = canonicalize(_req("read_file", {"path": "a", "limit": "0"}))
    assert result.rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE
    assert "limit" in result.rejection.detail


def test_check_request_codes_pass_through():
    assert canonicalize(_req("not_a_tool", {})).rejection.reason_code is ReasonCode.TOOL_UNKNOWN
    result = canonicalize(_req("bash", "not a dict"))
    assert result.rejection.reason_code is ReasonCode.ARGUMENTS_NOT_OBJECT
    result = canonicalize(_req("bash", {"command": "ls"}, call_id=""))
    assert result.rejection.reason_code is ReasonCode.CALL_ID_INVALID


# --- group 7 (step order) ----------------------------------------------------


def test_unknown_key_beside_missing_required_reports_missing():
    result = canonicalize(_req("write_file", {"path": "a", "bogus": 1}))
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_MISSING
    assert "content" in result.rejection.detail


def test_null_required_field_is_type_invalid_not_missing():
    result = canonicalize(_req("write_file", {"path": "a", "content": None}))
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_first_field_string_too_long_beats_second_field_type_invalid():
    result = canonicalize(
        _req(
            "edit_file",
            {
                "path": "a" * (bounds.MAX_PATH_CHARS + 1),
                "old_string": 5,
                "new_string": "n",
            },
        )
    )
    assert result.rejection.reason_code is ReasonCode.STRING_TOO_LONG


def test_check_request_rejection_beats_field_pass():
    result = canonicalize(_req("write_file", {"path": "a"}, batch_size=33))
    assert result.rejection.reason_code is ReasonCode.BATCH_TOO_LARGE


# --- group 8 (dropped_keys) --------------------------------------------------


def test_dropped_keys_counts_two_unknown():
    result = canonicalize(_req("write_file", {"path": "a", "content": "c", "x": 1, "y": 2}))
    assert result.rejection is None
    assert result.dropped_keys == 2


def test_dropped_keys_grep_timeout_is_ignored():
    result = canonicalize(_req("grep", {"pattern": "p", "timeout": 5}))
    assert result.rejection is None
    assert result.dropped_keys == 1
    assert not hasattr(result.action.args, "timeout")


def test_dropped_keys_counts_null_valued_unknown_key():
    result = canonicalize(_req("finish", {"extra": None}))
    assert result.rejection is None
    assert result.dropped_keys == 1


# --- group 9 (apply_edits nested rules) --------------------------------------

_APPLY_EDITS_CASES = [
    (["oops"], ReasonCode.ARGUMENT_TYPE_INVALID, "edits[0]"),
    ([{"old": "a"}], ReasonCode.ARGUMENT_TYPE_INVALID, "edits[0].new"),
    ([{"old": 5, "new": "b"}], ReasonCode.ARGUMENT_TYPE_INVALID, "edits[0].old"),
    ([{"old": "", "new": "b"}], ReasonCode.ARGUMENT_TYPE_INVALID, "edits[0].old"),
    ([], ReasonCode.ARGUMENT_TYPE_INVALID, "edits"),
]


@pytest.mark.parametrize("edits,code,needle", _APPLY_EDITS_CASES)
def test_apply_edits_nested_rules(edits, code, needle):
    result = canonicalize(_req("apply_edits", {"path": "a", "edits": edits}))
    assert result.rejection is not None
    assert result.rejection.reason_code is code
    assert needle in result.rejection.detail


def test_apply_edits_extra_key_is_unexpected_by_index():
    result = canonicalize(
        _req(
            "apply_edits",
            {
                "path": "a",
                "edits": [{"old": "a", "new": "b"}, {"old": "a", "new": "b", "sneaky": "x"}],
            },
        )
    )
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_UNEXPECTED
    assert "edits[1]" in result.rejection.detail
    assert "sneaky" not in result.rejection.detail


def test_apply_edits_item_string_over_max_chars():
    huge = "x" * (bounds.MAX_STRING_CHARS + 1)
    result = canonicalize(_req("apply_edits", {"path": "a", "edits": [{"old": huge, "new": "b"}]}))
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.STRING_TOO_LONG


# --- group 10 (coercion table, both directions) ------------------------------


def test_offset_numeric_string_coerces():
    result = canonicalize(_req("read_file", {"path": "a", "offset": "5"}))
    assert result.rejection is None
    assert result.action.args.offset == 5


def test_offset_decimal_string_rejected():
    result = canonicalize(_req("read_file", {"path": "a", "offset": "1.5"}))
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_offset_bool_rejected():
    result = canonicalize(_req("read_file", {"path": "a", "offset": True}))
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_offset_underscore_digit_string_parses_like_int():
    # int() accepts underscore digit grouping ("1_0" == 10); documented parity
    # with the registry's own int()-based coercion.
    result = canonicalize(_req("read_file", {"path": "a", "offset": "1_0"}))
    assert result.rejection is None
    assert result.action.args.offset == 10


@pytest.mark.parametrize(
    "value,expected",
    [(60, 60), ("60", 60), ("60s", 60), ("2m", 120), ("2 MIN", 120)],
)
def test_timeout_accepted_forms(value, expected):
    result = canonicalize(_req("bash", {"command": "ls", "timeout": value}))
    assert result.rejection is None
    assert result.action.args.timeout == expected


@pytest.mark.parametrize("value", ["60ms", "-5s", 1.5, True])
def test_timeout_rejected_forms(value):
    result = canonicalize(_req("bash", {"command": "ls", "timeout": value}))
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_timeout_underscore_digit_string_parses():
    result = canonicalize(_req("bash", {"command": "ls", "timeout": "1_0"}))
    assert result.rejection is None
    assert result.action.args.timeout == 10


# --- group 11 (bounds after coercion) ----------------------------------------


def test_offset_max_int_accepted():
    result = canonicalize(_req("read_file", {"path": "a", "offset": str(bounds.MAX_INT)}))
    assert result.rejection is None
    assert result.action.args.offset == bounds.MAX_INT


def test_offset_above_max_int_rejected():
    result = canonicalize(_req("read_file", {"path": "a", "offset": str(bounds.MAX_INT + 1)}))
    assert result.rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


def test_offset_negative_rejected():
    result = canonicalize(_req("read_file", {"path": "a", "offset": "-1"}))
    assert result.rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


def test_limit_zero_rejected():
    result = canonicalize(_req("read_file", {"path": "a", "limit": "0"}))
    assert result.rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, 1),
        (-5, 1),
        (601, bounds.MAX_BASH_TIMEOUT),
        ("2147483648", bounds.MAX_BASH_TIMEOUT),
    ],
)
def test_timeout_clamped_never_rejected(value, expected):
    result = canonicalize(_req("bash", {"command": "ls", "timeout": value}))
    assert result.rejection is None
    assert result.action.args.timeout == expected


def test_timeout_integer_above_max_int_rejected_by_check_request():
    result = canonicalize(_req("bash", {"command": "ls", "timeout": bounds.MAX_INT + 1}))
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


# --- group 18 (numeric strings are bounded before conversion) --------------


@pytest.mark.parametrize("field", ["offset", "limit"])
def test_numeric_string_at_the_bound_is_converted(field):
    value = "0" * 31 + "5"  # 32 chars; int() gives 5
    result = canonicalize(_req("read_file", {"path": "x", field: value}))
    assert result.action is not None
    assert getattr(result.action.args, field) == 5


@pytest.mark.parametrize("field", ["offset", "limit"])
def test_numeric_string_over_the_bound_is_string_too_long_before_conversion(field):
    value = "0" * 32 + "5"  # 33 chars
    result = canonicalize(_req("read_file", {"path": "x", field: value}))
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.STRING_TOO_LONG
    assert field in result.rejection.detail
    assert value not in result.rejection.detail


def test_timeout_numeric_string_over_the_bound_is_string_too_long():
    result = canonicalize(_req("bash", {"command": "ls", "timeout": "0" * 32 + "60"}))
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.STRING_TOO_LONG


@pytest.mark.parametrize("tool,args", [
    ("read_file", {"path": "x", "offset": "1" * 1_000_000}),
    ("bash", {"command": "ls", "timeout": "1" * 1_000_000}),
], ids=["offset", "timeout"])
def test_million_digit_numeric_string_is_rejected_fast(tool, args):
    start = time.perf_counter()
    result = canonicalize(_req(tool, args))
    assert time.perf_counter() - start < 0.5
    assert result.rejection is not None
    assert result.rejection.reason_code is ReasonCode.STRING_TOO_LONG


# --- group 12 (capability assembly) ------------------------------------------

_CAPABILITY_CASES = [
    ("read_file", {"path": "/etc/passwd"}, BASE_CAPABILITIES[ActionKind.READ_FILE] | {Capability.HOST_FS}),
    ("write_file", {"path": "../x", "content": "c"}, BASE_CAPABILITIES[ActionKind.WRITE_FILE] | {Capability.HOST_FS}),
    (
        "write_file",
        {"path": ".git/config", "content": "c"},
        BASE_CAPABILITIES[ActionKind.WRITE_FILE] | {Capability.REPO_CONTROL},
    ),
    ("read_file", {"path": ".git/config"}, BASE_CAPABILITIES[ActionKind.READ_FILE]),
    ("write_file", {"path": "src/a/../x", "content": "c"}, BASE_CAPABILITIES[ActionKind.WRITE_FILE]),
]


@pytest.mark.parametrize("tool,arguments,expected_caps", _CAPABILITY_CASES)
def test_capability_assembly(tool, arguments, expected_caps):
    result = canonicalize(_req(tool, arguments))
    assert result.rejection is None
    assert result.action.capabilities == frozenset(expected_caps)


# --- group 13 (idempotence, every kind) --------------------------------------


def _round_trip(action):
    arguments = dict(vars(action.args))
    if "edits" in arguments:
        arguments["edits"] = [{"old": e.old, "new": e.new} for e in arguments["edits"]]
    request = _req(action.kind.value, arguments, call_id=action.call_id, turn=action.turn)
    return canonicalize(request)


@pytest.mark.parametrize("kind,arguments,_expected", _PER_KIND_MINIMAL, ids=[k.value for k, _, _ in _PER_KIND_MINIMAL])
def test_idempotent_per_kind(kind, arguments, _expected):
    first = canonicalize(_req(kind.value, arguments))
    assert first.rejection is None
    second = _round_trip(first.action)
    assert second.rejection is None
    assert second.action == first.action
    assert action_identity(second.action) == action_identity(first.action)


# --- group 14 (bash identity) -------------------------------------------------


def _bash_identity(command):
    result = canonicalize(_req("bash", {"command": command}))
    assert result.rejection is None
    return action_identity(result.action)


def test_bash_identity_distinguishes_whitespace():
    ids = {_bash_identity(c) for c in ("ls", "ls\n", "  ls  ")}
    assert len(ids) == 3


def test_bash_identity_equal_for_repeated_call():
    assert _bash_identity("ls") == _bash_identity("ls")


# --- group 15 (Normalization invariants) --------------------------------------


def _sample_action():
    return canonicalize(_req("bash", {"command": "ls"})).action


def _sample_rejection():
    return canonicalize(_req("bash", {})).rejection


def test_normalization_requires_exactly_one_of_action_and_rejection():
    action = _sample_action()
    rejection = _sample_rejection()
    with pytest.raises(FirewallInternalError):
        Normalization(action=action, rejection=rejection, dropped_keys=0)
    with pytest.raises(FirewallInternalError):
        Normalization(action=None, rejection=None, dropped_keys=0)


def test_normalization_dropped_keys_must_be_a_nonnegative_int():
    action = _sample_action()
    with pytest.raises(FirewallInternalError):
        Normalization(action=action, rejection=None, dropped_keys=-1)
    with pytest.raises(FirewallInternalError):
        Normalization(action=action, rejection=None, dropped_keys=True)


def test_normalization_dropped_keys_must_be_zero_on_rejection():
    rejection = _sample_rejection()
    with pytest.raises(FirewallInternalError):
        Normalization(action=None, rejection=rejection, dropped_keys=1)


# --- group 16 (FIELD_TABLE versus the live registry) --------------------------


def test_field_table_matches_registry():
    for kind in ActionKind:
        spec = _REGISTRY.spec(kind.value)
        fields = FIELD_TABLE[kind]
        assert [f.name for f in fields] == list(spec.params.keys())
        for field in fields:
            pspec = spec.params[field.name]
            reg_required = field.name in spec.required
            if kind is ActionKind.FINISH and field.name == "summary":
                # Declared exception (spec §4): the registry requires
                # `summary` with no default; the Runner already
                # canonicalizes a missing one to "".
                assert reg_required is True
                assert field.required is False
                assert pspec.default is toolspec.MISSING
                assert field.default == ""
                continue
            assert field.required == reg_required
            if not field.required:
                assert pspec.default is not toolspec.MISSING
                assert field.default == pspec.default


# --- group 17 (import isolation) ----------------------------------------------


def _forbidden_imports(path):
    tree = ast.parse(path.read_text())
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "dirtywork" or alias.name.startswith("dirtywork."):
                    if not (
                        alias.name == "dirtywork.firewall"
                        or alias.name.startswith("dirtywork.firewall.")
                    ):
                        found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level >= 2:
                # A relative import climbing out of dirtywork.firewall itself.
                found.append("." * node.level + (node.module or ""))
            elif node.module and (node.module == "dirtywork" or node.module.startswith("dirtywork.")):
                if not (
                    node.module == "dirtywork.firewall"
                    or node.module.startswith("dirtywork.firewall.")
                ):
                    found.append(node.module)
    return found


def test_import_isolation():
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("dirtywork/firewall/paths.py", "dirtywork/firewall/normalize.py"):
        assert _forbidden_imports(root / rel) == [], rel
=== END tests/test_firewall_normalize.py ===

VERIFY: run python3 -m pytest -q -p no:cacheprovider tests/test_firewall_normalize.py and expect 219 passed. Then run python3 -m pytest -q -p no:cacheprovider with timeout=300 and expect all passed, 0 failed (2085 passed). Finish when both pass.
```

---
### Task 3: batch, re-exports and parity

**Files:**
- Modify: `dirtywork/firewall/normalize.py` (append `canonicalize_batch` after its last line, as one `edit_file` anchored on that line), `dirtywork/firewall/__init__.py:12-13` and `:46-48` (imports and `__all__`), `tests/test_firewall_normalize.py` (append the batch tests after its last two lines), `tests/test_firewall_request.py:295-296` (the `__all__` pin grows to 40 names)
- Create: `tests/test_firewall_parity.py`

**Interfaces:**
- Consumes: `canonicalize`, `Normalization`, `Rejection`, `ReasonCode.CALL_ID_DUPLICATE` (Task 2 and #135); the parity tests import `dirtywork.toolspec._validate_args`, `ToolValidationError` and `dirtywork.builtin_tools.default_registry`.
- Produces: `canonicalize_batch(requests: Sequence[ActionRequest]) -> list[Normalization]` (spec §9); the package root re-exports `Normalization`, `canonicalize`, `canonicalize_batch`, `recover_name`, `NormalizedPath`, `TargetClass`, `normalize_path`, and `__all__` has 40 names. This is the surface #137 and #138 import.

- [x] **Dry-run on the scratch clone** (2026-09-19, on top of Task 2): the edits and the file below applied, `tests/test_firewall_normalize.py` 228 passed (219 + 9 batch), `tests/test_firewall_parity.py` 84 passed (45 shared-domain accepts, 17 shared-domain rejections, the eleven exception rows including the numeric-string bound, and the `glob=null` non-exception), `tests/test_firewall_request.py` 40 passed with the grown pin, full host suite **2178 passed** (baseline 1829; 349 new across the three tasks), `ast.parse(..., feature_version=(3, 9))` silent, `git diff --check` clean, `len(dirtywork.firewall.__all__) == 40` and every name resolves. The pin in `test_firewall_request.py` is the one change outside the spec's file list: it is the #135 test that asserts `__all__` verbatim, and spec §2 grows `__all__` by seven, so the test grows with it.
- [ ] **Confirm the base.** Task 2's PR merged (or its run branch as `--branch-from`); the five anchors below must still be at the quoted line numbers (`git show <base>:<file> | sed -n '<lines>p'`).
- [ ] **Launch** the brief below verbatim through the invocation in the header, sampler on, model loaded in the same command. Five `edit_file` calls and one `write_file`; an `apply_edits` in place of several `edit_file`s is fine.
- [ ] **Review** against the gates in the header: apply the brief's five pairs to the base with the round-trip script and `cmp` every produced file against the result; `cmp` the parity file against its block; `files_changed` is exactly the five files; host suite 2178 passed; `diff --check`; 3.9 grammar; `python3 -c "import dirtywork.firewall as f; assert len(f.__all__) == 40"` from the run's worktree.
- [ ] **Ledger** `docs/superpowers/bench/2026-09-XX-issue-136-w3-batch-parity-ledger.md` plus the sampler CSV, committed on the run branch.
- [ ] **PR** titled `feat(firewall): issue #136 W3 — batch, re-exports and parity`, body naming the plan, the spec and the ledger; part 3 of 3; **closes #136**.
- [ ] After merge: comment on issue #136 with the three ledgers; issue #137 (policy engine) is next and is briefed against the merged package.

### Worker brief W3

```text
Issue #136 task W3 of 3 (Worker Action Firewall C): add canonicalize_batch to the end of dirtywork/firewall/normalize.py, re-export the seven new names from dirtywork/firewall/__init__.py, grow the __all__ pin in tests/test_firewall_request.py to match, append the batch tests to tests/test_firewall_normalize.py, and add tests/test_firewall_parity.py, which pins the pass to the registry's validation on a shared domain with an explicit exception table. paths.py (W1) and normalize.py (W2) exist. Nothing outside the package imports it. Spec: docs/superpowers/specs/2026-09-19-issue-136-firewall-normalization-design.md sections 2, 9, 12 and 13 (the texts below are its exact content).

Touch ONLY dirtywork/firewall/normalize.py, dirtywork/firewall/__init__.py, tests/test_firewall_normalize.py, tests/test_firewall_request.py, tests/test_firewall_parity.py. Apply the FIVE edit_file edits below, each with the exact old and new text (byte for byte; keep indentation and blank lines; the "old:"/"new:" labels and the marker lines are not part of the text; NEVER use write_file or append_file on an existing file). Then create the ONE new file with ONE write_file call whose content is exactly the text between its BEGIN and END marker lines (byte for byte; the file ends with a newline after its last line). Use relative paths exactly as written (never an absolute /work/... path). Line numbers refer to the files before any of these edits. No other files, no docs, no commits, nothing else.

EDIT edit_file on dirtywork/firewall/normalize.py (old is lines 388-388; the new text is 42 lines). old:
    return Normalization(action=action, rejection=None, dropped_keys=dropped_keys)
new:
    return Normalization(action=action, rejection=None, dropped_keys=dropped_keys)


def canonicalize_batch(requests: "Sequence[ActionRequest]") -> "list[Normalization]":
    """Canonicalize a batch of requests in order (spec §9): the first request
    carrying a given `call_id` goes through `canonicalize` normally, whatever
    it decides; every later request whose `call_id` equals an earlier one's
    -- compared with plain `==` on the id as given, before any validation, so
    a non-string id is compared as-is -- is rejected with
    `Rejection(ReasonCode.CALL_ID_DUPLICATE, ...)` naming only its batch
    index, and is never canonicalized. Every other request is independent:
    one request's rejection never affects its neighbours. Ids are tracked in
    a `set` for a fast membership check; an id a set cannot hash (a list)
    falls back to `==` against the unhashable ids seen so far rather than
    raising."""
    results: "list[Normalization]" = []
    seen: set = set()
    seen_unhashable: list = []
    for index, request in enumerate(requests):
        call_id = request.call_id
        try:
            is_duplicate = call_id in seen
        except TypeError:
            is_duplicate = any(call_id == existing for existing in seen_unhashable)
        if is_duplicate:
            results.append(
                Normalization(
                    action=None,
                    rejection=Rejection(
                        ReasonCode.CALL_ID_DUPLICATE,
                        f"duplicate call_id at batch index {index}",
                    ),
                    dropped_keys=0,
                )
            )
            continue
        try:
            seen.add(call_id)
        except TypeError:
            seen_unhashable.append(call_id)
        results.append(canonicalize(request))
    return results

EDIT edit_file on tests/test_firewall_normalize.py (old is lines 598-599; the new text is 116 lines). old:
    for rel in ("dirtywork/firewall/paths.py", "dirtywork/firewall/normalize.py"):
        assert _forbidden_imports(root / rel) == [], rel
new:
    for rel in ("dirtywork/firewall/paths.py", "dirtywork/firewall/normalize.py"):
        assert _forbidden_imports(root / rel) == [], rel


# --- batch: canonicalize_batch --------------------------------------------

from dirtywork.firewall.normalize import canonicalize_batch


def test_batch_three_distinct_ids_all_canonicalized_in_order():
    requests = [
        _req("bash", {"command": "ls"}, call_id="call_1", batch_size=1),
        _req("bash", {"command": "pwd"}, call_id="call_2", batch_size=1),
        _req("bash", {"command": "echo hi"}, call_id="call_3", batch_size=1),
    ]
    results = canonicalize_batch(requests)
    assert len(results) == 3
    assert [r.rejection for r in results] == [None, None, None]
    assert [r.action.args.command for r in results] == ["ls", "pwd", "echo hi"]


def test_batch_duplicate_id_at_index_2():
    requests = [
        _req("bash", {"command": "ls"}, call_id="zzqx7", batch_size=1),
        _req("bash", {"command": "pwd"}, call_id="b", batch_size=1),
        _req("bash", {"command": "echo hi"}, call_id="zzqx7", batch_size=1),
    ]
    results = canonicalize_batch(requests)
    assert results[0].rejection is None
    assert results[0].action.args.command == "ls"
    assert results[1].rejection is None
    assert results[2].rejection is not None
    assert results[2].rejection.reason_code is ReasonCode.CALL_ID_DUPLICATE
    assert "index 2" in results[2].rejection.detail
    assert "zzqx7" not in results[2].rejection.detail
    assert results[2].dropped_keys == 0


def test_batch_three_copies_of_same_id():
    requests = [
        _req("bash", {"command": "ls"}, call_id="dup", batch_size=1),
        _req("bash", {"command": "pwd"}, call_id="dup", batch_size=1),
        _req("bash", {"command": "echo hi"}, call_id="dup", batch_size=1),
    ]
    results = canonicalize_batch(requests)
    assert results[0].rejection is None
    assert results[0].action.args.command == "ls"
    assert results[1].rejection is not None
    assert results[1].rejection.reason_code is ReasonCode.CALL_ID_DUPLICATE
    assert results[2].rejection is not None
    assert results[2].rejection.reason_code is ReasonCode.CALL_ID_DUPLICATE


def test_batch_duplicate_of_a_rejected_first_occurrence_is_still_flagged():
    # Dedup is by position, before validation: the first occurrence being
    # itself rejected (for an unrelated reason) does not exempt a later
    # occurrence from call_id_duplicate.
    requests = [
        _req("not_a_tool", {}, call_id="x", batch_size=1),
        _req("bash", {"command": "ls"}, call_id="x", batch_size=1),
    ]
    results = canonicalize_batch(requests)
    assert results[0].rejection is not None
    assert results[0].rejection.reason_code is ReasonCode.TOOL_UNKNOWN
    assert results[1].rejection is not None
    assert results[1].rejection.reason_code is ReasonCode.CALL_ID_DUPLICATE


def test_batch_empty_is_empty():
    assert canonicalize_batch([]) == []


def test_batch_malformed_neighbour_does_not_affect_others():
    requests = [
        _req("write_file", {}, call_id="ok_1", batch_size=1),  # missing content
        _req("bash", {"command": "ls"}, call_id="ok_2", batch_size=1),
    ]
    results = canonicalize_batch(requests)
    assert results[0].rejection is not None
    assert results[0].rejection.reason_code is ReasonCode.ARGUMENT_MISSING
    assert results[1].rejection is None
    assert results[1].action.args.command == "ls"


def test_batch_ids_compared_exactly():
    # "call_1" and "call_1 " are different ids under plain `==`; the second
    # is not deduplicated against the first, so it reaches check_request on
    # its own and is rejected for whitespace, not for being a duplicate.
    requests = [
        _req("bash", {"command": "ls"}, call_id="call_1", batch_size=1),
        _req("bash", {"command": "pwd"}, call_id="call_1 ", batch_size=1),
    ]
    results = canonicalize_batch(requests)
    assert results[0].rejection is None
    assert results[1].rejection is not None
    assert results[1].rejection.reason_code is ReasonCode.CALL_ID_INVALID


def test_batch_size_field_not_required_to_match_request_count():
    # canonicalize_batch never checks batch_size against len(requests); that
    # consistency is the adapter's job (spec §9).
    requests = [
        _req("bash", {"command": "ls"}, call_id="call_1", batch_size=1),
        _req("bash", {"command": "pwd"}, call_id="call_2", batch_size=1),
    ]
    assert len(requests) == 2
    results = canonicalize_batch(requests)
    assert all(r.rejection is None for r in results)


def test_batch_single_request_matches_canonicalize():
    request = _req("bash", {"command": "ls"}, call_id="call_1", batch_size=1)
    batch_result = canonicalize_batch([request])[0]
    direct_result = canonicalize(request)
    assert batch_result.action == direct_result.action
    assert batch_result.rejection == direct_result.rejection

EDIT edit_file on dirtywork/firewall/__init__.py (old is lines 12-13; the new text is 4 lines). old:
from .errors import FirewallInternalError
from .reasons import ReasonClass, ReasonCode, reason_class
new:
from .errors import FirewallInternalError
from .normalize import Normalization, canonicalize, canonicalize_batch, recover_name
from .paths import NormalizedPath, TargetClass, normalize_path
from .reasons import ReasonClass, ReasonCode, reason_class

EDIT edit_file on dirtywork/firewall/__init__.py (old is lines 46-48; the new text is 5 lines). old:
    "FirewallEvent", "action_identity", "rejection_identity",
    "Rejection", "check_request",
]
new:
    "FirewallEvent", "action_identity", "rejection_identity",
    "Rejection", "check_request",
    "Normalization", "canonicalize", "canonicalize_batch", "recover_name",
    "NormalizedPath", "TargetClass", "normalize_path",
]

EDIT edit_file on tests/test_firewall_request.py (old is lines 295-296; the new text is 4 lines). old:
        "Rejection", "check_request",
    ]
new:
        "Rejection", "check_request",
        "Normalization", "canonicalize", "canonicalize_batch", "recover_name",
        "NormalizedPath", "TargetClass", "normalize_path",
    ]

FILE tests/test_firewall_parity.py (new, 354 lines) — write_file with exactly:
=== BEGIN tests/test_firewall_parity.py ===
"""Pins `canonicalize` to the registry's VALIDATION step,
`dirtywork.toolspec._validate_args(spec, args)` -- not `ToolRegistry.execute`,
which additionally clamps `timeout`, applies the run deadline and byte caps,
and runs the tool (spec §12, §13)."""
from __future__ import annotations

import json

import pytest

from dirtywork import toolspec
from dirtywork.builtin_tools import default_registry
from dirtywork.firewall.bounds import MAX_GLOB_CHARS, MAX_PATH_CHARS, MAX_PATTERN_CHARS
from dirtywork.firewall.normalize import canonicalize
from dirtywork.firewall.reasons import ReasonCode
from dirtywork.firewall.schema import ActionRequest, Edit

_reg = default_registry()


def _spec(tool):
    return _reg.spec(tool)


def _req(tool, args, call_id="call_1", turn=1, batch_index=0, batch_size=1):
    return ActionRequest(
        call_id=call_id,
        tool_name=tool,
        arguments=args,
        parse_error=None,
        raw_chars=len(json.dumps(args)),
        turn=turn,
        batch_index=batch_index,
        batch_size=batch_size,
    )


def _registry(tool, args):
    """("accept", call_args) or ("reject", None), catching ToolValidationError."""
    try:
        call_args = toolspec._validate_args(_spec(tool), args)
    except toolspec.ToolValidationError:
        return "reject", None
    return "accept", call_args


def _firewall(tool, args):
    """("accept", action) or ("reject", rejection)."""
    result = canonicalize(_req(tool, args))
    if result.rejection is not None:
        return "reject", result.rejection
    return "accept", result.action


def _norm_path(value):
    """The registry does not normalize paths at validation; drop '.' and
    empty components the same way normalize_path does, so a registry path
    string compares equal to the canonical one (spec §12)."""
    parts = [p for p in value.split("/") if p not in ("", ".")]
    joined = "/".join(parts)
    if value.startswith("/"):
        return "/" + joined if joined else "/"
    return joined if joined else "."


def _assert_values_equal(reg_args, action):
    for key, reg_value in reg_args.items():
        fw_value = getattr(action.args, key)
        if key == "path":
            assert _norm_path(reg_value) == fw_value
        elif key == "edits":
            expected = tuple(Edit(old=d["old"], new=d["new"]) for d in reg_value)
            assert expected == fw_value
        else:
            assert reg_value == fw_value


# --- 1. shared-domain corpus: same accept, equal values ----------------------

_SHARED_DOMAIN_CASES = [
    ("read_file", {"path": "a"}),
    ("read_file", {"path": "a", "offset": 0, "limit": 400}),
    ("read_file", {"path": "a", "offset": "5", "limit": "1_0"}),
    ("read_file", {"path": "a", "bogus": 1}),
    ("read_file", {"path": "a", "e1": 1, "e2": 2, "e3": 3}),
    ("write_file", {"path": "a", "content": "c"}),
    ("write_file", {"path": "a", "content": "c", "bogus": 1}),
    ("write_file", {"path": "a", "content": "c", "e1": 1, "e2": 2, "e3": 3}),
    ("append_file", {"path": "a", "text": "t"}),
    ("append_file", {"path": "a", "text": "t", "bogus": 1}),
    ("append_file", {"path": "a", "text": "t", "e1": 1, "e2": 2, "e3": 3}),
    ("edit_file", {"path": "a", "old_string": "o", "new_string": "n"}),
    ("edit_file", {"path": "a", "old_string": "o", "new_string": "n", "bogus": 1}),
    ("edit_file", {"path": "a", "old_string": "o", "new_string": "n", "e1": 1, "e2": 2, "e3": 3}),
    ("apply_edits", {"path": "a", "edits": [{"old": "o", "new": "n"}]}),
    (
        "apply_edits",
        {
            "path": "a",
            "edits": [
                {"old": "o1", "new": "n1"},
                {"old": "o2", "new": "n2"},
                {"old": "o3", "new": "n3"},
            ],
        },
    ),
    ("apply_edits", {"path": "a", "edits": [{"old": "o", "new": "n"}], "bogus": 1}),
    (
        "apply_edits",
        {"path": "a", "edits": [{"old": "o", "new": "n"}], "e1": 1, "e2": 2, "e3": 3},
    ),
    ("insert_before", {"path": "a", "anchor": "x", "text": "t"}),
    ("insert_before", {"path": "a", "anchor": "x", "text": "t", "bogus": 1}),
    ("insert_before", {"path": "a", "anchor": "x", "text": "t", "e1": 1, "e2": 2, "e3": 3}),
    ("insert_after", {"path": "a", "anchor": "x", "text": "t"}),
    ("insert_after", {"path": "a", "anchor": "x", "text": "t", "bogus": 1}),
    ("insert_after", {"path": "a", "anchor": "x", "text": "t", "e1": 1, "e2": 2, "e3": 3}),
    ("list_dir", {}),
    ("list_dir", {"path": "."}),
    ("list_dir", {"bogus": 1}),
    ("list_dir", {"e1": 1, "e2": 2, "e3": 3}),
    ("grep", {"pattern": "p"}),
    ("grep", {"pattern": "p", "path": ".", "glob": None}),
    ("grep", {"pattern": "p", "glob": None}),
    ("grep", {"pattern": "p", "bogus": 1}),
    ("grep", {"pattern": "p", "timeout": 30}),
    ("grep", {"pattern": "p", "e1": 1, "e2": 2, "e3": 3}),
    ("bash", {"command": "ls"}),
    ("bash", {"command": "ls", "timeout": 120}),
    ("bash", {"command": "ls", "timeout": "60"}),
    ("bash", {"command": "ls", "timeout": "2m"}),
    ("bash", {"command": "ls", "timeout": "2 MIN"}),
    ("bash", {"command": "ls", "bogus": 1}),
    ("bash", {"command": "ls", "e1": 1, "e2": 2, "e3": 3}),
    ("finish", {"summary": "done"}),
    ("finish", {"summary": ""}),
    ("finish", {"summary": "done", "bogus": 1}),
    ("finish", {"summary": "done", "e1": 1, "e2": 2, "e3": 3}),
]
_SHARED_DOMAIN_IDS = [f"{i}:{tool}:{sorted(args)}" for i, (tool, args) in enumerate(_SHARED_DOMAIN_CASES)]


@pytest.mark.parametrize("tool,args", _SHARED_DOMAIN_CASES, ids=_SHARED_DOMAIN_IDS)
def test_shared_domain_accept_and_equal(tool, args):
    reg_outcome, reg_val = _registry(tool, args)
    fw_outcome, action = _firewall(tool, args)
    assert reg_outcome == "accept"
    assert fw_outcome == "accept"
    _assert_values_equal(reg_val, action)


# --- 2. shared-domain rejections: same reject ---------------------------------

_SHARED_DOMAIN_REJECTIONS = [
    ("read_file", {}),
    ("write_file", {"path": "a"}),
    ("append_file", {"path": "a"}),
    ("edit_file", {"path": "a", "old_string": "o"}),
    ("apply_edits", {"path": "a"}),
    ("insert_before", {"path": "a", "anchor": "x"}),
    ("insert_after", {"path": "a", "anchor": "x"}),
    ("grep", {}),
    ("bash", {}),
    ("read_file", {"path": 5}),
    ("write_file", {"path": "a", "content": []}),
    ("read_file", {"path": "a", "offset": "1.5"}),
    ("bash", {"command": "ls", "timeout": "60ms"}),
    ("bash", {"command": "ls", "timeout": True}),
    ("apply_edits", {"path": "a", "edits": [{"old": "o", "new": "n", "sneaky": 1}]}),
    ("apply_edits", {"path": "a", "edits": [{"old": "o"}]}),
    ("apply_edits", {"path": "a", "edits": "not-a-list"}),
]
_SHARED_DOMAIN_REJECTION_IDS = [
    f"{i}:{tool}:{sorted(args)}" for i, (tool, args) in enumerate(_SHARED_DOMAIN_REJECTIONS)
]


@pytest.mark.parametrize("tool,args", _SHARED_DOMAIN_REJECTIONS, ids=_SHARED_DOMAIN_REJECTION_IDS)
def test_shared_domain_rejection_parity(tool, args):
    reg_outcome, _reg_val = _registry(tool, args)
    fw_outcome, _rejection = _firewall(tool, args)
    assert reg_outcome == "reject"
    assert fw_outcome == "reject"


# --- 3. exception table, one test per row -------------------------------------

_NULL_OPTIONAL_NON_NONE_DEFAULT = [
    ("read_file", {"path": "a", "offset": None}, "offset", 0),
    ("read_file", {"path": "a", "limit": None}, "limit", 400),
    ("list_dir", {"path": None}, "path", "."),
    ("grep", {"pattern": "p", "path": None}, "path", "."),
    ("bash", {"command": "ls", "timeout": None}, "timeout", 120),
]


@pytest.mark.parametrize(
    "tool,args,field,default",
    _NULL_OPTIONAL_NON_NONE_DEFAULT,
    ids=[f"{tool}.{field}" for tool, _, field, _ in _NULL_OPTIONAL_NON_NONE_DEFAULT],
)
def test_null_on_optional_non_none_default_registry_rejects_firewall_defaults(tool, args, field, default):
    reg_outcome, _reg_val = _registry(tool, args)
    assert reg_outcome == "reject"
    fw_outcome, action = _firewall(tool, args)
    assert fw_outcome == "accept"
    assert getattr(action.args, field) == default


def test_grep_glob_none_explicit_is_not_an_exception_both_accept_none():
    args = {"pattern": "p", "glob": None}
    reg_outcome, reg_val = _registry("grep", args)
    fw_outcome, action = _firewall("grep", args)
    assert reg_outcome == "accept" and reg_val["glob"] is None
    assert fw_outcome == "accept" and action.args.glob is None


@pytest.mark.parametrize(
    "tool,args,field",
    [
        ("read_file", {"path": "a", "offset": -1}, "offset"),
        ("read_file", {"path": "a", "limit": 0}, "limit"),
    ],
    ids=["offset_negative", "limit_zero"],
)
def test_offset_limit_domain_registry_accepts_firewall_number_out_of_range(tool, args, field):
    reg_outcome, reg_val = _registry(tool, args)
    assert reg_outcome == "accept"
    assert reg_val[field] == args[field]
    fw_outcome, rejection = _firewall(tool, args)
    assert fw_outcome == "reject"
    assert rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


def test_offset_numeric_string_above_max_int_registry_accepts_firewall_rejects():
    args = {"path": "a", "offset": "2147483648"}
    reg_outcome, reg_val = _registry("read_file", args)
    assert reg_outcome == "accept"
    assert reg_val["offset"] == 2147483648
    fw_outcome, rejection = _firewall("read_file", args)
    assert fw_outcome == "reject"
    assert rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


@pytest.mark.parametrize("value", [0, -5], ids=["zero", "negative"])
def test_bash_timeout_low_registry_unchanged_firewall_clamps_to_one(value):
    args = {"command": "ls", "timeout": value}
    reg_outcome, reg_val = _registry("bash", args)
    assert reg_outcome == "accept"
    assert reg_val["timeout"] == value
    fw_outcome, action = _firewall("bash", args)
    assert fw_outcome == "accept"
    assert action.args.timeout == 1


def test_bash_timeout_601_registry_unchanged_firewall_clamps_to_600():
    args = {"command": "ls", "timeout": 601}
    reg_outcome, reg_val = _registry("bash", args)
    assert reg_outcome == "accept"
    assert reg_val["timeout"] == 601
    fw_outcome, action = _firewall("bash", args)
    assert fw_outcome == "accept"
    assert action.args.timeout == 600


def test_bash_timeout_numeric_string_above_max_int_registry_unchanged_firewall_clamps_to_600():
    args = {"command": "ls", "timeout": "2147483648"}
    reg_outcome, reg_val = _registry("bash", args)
    assert reg_outcome == "accept"
    assert reg_val["timeout"] == 2147483648
    fw_outcome, action = _firewall("bash", args)
    assert fw_outcome == "accept"
    assert action.args.timeout == 600


def test_bash_timeout_integer_above_max_int_registry_accepts_firewall_rejects():
    # The pair the exception table calls out precisely: the same magnitude as
    # an int is in the registry's validation domain (no domain check there)
    # but is caught by the Firewall's own structural walk (check_request)
    # before the pass ever clamps timeout, since it is outside
    # [MIN_INT, MAX_INT]. The string form above clamps instead (previous test).
    args = {"command": "ls", "timeout": 2147483648}
    reg_outcome, reg_val = _registry("bash", args)
    assert reg_outcome == "accept"
    assert reg_val["timeout"] == 2147483648
    fw_outcome, rejection = _firewall("bash", args)
    assert fw_outcome == "reject"
    assert rejection.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


@pytest.mark.parametrize(
    "tool,args",
    [
        ("grep", {"pattern": "p" * (MAX_PATTERN_CHARS + 1)}),
        ("grep", {"pattern": "p", "glob": "g" * (MAX_GLOB_CHARS + 1)}),
        ("read_file", {"path": "p" * (MAX_PATH_CHARS + 1)}),
    ],
    ids=["pattern_over_max", "glob_over_max", "path_over_max"],
)
def test_string_over_section4_bound_registry_accepts_firewall_string_too_long(tool, args):
    reg_outcome, _reg_val = _registry(tool, args)
    assert reg_outcome == "accept"
    fw_outcome, rejection = _firewall(tool, args)
    assert fw_outcome == "reject"
    assert rejection.reason_code is ReasonCode.STRING_TOO_LONG


def test_33_unknown_top_level_keys_registry_drops_firewall_collection_too_large():
    args = {"command": "ls"}
    for i in range(33):
        args[f"unknown_{i}"] = i
    reg_outcome, reg_val = _registry("bash", args)
    assert reg_outcome == "accept"
    assert reg_val == {"command": "ls", "timeout": 120}
    fw_outcome, rejection = _firewall("bash", args)
    assert fw_outcome == "reject"
    assert rejection.reason_code is ReasonCode.COLLECTION_TOO_LARGE


def test_finish_no_summary_registry_rejects_firewall_accepts_empty():
    reg_outcome, _reg_val = _registry("finish", {})
    assert reg_outcome == "reject"
    fw_outcome, action = _firewall("finish", {})
    assert fw_outcome == "accept"
    assert action.args.summary == ""


def test_apply_edits_empty_old_registry_accepts_firewall_rejects():
    args = {"path": "a", "edits": [{"old": "", "new": "n"}]}
    reg_outcome, reg_val = _registry("apply_edits", args)
    assert reg_outcome == "accept"
    assert reg_val["edits"] == [{"old": "", "new": "n"}]
    fw_outcome, rejection = _firewall("apply_edits", args)
    assert fw_outcome == "reject"
    assert rejection.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_apply_edits_unknown_nested_key_firewall_code_is_argument_unexpected():
    args = {"path": "a", "edits": [{"old": "o", "new": "n", "sneaky": 1}]}
    reg_outcome, _reg_val = _registry("apply_edits", args)
    assert reg_outcome == "reject"
    fw_outcome, rejection = _firewall("apply_edits", args)
    assert fw_outcome == "reject"
    assert rejection.reason_code is ReasonCode.ARGUMENT_UNEXPECTED


def test_numeric_string_over_32_chars_registry_accepts_firewall_string_too_long():
    args = {"path": "x", "offset": "0" * 40 + "5"}
    outcome, call_args = _registry("read_file", args)
    assert outcome == "accept"
    assert call_args["offset"] == 5
    outcome, rejection = _firewall("read_file", args)
    assert outcome == "reject"
    assert rejection.reason_code is ReasonCode.STRING_TOO_LONG
=== END tests/test_firewall_parity.py ===

VERIFY: run python3 -m pytest -q -p no:cacheprovider tests/test_firewall_normalize.py tests/test_firewall_parity.py tests/test_firewall_request.py and expect 352 passed (228 + 84 + 40). Then run python3 -m pytest -q -p no:cacheprovider with timeout=300 and expect all passed, 0 failed (2178 passed). Finish when both pass.
```
