# Issue #135: Firewall canonical schema, bounds, capabilities and reason codes — implementation plan

> **For agentic workers:** this plan is executed by the released dirtywork (repository `CLAUDE.md` dogfood rule), one run per task, each from the brief quoted verbatim under that task. The orchestrator dry-runs every brief on a scratch clone first, reviews each produced branch, and opens one PR per task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** land the closed, harness-owned Firewall contract from the spec as the new package `dirtywork/firewall/`, with tests that pin every vocabulary, and no runtime behavior change.

**Architecture:** six small modules with strictly downward imports (`errors`, `bounds` → `reasons` → `capabilities` → `schema` → `request`), each one task, each independently green before the next exists. The package initializer stays a docstring until the last task adds the re-exports. Nothing outside the package imports it; issue #136 is the first consumer.

**Tech Stack:** Python >=3.9 (stdlib only: `enum`, `dataclasses`, `hashlib`, `json`), pytest. No dependencies added.

**Spec:** [`docs/superpowers/specs/2026-09-08-issue-135-firewall-schema-design.md`](../specs/2026-09-08-issue-135-firewall-schema-design.md) (PR #157, approved 2026-09-08). Section numbers below refer to it.

## Global Constraints

- Repository `CLAUDE.md`: the latest released dirtywork plus a local worker implements code; Claude plans, briefs and reviews. PyPI checked 2026-09-08: `dirtywork==0.13.1`. Worker: `qwen/qwen3-coder-next` via LM Studio (`--provider openai --base-url http://localhost:1234/v1`). Image: `dirtywork-worker-pytest:0.13` (Python 3.11.2), network disabled.
- Python 3.9 compatible source: `from __future__ import annotations` first in every module and test; `class X(str, enum.Enum)`; no `StrEnum`, `match`, `kw_only`, `slots=True`; `tomllib` only behind a fallback (spec §2).
- Package rules (spec §2): stdlib only; `dirtywork/firewall/` imports nothing from `dirtywork/` except `dirtywork.providers.ToolCall`; tests import from submodules, never the package root, until Task 5 adds the re-exports.
- Enum values are lowercase; member names are the uppercase of the value. Every closed vocabulary is pinned by a literal list in a test (spec §11).
- Every task's brief names its files; the worker touches only those. New files are written with `write_file`; the one existing-file edit (`pyproject.toml`, Task 1) is an exact `edit_file` pair with its line number. Briefs carry the full source and tests: the contract is closed, so any deviation from the spec is a defect, and the 2026-09-08 runs showed that exact briefs land first try.
- Each task runs from `main` at the head the dry-run used, after the previous task's PR has merged (the next task imports the previous file). One PR per task, one ledger row per run under `docs/superpowers/bench/`, metrics sampler on for every run.
- No merge and no release without the owner's explicit per-action go.

## Invocation (every task)

```bash
nohup tools/soak_sampler.sh docs/superpowers/bench/135-w<N>-sampler.csv &
pipx run --spec 'dirtywork==0.13.1' dirtywork run "$(cat /path/to/brief-w<N>.txt)" \
  --repo /Users/jimschneider/repos/dirtywork \
  --provider openai --base-url http://localhost:1234/v1 --model qwen/qwen3-coder-next \
  --sandbox docker --image dirtywork-worker-pytest:0.13 \
  --verify "python3 -m pytest -q -p no:cacheprovider" --verify-rounds 2 \
  --max-turns 60 --timeout 1800
tools/soak_sampler.sh docs/superpowers/bench/135-w<N>-sampler.csv --stop
```

The brief is passed as one argv element. The sampler is stopped on every exit path.

## Review gates (every task)

- Worker diff matches the dry-run files byte for byte (allow the known one-newline `append_file` quirk; none expected here since every file is new).
- `files_changed` lists only the brief's files.
- Host: `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider` all green with the expected count; `git diff --check` clean; `python3 -c "import ast,sys; [ast.parse(open(f).read(), feature_version=(3,9)) for f in sys.argv[1:]]" dirtywork/firewall/*.py tests/test_firewall_*.py` silent.
- Task 1 additionally: build a wheel (`pipx run build --wheel`) and `python3 -c "import dirtywork.firewall"` from a clean venv with that wheel installed (spec §11 test 15).
- Copy `diff.patch` and `orchestrator/` receipts out of the run dir before any `runs clean`.
- Ledger row: status, turns, wall, prompt/completion tokens, tok/s, nudges, verdict, tool-call counts, sampler summary.

---
### Task 1: package skeleton: errors, bounds, registration

**Files:**
- Create: `dirtywork/firewall/__init__.py` (docstring only), `dirtywork/firewall/errors.py`, `dirtywork/firewall/bounds.py`
- Modify: `pyproject.toml:34` (the one-line `packages` list)
- Test: `tests/test_firewall_bounds.py`

**Interfaces:**
- Consumes: nothing in the package; the two equality pins import `dirtywork.builtin_tools.MAX_APPLY_EDITS` and `BASH_SPEC.caps.timeout_max` in the test only
- Produces: `dirtywork.firewall.errors.FirewallInternalError(Exception)`; every constant in spec §6.1 as a module-level int in `dirtywork.firewall.bounds` (`FIREWALL_SCHEMA_VERSION`, `MAX_CALL_ID_CHARS`, `MAX_TOOL_NAME_CHARS`, `MAX_RAW_ARGUMENT_CHARS`, `MAX_STRING_CHARS`, `MAX_PATH_CHARS`, `MAX_COMMAND_CHARS`, `MAX_PATTERN_CHARS`, `MAX_GLOB_CHARS`, `MAX_SUMMARY_CHARS`, `MAX_ARGUMENT_KEYS`, `MAX_NESTED_KEYS`, `MAX_NESTING_DEPTH`, `MAX_COLLECTION_ITEMS`, `MAX_INT`, `MIN_INT`, `MAX_BASH_TIMEOUT`, `MAX_BATCH_CALLS`, `MAX_DETAIL_CHARS`, `IDENTITY_VERSION`); `dirtywork.firewall` importable and shipped in the wheel

- [x] **Dry-run on the scratch clone** (2026-09-08, clone of `main` at `d3c7ce8`): the files below written, `tests/test_firewall_*.py` for this task 5 passed, full host suite 1714 passed (baseline 1709), `ast.parse(..., feature_version=(3, 9))` clean. The brief text is the reference: its file blocks are the dry-run files byte for byte (checked by a round-trip script).
- [ ] **Confirm the base.** `main` must be at the head that includes Task 0's PR (none for Task 1). Re-check that `pyproject.toml` line 34 is still the one-line `packages` list quoted in the brief.
- [ ] **Launch** the brief below verbatim through the invocation in the header, sampler on.
- [ ] **Review** against the gates in the header: `diff` every produced file against the brief's block (extract with the round-trip script or by eye); `files_changed` is exactly the brief's file list; host suite 1714 passed; `git diff --check` clean; 3.9 AST check silent.
- [ ] Build a wheel from the run's worktree (`pipx run build --wheel`), install it in a fresh venv, run `python -c "import dirtywork.firewall"` (dry-run: wheel lists 7 firewall entries, import succeeds).
- [ ] **Ledger row** in `docs/superpowers/bench/2026-09-XX-issue-135-w1-ledger.md` (status, turns, wall, tokens, tok/s, nudges, verdict, tool-call counts, sampler summary, diff-vs-brief result).
- [ ] **PR** `dirtywork/<slug>` → `main`, titled `feat(firewall): issue #135 W1 — package skeleton: errors, bounds, registration`, body naming the plan, the ledger and the spec; part 1 of 5 for issue #135 (does not close it). Wait for the owner's explicit merge go.

### Worker brief W1

```text
Issue #135 task W1 of 5 (Worker Action Firewall B): create the new package dirtywork/firewall/ with its error type and its bound constants, and register the package in pyproject.toml. This is the closed harness-owned contract every later Firewall issue builds on; nothing else in dirtywork/ imports it yet, so runtime behavior does not change. Spec: docs/superpowers/specs/2026-09-08-issue-135-firewall-schema-design.md sections 2, 6.1 and 6.3 (you do not need to read it; the files below are the spec's exact content).

Touch ONLY dirtywork/firewall/__init__.py, dirtywork/firewall/errors.py, dirtywork/firewall/bounds.py, tests/test_firewall_bounds.py, pyproject.toml. Create each NEW file with ONE write_file call whose content is exactly the text between its BEGIN and END marker lines below (byte for byte; keep every blank line; the file ends with a newline after its last line; the marker lines themselves are not part of the file). The ONE existing-file change is an edit_file with the exact old/new strings below. No other files, no docs, no commits, nothing else.

EDIT edit_file on pyproject.toml (line 34). old:
packages = ["dirtywork", "dirtywork.providers", "dirtywork.sandbox", "dirtywork.contract"]
new:
packages = ["dirtywork", "dirtywork.providers", "dirtywork.sandbox", "dirtywork.contract", "dirtywork.firewall"]

FILE dirtywork/firewall/__init__.py (new, 1 lines) — write_file with exactly:
=== BEGIN dirtywork/firewall/__init__.py ===
"""Worker Action Firewall: canonical schema, bounds, capabilities and reason codes."""
=== END dirtywork/firewall/__init__.py ===

FILE dirtywork/firewall/errors.py (new, 10 lines) — write_file with exactly:
=== BEGIN dirtywork/firewall/errors.py ===
"""The Firewall's own invariant-violation error."""
from __future__ import annotations


class FirewallInternalError(Exception):
    """Raised when the package finds its own invariant violated.

    Subclasses ``Exception``, not ``ValueError``, so a caller's
    ``try/except ValueError`` cannot swallow it; the caller must fail closed.
    """
=== END dirtywork/firewall/errors.py ===

FILE dirtywork/firewall/bounds.py (new, 23 lines) — write_file with exactly:
=== BEGIN dirtywork/firewall/bounds.py ===
"""Integer and size bounds for the Firewall's boundary validator (spec §6.1)."""
from __future__ import annotations

FIREWALL_SCHEMA_VERSION = 1
MAX_CALL_ID_CHARS = 256
MAX_TOOL_NAME_CHARS = 512
MAX_RAW_ARGUMENT_CHARS = 32 * 1024 * 1024
MAX_STRING_CHARS = 5 * 1024 * 1024
MAX_PATH_CHARS = 4096
MAX_COMMAND_CHARS = 32_768
MAX_PATTERN_CHARS = 4096
MAX_GLOB_CHARS = 1024
MAX_SUMMARY_CHARS = 64_000
MAX_ARGUMENT_KEYS = 32
MAX_NESTED_KEYS = 8
MAX_NESTING_DEPTH = 4
MAX_COLLECTION_ITEMS = 100
MAX_INT = 2**31 - 1
MIN_INT = -MAX_INT - 1
MAX_BASH_TIMEOUT = 600
MAX_BATCH_CALLS = 32
MAX_DETAIL_CHARS = 200
IDENTITY_VERSION = 1
=== END dirtywork/firewall/bounds.py ===

FILE tests/test_firewall_bounds.py (new, 86 lines) — write_file with exactly:
=== BEGIN tests/test_firewall_bounds.py ===
from __future__ import annotations

import os
import re

from dirtywork.firewall.bounds import (
    FIREWALL_SCHEMA_VERSION,
    IDENTITY_VERSION,
    MAX_ARGUMENT_KEYS,
    MAX_BASH_TIMEOUT,
    MAX_BATCH_CALLS,
    MAX_CALL_ID_CHARS,
    MAX_COLLECTION_ITEMS,
    MAX_COMMAND_CHARS,
    MAX_DETAIL_CHARS,
    MAX_GLOB_CHARS,
    MAX_INT,
    MAX_NESTED_KEYS,
    MAX_NESTING_DEPTH,
    MAX_PATH_CHARS,
    MAX_PATTERN_CHARS,
    MAX_RAW_ARGUMENT_CHARS,
    MAX_STRING_CHARS,
    MAX_SUMMARY_CHARS,
    MAX_TOOL_NAME_CHARS,
    MIN_INT,
)
from dirtywork.firewall.errors import FirewallInternalError


def test_constant_pins():
    assert FIREWALL_SCHEMA_VERSION == 1
    assert MAX_CALL_ID_CHARS == 256
    assert MAX_TOOL_NAME_CHARS == 512
    assert MAX_RAW_ARGUMENT_CHARS == 32 * 1024 * 1024
    assert MAX_STRING_CHARS == 5 * 1024 * 1024
    assert MAX_PATH_CHARS == 4096
    assert MAX_COMMAND_CHARS == 32_768
    assert MAX_PATTERN_CHARS == 4096
    assert MAX_GLOB_CHARS == 1024
    assert MAX_SUMMARY_CHARS == 64_000
    assert MAX_ARGUMENT_KEYS == 32
    assert MAX_NESTED_KEYS == 8
    assert MAX_NESTING_DEPTH == 4
    assert MAX_COLLECTION_ITEMS == 100
    assert MAX_INT == 2**31 - 1
    assert MIN_INT == -MAX_INT - 1
    assert MAX_BASH_TIMEOUT == 600
    assert MAX_BATCH_CALLS == 32
    assert MAX_DETAIL_CHARS == 200
    assert IDENTITY_VERSION == 1


def test_max_collection_items_matches_builtin_tools():
    from dirtywork.builtin_tools import MAX_APPLY_EDITS

    assert MAX_COLLECTION_ITEMS == MAX_APPLY_EDITS


def test_max_bash_timeout_matches_bash_spec_caps():
    from dirtywork.builtin_tools import BASH_SPEC

    assert MAX_BASH_TIMEOUT == BASH_SPEC.caps.timeout_max


def test_firewall_internal_error_is_not_a_value_error():
    assert not issubclass(FirewallInternalError, ValueError)
    assert issubclass(FirewallInternalError, Exception)


def _packages_list_from_pyproject() -> list[str]:
    path = os.path.join(os.path.dirname(__file__), "..", "pyproject.toml")
    try:
        import tomllib
    except ImportError:  # Python < 3.11: scan the one-line list instead
        with open(path, encoding="utf-8") as f:
            match = re.search(r"^packages\s*=\s*\[(.*?)\]", f.read(), re.M)
        assert match is not None
        return [part.strip().strip('"') for part in match.group(1).split(",") if part.strip()]
    with open(path, "rb") as f:
        return list(tomllib.load(f)["tool"]["setuptools"]["packages"])


def test_package_registration():
    packages = _packages_list_from_pyproject()
    assert "dirtywork.firewall" in packages
=== END tests/test_firewall_bounds.py ===

VERIFY: run python3 -m pytest -q -p no:cacheprovider tests/test_firewall_bounds.py and expect 5 passed. Then run python3 -m pytest -q -p no:cacheprovider with timeout=300 and expect all passed, 0 failed. Finish when both pass.
```

---
### Task 2: reason vocabularies

**Files:**
- Create: `dirtywork/firewall/reasons.py`
- Test: `tests/test_firewall_reasons.py`

**Interfaces:**
- Consumes: `FirewallInternalError` from Task 1
- Produces: `ReasonClass(str, enum.Enum)` with `MALFORMED`, `BOUNDS`, `AUTHORITY`, `INTERNAL`; `ReasonCode(str, enum.Enum)` with the 26 members of spec §7.2 (names uppercase, values lowercase); `reason_class(code: ReasonCode) -> ReasonClass`, total over members, raising `FirewallInternalError` for anything that is not a `ReasonCode` instance (a bare equal string included)

- [x] **Dry-run on the scratch clone** (2026-09-08, clone of `main` at `d3c7ce8`): the files below written, `tests/test_firewall_*.py` for this task 4 passed, full host suite 1718 passed (baseline 1709), `ast.parse(..., feature_version=(3, 9))` clean. The brief text is the reference: its file blocks are the dry-run files byte for byte (checked by a round-trip script).
- [ ] **Confirm the base.** `main` must be at the head that includes Task 1's PR. No line numbers in this brief; nothing to re-check.
- [ ] **Launch** the brief below verbatim through the invocation in the header, sampler on.
- [ ] **Review** against the gates in the header: `diff` every produced file against the brief's block (extract with the round-trip script or by eye); `files_changed` is exactly the brief's file list; host suite 1718 passed; `git diff --check` clean; 3.9 AST check silent.
- [ ] **Ledger row** in `docs/superpowers/bench/2026-09-XX-issue-135-w2-ledger.md` (status, turns, wall, tokens, tok/s, nudges, verdict, tool-call counts, sampler summary, diff-vs-brief result).
- [ ] **PR** `dirtywork/<slug>` → `main`, titled `feat(firewall): issue #135 W2 — reason vocabularies`, body naming the plan, the ledger and the spec; part 2 of 5 for issue #135 (does not close it). Wait for the owner's explicit merge go.

### Worker brief W2

```text
Issue #135 task W2 of 5 (Worker Action Firewall B): add dirtywork/firewall/reasons.py, the closed reason-class and reason-code vocabularies with a total reason_class() function, plus its test. The package (dirtywork/firewall/errors.py, bounds.py) already exists from task W1. Nothing outside the package imports it yet. Spec: docs/superpowers/specs/2026-09-08-issue-135-firewall-schema-design.md section 7 (the files below are its exact content).

Touch ONLY dirtywork/firewall/reasons.py, tests/test_firewall_reasons.py. Create each NEW file with ONE write_file call whose content is exactly the text between its BEGIN and END marker lines below (byte for byte; keep every blank line; the file ends with a newline after its last line; the marker lines themselves are not part of the file). No other files, no docs, no commits, nothing else.

FILE dirtywork/firewall/reasons.py (new, 89 lines) — write_file with exactly:
=== BEGIN dirtywork/firewall/reasons.py ===
"""The closed reason-class and reason-code vocabularies (spec §7)."""
from __future__ import annotations

import enum

from .errors import FirewallInternalError


class ReasonClass(str, enum.Enum):
    """The four buckets every ReasonCode belongs to, for Supervisor weighting."""

    MALFORMED = "malformed"
    BOUNDS = "bounds"
    AUTHORITY = "authority"
    INTERNAL = "internal"


class ReasonCode(str, enum.Enum):
    """Closed, append-only reason vocabulary; never renamed, never reused."""

    CALL_ID_INVALID = "call_id_invalid"
    TOOL_NAME_INVALID = "tool_name_invalid"
    TOOL_UNKNOWN = "tool_unknown"
    ARGUMENTS_UNPARSEABLE = "arguments_unparseable"
    ARGUMENTS_NOT_OBJECT = "arguments_not_object"
    ARGUMENT_MISSING = "argument_missing"
    ARGUMENT_TYPE_INVALID = "argument_type_invalid"
    ARGUMENT_UNEXPECTED = "argument_unexpected"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    STRING_TOO_LONG = "string_too_long"
    COLLECTION_TOO_LARGE = "collection_too_large"
    NESTING_TOO_DEEP = "nesting_too_deep"
    NUMBER_OUT_OF_RANGE = "number_out_of_range"
    BATCH_TOO_LARGE = "batch_too_large"
    CALL_ID_DUPLICATE = "call_id_duplicate"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    REPO_PUBLISH = "repo_publish"
    REPO_CONTROL = "repo_control"
    HOST_FS_DESTRUCTIVE = "host_fs_destructive"
    REMOTE_CODE_EXEC = "remote_code_exec"
    SYSTEM_CONTROL = "system_control"
    HOST_FS_REDIRECT = "host_fs_redirect"
    HOST_FS_CHDIR = "host_fs_chdir"
    REPO_METADATA_TARGET = "repo_metadata_target"
    PATH_OUTSIDE_WORKSPACE = "path_outside_workspace"
    FIREWALL_INTERNAL_ERROR = "firewall_internal_error"


_REASON_CLASS_BY_CODE = {
    ReasonCode.CALL_ID_INVALID: ReasonClass.MALFORMED,
    ReasonCode.TOOL_NAME_INVALID: ReasonClass.MALFORMED,
    ReasonCode.TOOL_UNKNOWN: ReasonClass.MALFORMED,
    ReasonCode.ARGUMENTS_UNPARSEABLE: ReasonClass.MALFORMED,
    ReasonCode.ARGUMENTS_NOT_OBJECT: ReasonClass.MALFORMED,
    ReasonCode.ARGUMENT_MISSING: ReasonClass.MALFORMED,
    ReasonCode.ARGUMENT_TYPE_INVALID: ReasonClass.MALFORMED,
    ReasonCode.ARGUMENT_UNEXPECTED: ReasonClass.MALFORMED,
    ReasonCode.PAYLOAD_TOO_LARGE: ReasonClass.BOUNDS,
    ReasonCode.STRING_TOO_LONG: ReasonClass.BOUNDS,
    ReasonCode.COLLECTION_TOO_LARGE: ReasonClass.BOUNDS,
    ReasonCode.NESTING_TOO_DEEP: ReasonClass.BOUNDS,
    ReasonCode.NUMBER_OUT_OF_RANGE: ReasonClass.BOUNDS,
    ReasonCode.BATCH_TOO_LARGE: ReasonClass.BOUNDS,
    ReasonCode.CALL_ID_DUPLICATE: ReasonClass.BOUNDS,
    ReasonCode.PRIVILEGE_ESCALATION: ReasonClass.AUTHORITY,
    ReasonCode.REPO_PUBLISH: ReasonClass.AUTHORITY,
    ReasonCode.REPO_CONTROL: ReasonClass.AUTHORITY,
    ReasonCode.HOST_FS_DESTRUCTIVE: ReasonClass.AUTHORITY,
    ReasonCode.REMOTE_CODE_EXEC: ReasonClass.AUTHORITY,
    ReasonCode.SYSTEM_CONTROL: ReasonClass.AUTHORITY,
    ReasonCode.HOST_FS_REDIRECT: ReasonClass.AUTHORITY,
    ReasonCode.HOST_FS_CHDIR: ReasonClass.AUTHORITY,
    ReasonCode.REPO_METADATA_TARGET: ReasonClass.AUTHORITY,
    ReasonCode.PATH_OUTSIDE_WORKSPACE: ReasonClass.AUTHORITY,
    ReasonCode.FIREWALL_INTERNAL_ERROR: ReasonClass.INTERNAL,
}


def reason_class(code: ReasonCode) -> ReasonClass:
    """Total function from ReasonCode to its ReasonClass; fails closed.

    Checks membership by identity, not value equality: a str enum member
    hashes and compares equal to a plain str of the same value, so a
    look-alike string must be rejected explicitly rather than trusted to a
    dict lookup.
    """
    if not isinstance(code, ReasonCode):
        raise FirewallInternalError(f"not a ReasonCode: {code!r}")
    return _REASON_CLASS_BY_CODE[code]
=== END dirtywork/firewall/reasons.py ===

FILE tests/test_firewall_reasons.py (new, 106 lines) — write_file with exactly:
=== BEGIN tests/test_firewall_reasons.py ===
from __future__ import annotations

import pytest

from dirtywork.firewall.errors import FirewallInternalError
from dirtywork.firewall.reasons import ReasonClass, ReasonCode, reason_class

EXPECTED_REASON_CLASSES = ["authority", "bounds", "internal", "malformed"]

EXPECTED_REASON_CODES = sorted(
    [
        "call_id_invalid",
        "tool_name_invalid",
        "tool_unknown",
        "arguments_unparseable",
        "arguments_not_object",
        "argument_missing",
        "argument_type_invalid",
        "argument_unexpected",
        "payload_too_large",
        "string_too_long",
        "collection_too_large",
        "nesting_too_deep",
        "number_out_of_range",
        "batch_too_large",
        "call_id_duplicate",
        "privilege_escalation",
        "repo_publish",
        "repo_control",
        "host_fs_destructive",
        "remote_code_exec",
        "system_control",
        "host_fs_redirect",
        "host_fs_chdir",
        "repo_metadata_target",
        "path_outside_workspace",
        "firewall_internal_error",
    ]
)

MALFORMED = [
    ReasonCode.CALL_ID_INVALID,
    ReasonCode.TOOL_NAME_INVALID,
    ReasonCode.TOOL_UNKNOWN,
    ReasonCode.ARGUMENTS_UNPARSEABLE,
    ReasonCode.ARGUMENTS_NOT_OBJECT,
    ReasonCode.ARGUMENT_MISSING,
    ReasonCode.ARGUMENT_TYPE_INVALID,
    ReasonCode.ARGUMENT_UNEXPECTED,
]
BOUNDS = [
    ReasonCode.PAYLOAD_TOO_LARGE,
    ReasonCode.STRING_TOO_LONG,
    ReasonCode.COLLECTION_TOO_LARGE,
    ReasonCode.NESTING_TOO_DEEP,
    ReasonCode.NUMBER_OUT_OF_RANGE,
    ReasonCode.BATCH_TOO_LARGE,
    ReasonCode.CALL_ID_DUPLICATE,
]
AUTHORITY = [
    ReasonCode.PRIVILEGE_ESCALATION,
    ReasonCode.REPO_PUBLISH,
    ReasonCode.REPO_CONTROL,
    ReasonCode.HOST_FS_DESTRUCTIVE,
    ReasonCode.REMOTE_CODE_EXEC,
    ReasonCode.SYSTEM_CONTROL,
    ReasonCode.HOST_FS_REDIRECT,
    ReasonCode.HOST_FS_CHDIR,
    ReasonCode.REPO_METADATA_TARGET,
    ReasonCode.PATH_OUTSIDE_WORKSPACE,
]
INTERNAL = [ReasonCode.FIREWALL_INTERNAL_ERROR]


def test_reason_class_vocabulary_pin():
    assert sorted(m.value for m in ReasonClass) == EXPECTED_REASON_CLASSES


def test_reason_code_vocabulary_pin():
    assert sorted(m.value for m in ReasonCode) == EXPECTED_REASON_CODES
    assert len(ReasonCode) == 26


def test_reason_class_totality_and_counts():
    assert len(MALFORMED) == 8
    assert len(BOUNDS) == 7
    assert len(AUTHORITY) == 10
    assert len(INTERNAL) == 1
    assert len(MALFORMED) + len(BOUNDS) + len(AUTHORITY) + len(INTERNAL) == len(ReasonCode)

    for code in ReasonCode:
        assert code in MALFORMED + BOUNDS + AUTHORITY + INTERNAL

    for code in MALFORMED:
        assert reason_class(code) is ReasonClass.MALFORMED
    for code in BOUNDS:
        assert reason_class(code) is ReasonClass.BOUNDS
    for code in AUTHORITY:
        assert reason_class(code) is ReasonClass.AUTHORITY
    for code in INTERNAL:
        assert reason_class(code) is ReasonClass.INTERNAL


def test_reason_class_rejects_bare_str_lookalike():
    with pytest.raises(FirewallInternalError):
        reason_class("tool_unknown")
=== END tests/test_firewall_reasons.py ===

VERIFY: run python3 -m pytest -q -p no:cacheprovider tests/test_firewall_reasons.py and expect 4 passed. Then run python3 -m pytest -q -p no:cacheprovider with timeout=300 and expect all passed, 0 failed. Finish when both pass.
```

---
### Task 3: action kinds, capabilities and the checked-in tables

**Files:**
- Create: `dirtywork/firewall/capabilities.py`
- Test: `tests/test_firewall_capabilities.py`

**Interfaces:**
- Consumes: `ReasonCode` from Task 2; the test reads `dirtywork.builtin_tools.BUILTIN_SPECS` and `dirtywork.guardrails._RULES` for the lockstep pins
- Produces: `ActionKind(str, enum.Enum)` (11 members in `BUILTIN_SPECS` order, values are tool names); `Capability(str, enum.Enum)` (11 members of spec §5.1); `BASE_CAPABILITIES: dict[ActionKind, frozenset[Capability]]`; `LEGACY_RULES: tuple[tuple[int, Capability, ReasonCode], ...]` (8 entries, index 0..7 in `_RULES` order); `FILE_TARGET_RULES: tuple[tuple[Capability, ReasonCode], ...]` (2 entries)

- [x] **Dry-run on the scratch clone** (2026-09-08, clone of `main` at `d3c7ce8`): the files below written, `tests/test_firewall_*.py` for this task 7 passed, full host suite 1725 passed (baseline 1709), `ast.parse(..., feature_version=(3, 9))` clean. The brief text is the reference: its file blocks are the dry-run files byte for byte (checked by a round-trip script).
- [ ] **Confirm the base.** `main` must be at the head that includes Task 2's PR. No line numbers in this brief; nothing to re-check.
- [ ] **Launch** the brief below verbatim through the invocation in the header, sampler on.
- [ ] **Review** against the gates in the header: `diff` every produced file against the brief's block (extract with the round-trip script or by eye); `files_changed` is exactly the brief's file list; host suite 1725 passed; `git diff --check` clean; 3.9 AST check silent.
- [ ] **Ledger row** in `docs/superpowers/bench/2026-09-XX-issue-135-w3-ledger.md` (status, turns, wall, tokens, tok/s, nudges, verdict, tool-call counts, sampler summary, diff-vs-brief result).
- [ ] **PR** `dirtywork/<slug>` → `main`, titled `feat(firewall): issue #135 W3 — action kinds, capabilities and the checked-in tables`, body naming the plan, the ledger and the spec; part 3 of 5 for issue #135 (does not close it). Wait for the owner's explicit merge go.

### Worker brief W3

```text
Issue #135 task W3 of 5 (Worker Action Firewall B): add dirtywork/firewall/capabilities.py, the closed ActionKind and Capability vocabularies and the checked-in tables mapping every built-in tool, every ordered guardrail rule and both file-target refusals to them, plus its test. dirtywork/firewall/reasons.py already exists from task W2. Nothing outside the package imports it yet. Spec: docs/superpowers/specs/2026-09-08-issue-135-firewall-schema-design.md sections 4.1 and 5 (the files below are its exact content).

Touch ONLY dirtywork/firewall/capabilities.py, tests/test_firewall_capabilities.py. Create each NEW file with ONE write_file call whose content is exactly the text between its BEGIN and END marker lines below (byte for byte; keep every blank line; the file ends with a newline after its last line; the marker lines themselves are not part of the file). No other files, no docs, no commits, nothing else.

FILE dirtywork/firewall/capabilities.py (new, 72 lines) — write_file with exactly:
=== BEGIN dirtywork/firewall/capabilities.py ===
"""Closed action-kind and capability vocabularies and the checked-in tables
mapping them (spec §4.1, §5)."""
from __future__ import annotations

import enum

from .reasons import ReasonCode


class ActionKind(str, enum.Enum):
    """One member per built-in tool; values are the registered tool names."""

    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    APPEND_FILE = "append_file"
    EDIT_FILE = "edit_file"
    APPLY_EDITS = "apply_edits"
    INSERT_BEFORE = "insert_before"
    INSERT_AFTER = "insert_after"
    LIST_DIR = "list_dir"
    GREP = "grep"
    BASH = "bash"
    FINISH = "finish"


class Capability(str, enum.Enum):
    """Closed set of authority names an action can require or be granted."""

    WORKSPACE_READ = "workspace_read"
    WORKSPACE_WRITE = "workspace_write"
    SHELL = "shell"
    RUN_CONTROL = "run_control"
    REPO_CONTROL = "repo_control"
    REPO_PUBLISH = "repo_publish"
    HOST_FS = "host_fs"
    PRIVILEGE = "privilege"
    SYSTEM_CONTROL = "system_control"
    NETWORK = "network"
    REMOTE_CODE_EXEC = "remote_code_exec"


BASE_CAPABILITIES: dict[ActionKind, frozenset[Capability]] = {
    ActionKind.READ_FILE: frozenset({Capability.WORKSPACE_READ}),
    ActionKind.LIST_DIR: frozenset({Capability.WORKSPACE_READ}),
    ActionKind.GREP: frozenset({Capability.WORKSPACE_READ}),
    ActionKind.WRITE_FILE: frozenset({Capability.WORKSPACE_WRITE}),
    ActionKind.APPEND_FILE: frozenset({Capability.WORKSPACE_WRITE}),
    ActionKind.EDIT_FILE: frozenset({Capability.WORKSPACE_WRITE}),
    ActionKind.APPLY_EDITS: frozenset({Capability.WORKSPACE_WRITE}),
    ActionKind.INSERT_BEFORE: frozenset({Capability.WORKSPACE_WRITE}),
    ActionKind.INSERT_AFTER: frozenset({Capability.WORKSPACE_WRITE}),
    ActionKind.BASH: frozenset({Capability.SHELL}),
    ActionKind.FINISH: frozenset({Capability.RUN_CONTROL}),
}

# (index, capability, reason_code) in dirtywork.guardrails._RULES order.
LEGACY_RULES: tuple = (
    (0, Capability.PRIVILEGE, ReasonCode.PRIVILEGE_ESCALATION),
    (1, Capability.REPO_PUBLISH, ReasonCode.REPO_PUBLISH),
    (2, Capability.REPO_CONTROL, ReasonCode.REPO_CONTROL),
    (3, Capability.HOST_FS, ReasonCode.HOST_FS_DESTRUCTIVE),
    (4, Capability.REMOTE_CODE_EXEC, ReasonCode.REMOTE_CODE_EXEC),
    (5, Capability.SYSTEM_CONTROL, ReasonCode.SYSTEM_CONTROL),
    (6, Capability.HOST_FS, ReasonCode.HOST_FS_REDIRECT),
    (7, Capability.HOST_FS, ReasonCode.HOST_FS_CHDIR),
)

# (capability, reason_code) for the two file-tool refusals the backends make today.
FILE_TARGET_RULES: tuple = (
    (Capability.REPO_CONTROL, ReasonCode.REPO_METADATA_TARGET),
    (Capability.HOST_FS, ReasonCode.PATH_OUTSIDE_WORKSPACE),
)
=== END dirtywork/firewall/capabilities.py ===

FILE tests/test_firewall_capabilities.py (new, 106 lines) — write_file with exactly:
=== BEGIN tests/test_firewall_capabilities.py ===
from __future__ import annotations

from dirtywork.firewall.capabilities import (
    BASE_CAPABILITIES,
    FILE_TARGET_RULES,
    ActionKind,
    Capability,
    LEGACY_RULES,
)
from dirtywork.firewall.reasons import ReasonCode

EXPECTED_ACTION_KINDS = sorted(
    [
        "read_file",
        "write_file",
        "append_file",
        "edit_file",
        "apply_edits",
        "insert_before",
        "insert_after",
        "list_dir",
        "grep",
        "bash",
        "finish",
    ]
)

EXPECTED_CAPABILITIES = sorted(
    [
        "workspace_read",
        "workspace_write",
        "shell",
        "run_control",
        "repo_control",
        "repo_publish",
        "host_fs",
        "privilege",
        "system_control",
        "network",
        "remote_code_exec",
    ]
)

# (index, capability, reason_code) from spec §5.3, in guardrails._RULES order.
EXPECTED_LEGACY_RULES = (
    (0, Capability.PRIVILEGE, ReasonCode.PRIVILEGE_ESCALATION),
    (1, Capability.REPO_PUBLISH, ReasonCode.REPO_PUBLISH),
    (2, Capability.REPO_CONTROL, ReasonCode.REPO_CONTROL),
    (3, Capability.HOST_FS, ReasonCode.HOST_FS_DESTRUCTIVE),
    (4, Capability.REMOTE_CODE_EXEC, ReasonCode.REMOTE_CODE_EXEC),
    (5, Capability.SYSTEM_CONTROL, ReasonCode.SYSTEM_CONTROL),
    (6, Capability.HOST_FS, ReasonCode.HOST_FS_REDIRECT),
    (7, Capability.HOST_FS, ReasonCode.HOST_FS_CHDIR),
)


def test_action_kind_vocabulary_pin():
    assert sorted(m.value for m in ActionKind) == EXPECTED_ACTION_KINDS


def test_capability_vocabulary_pin():
    assert sorted(m.value for m in Capability) == EXPECTED_CAPABILITIES


def test_action_kind_lockstep_with_registry():
    from dirtywork.builtin_tools import BUILTIN_SPECS

    assert [k.value for k in ActionKind] == [s.name for s in BUILTIN_SPECS]


def test_legacy_rules_lockstep_with_guardrails():
    from dirtywork.guardrails import _RULES

    assert len(LEGACY_RULES) == len(_RULES)
    assert [t[0] for t in LEGACY_RULES] == list(range(8))
    assert LEGACY_RULES == EXPECTED_LEGACY_RULES

    reason_codes = [t[2] for t in LEGACY_RULES]
    assert len(set(reason_codes)) == len(reason_codes) == 8


def test_file_target_rules_pin():
    assert FILE_TARGET_RULES == (
        (Capability.REPO_CONTROL, ReasonCode.REPO_METADATA_TARGET),
        (Capability.HOST_FS, ReasonCode.PATH_OUTSIDE_WORKSPACE),
    )


def test_base_capabilities_totality():
    for kind in ActionKind:
        assert kind in BASE_CAPABILITIES
        assert len(BASE_CAPABILITIES[kind]) > 0


def test_every_capability_is_accounted_for():
    covered = set()
    for caps in BASE_CAPABILITIES.values():
        covered |= caps
    for _, cap, _ in LEGACY_RULES:
        covered.add(cap)
    for cap, _ in FILE_TARGET_RULES:
        covered.add(cap)
    covered.add(Capability.NETWORK)

    for cap in Capability:
        assert cap in covered
=== END tests/test_firewall_capabilities.py ===

VERIFY: run python3 -m pytest -q -p no:cacheprovider tests/test_firewall_capabilities.py and expect 7 passed. Then run python3 -m pytest -q -p no:cacheprovider with timeout=300 and expect all passed, 0 failed. Finish when both pass.
```

---
### Task 4: schema: request, canonical action, decision, identity, event

**Files:**
- Create: `dirtywork/firewall/schema.py`
- Test: `tests/test_firewall_schema.py`

**Review fold-in (2026-09-08):** the identity digest encodes with `surrogatepass`, so a raw tool name carrying a lone surrogate still gets a rejection identity instead of a `UnicodeEncodeError` (regression test in group 14). `rejection_identity` is documented as a coarse audit key: request-stage events count toward total denial volume only, never toward exact-equivalent tracking (spec §9.2).

**Interfaces:**
- Consumes: `FirewallInternalError`, every `bounds` constant, `ReasonClass`/`ReasonCode`/`reason_class`, `ActionKind`/`Capability`/`BASE_CAPABILITIES` from Tasks 1–3
- Produces: `ActionRequest` (frozen; `from_tool_call(tc, *, turn, batch_index, batch_size)` classmethod, duck-typed on `tc.id/.name/.arguments/.error/.raw_arguments`); `valid_call_id(value) -> bool`; private helpers `_str_field`, `_int_field`, `_tuple_field`; `Edit`, `ReadFileArgs`, `WriteFileArgs`, `AppendFileArgs`, `EditFileArgs`, `ApplyEditsArgs`, `InsertArgs`, `ListDirArgs`, `GrepArgs`, `BashArgs`, `FinishArgs` (each with `__post_init__` bounds, raising `FirewallInternalError`); `CanonicalArgs`; `ARGS_FOR_KIND`; `Decision`, `SemanticStatus`; `CanonicalAction`; `PolicyDecision`; `IDENTITY_FIELDS`; `action_identity(action) -> str`; `rejection_identity(request, rejection) -> str` (duck-typed on `rejection.reason_code`); `FirewallEvent` with `from_action`, `from_rejection`, `to_dict`. Pinned identity of the reference action: `3fcfda5eb96c0f0052810597adeb47a8f7d16cfe7bc46a29456e660ff74af783`

- [x] **Dry-run on the scratch clone** (2026-09-08, clone of `main` at `d3c7ce8`): the files below written, `tests/test_firewall_*.py` for this task 47 passed, full host suite 1772 passed (baseline 1709), `ast.parse(..., feature_version=(3, 9))` clean. The brief text is the reference: its file blocks are the dry-run files byte for byte (checked by a round-trip script).
- [ ] **Confirm the base.** `main` must be at the head that includes Task 3's PR. No line numbers in this brief; nothing to re-check.
- [ ] **Launch** the brief below verbatim through the invocation in the header, sampler on.
- [ ] **Review** against the gates in the header: `diff` every produced file against the brief's block (extract with the round-trip script or by eye); `files_changed` is exactly the brief's file list; host suite 1772 passed; `git diff --check` clean; 3.9 AST check silent.
- [ ] This is the largest brief (31 KB). If the worker's `write_file` of `schema.py` lands truncated, do not resume; rerun fresh from `main` with the same brief (see the brief-recipe note on resume hallucination).
- [ ] **Ledger row** in `docs/superpowers/bench/2026-09-XX-issue-135-w4-ledger.md` (status, turns, wall, tokens, tok/s, nudges, verdict, tool-call counts, sampler summary, diff-vs-brief result).
- [ ] **PR** `dirtywork/<slug>` → `main`, titled `feat(firewall): issue #135 W4 — schema: request, canonical action, decision, identity, event`, body naming the plan, the ledger and the spec; part 4 of 5 for issue #135 (does not close it). Wait for the owner's explicit merge go.

### Worker brief W4

```text
Issue #135 task W4 of 5 (Worker Action Firewall B): add dirtywork/firewall/schema.py, the boundary ActionRequest, the ten closed per-tool argument classes, CanonicalAction, Decision, SemanticStatus, PolicyDecision, the identity functions and the versioned FirewallEvent, plus its test. dirtywork/firewall/errors.py, bounds.py, reasons.py and capabilities.py already exist from tasks W1-W3. Nothing outside the package imports it yet. Spec: docs/superpowers/specs/2026-09-08-issue-135-firewall-schema-design.md sections 3, 4, 8 and 9 (the files below are its exact content).

Touch ONLY dirtywork/firewall/schema.py, tests/test_firewall_schema.py. Create each NEW file with ONE write_file call whose content is exactly the text between its BEGIN and END marker lines below (byte for byte; keep every blank line; the file ends with a newline after its last line; the marker lines themselves are not part of the file). No other files, no docs, no commits, nothing else.

FILE dirtywork/firewall/schema.py (new, 447 lines) — write_file with exactly:
=== BEGIN dirtywork/firewall/schema.py ===
"""ActionRequest, the canonical action shapes, and the Firewall's identity
and event types (spec §3, §4, §8, §9)."""
from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional, Union

from .bounds import (
    FIREWALL_SCHEMA_VERSION,
    IDENTITY_VERSION,
    MAX_BASH_TIMEOUT,
    MAX_CALL_ID_CHARS,
    MAX_COLLECTION_ITEMS,
    MAX_COMMAND_CHARS,
    MAX_DETAIL_CHARS,
    MAX_GLOB_CHARS,
    MAX_INT,
    MAX_PATH_CHARS,
    MAX_PATTERN_CHARS,
    MAX_STRING_CHARS,
    MAX_SUMMARY_CHARS,
    MAX_TOOL_NAME_CHARS,
)
from .capabilities import ActionKind, Capability, BASE_CAPABILITIES
from .errors import FirewallInternalError
from .reasons import ReasonClass, ReasonCode, reason_class


@dataclass(frozen=True)
class ActionRequest:
    """The only way worker input enters the Firewall (spec §3)."""

    call_id: str
    tool_name: str
    arguments: Any
    parse_error: Optional[str]
    raw_chars: int
    turn: int
    batch_index: int
    batch_size: int

    @classmethod
    def from_tool_call(cls, tc, *, turn, batch_index, batch_size) -> "ActionRequest":
        """Copies the ToolCall fields verbatim; performs no validation."""
        return cls(
            call_id=tc.id,
            tool_name=tc.name,
            arguments=tc.arguments,
            parse_error=tc.error,
            raw_chars=len(tc.raw_arguments or ""),
            turn=turn,
            batch_index=batch_index,
            batch_size=batch_size,
        )


def valid_call_id(value: Any) -> bool:
    """Check 2 of spec §6.2: nonempty str, <= MAX_CALL_ID_CHARS, printable
    ASCII with no whitespace (every char in 0x21..0x7e). Reused by request.py."""
    if not isinstance(value, str) or not value or len(value) > MAX_CALL_ID_CHARS:
        return False
    return all(0x21 <= ord(ch) <= 0x7E for ch in value)


def _str_field(value: Any, name: str, limit: int, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str):
        raise FirewallInternalError(f"{name} must be a str")
    if len(value) > limit:
        raise FirewallInternalError(f"{name} exceeds {limit} chars")


def _int_field(value: Any, name: str, lo: int, hi: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FirewallInternalError(f"{name} must be an int")
    if not (lo <= value <= hi):
        raise FirewallInternalError(f"{name} must be in [{lo}, {hi}]")


def _tuple_field(value: Any, name: str, item_type: type, lo: int, hi: int) -> None:
    if not isinstance(value, tuple):
        raise FirewallInternalError(f"{name} must be a tuple")
    if not (lo <= len(value) <= hi):
        raise FirewallInternalError(f"{name} must have {lo}..{hi} items")
    for item in value:
        if not isinstance(item, item_type):
            raise FirewallInternalError(f"{name} items must be {item_type.__name__}")


@dataclass(frozen=True)
class Edit:
    old: str
    new: str

    def __post_init__(self) -> None:
        _str_field(self.old, "old", MAX_STRING_CHARS)
        if not self.old:
            raise FirewallInternalError("Edit.old must be nonempty")
        _str_field(self.new, "new", MAX_STRING_CHARS)


@dataclass(frozen=True)
class ReadFileArgs:
    path: str
    offset: int
    limit: int

    def __post_init__(self) -> None:
        _str_field(self.path, "path", MAX_PATH_CHARS)
        _int_field(self.offset, "offset", 0, MAX_INT)
        _int_field(self.limit, "limit", 1, MAX_INT)


@dataclass(frozen=True)
class WriteFileArgs:
    path: str
    content: str

    def __post_init__(self) -> None:
        _str_field(self.path, "path", MAX_PATH_CHARS)
        _str_field(self.content, "content", MAX_STRING_CHARS)


@dataclass(frozen=True)
class AppendFileArgs:
    path: str
    text: str

    def __post_init__(self) -> None:
        _str_field(self.path, "path", MAX_PATH_CHARS)
        _str_field(self.text, "text", MAX_STRING_CHARS)


@dataclass(frozen=True)
class EditFileArgs:
    path: str
    old_string: str
    new_string: str

    def __post_init__(self) -> None:
        _str_field(self.path, "path", MAX_PATH_CHARS)
        _str_field(self.old_string, "old_string", MAX_STRING_CHARS)
        _str_field(self.new_string, "new_string", MAX_STRING_CHARS)


@dataclass(frozen=True)
class ApplyEditsArgs:
    path: str
    edits: "tuple[Edit, ...]"

    def __post_init__(self) -> None:
        _str_field(self.path, "path", MAX_PATH_CHARS)
        _tuple_field(self.edits, "edits", Edit, 1, MAX_COLLECTION_ITEMS)


@dataclass(frozen=True)
class InsertArgs:
    path: str
    anchor: str
    text: str

    def __post_init__(self) -> None:
        _str_field(self.path, "path", MAX_PATH_CHARS)
        _str_field(self.anchor, "anchor", MAX_STRING_CHARS)
        _str_field(self.text, "text", MAX_STRING_CHARS)


@dataclass(frozen=True)
class ListDirArgs:
    path: str

    def __post_init__(self) -> None:
        _str_field(self.path, "path", MAX_PATH_CHARS)


@dataclass(frozen=True)
class GrepArgs:
    pattern: str
    path: str
    glob: Optional[str]

    def __post_init__(self) -> None:
        _str_field(self.pattern, "pattern", MAX_PATTERN_CHARS)
        _str_field(self.path, "path", MAX_PATH_CHARS)
        _str_field(self.glob, "glob", MAX_GLOB_CHARS, nullable=True)


@dataclass(frozen=True)
class BashArgs:
    command: str
    timeout: int

    def __post_init__(self) -> None:
        _str_field(self.command, "command", MAX_COMMAND_CHARS)
        _int_field(self.timeout, "timeout", 1, MAX_BASH_TIMEOUT)


@dataclass(frozen=True)
class FinishArgs:
    summary: str

    def __post_init__(self) -> None:
        _str_field(self.summary, "summary", MAX_SUMMARY_CHARS)


CanonicalArgs = Union[
    ReadFileArgs,
    WriteFileArgs,
    AppendFileArgs,
    EditFileArgs,
    ApplyEditsArgs,
    InsertArgs,
    ListDirArgs,
    GrepArgs,
    BashArgs,
    FinishArgs,
]

ARGS_FOR_KIND: "dict[ActionKind, type]" = {
    ActionKind.READ_FILE: ReadFileArgs,
    ActionKind.WRITE_FILE: WriteFileArgs,
    ActionKind.APPEND_FILE: AppendFileArgs,
    ActionKind.EDIT_FILE: EditFileArgs,
    ActionKind.APPLY_EDITS: ApplyEditsArgs,
    ActionKind.INSERT_BEFORE: InsertArgs,
    ActionKind.INSERT_AFTER: InsertArgs,
    ActionKind.LIST_DIR: ListDirArgs,
    ActionKind.GREP: GrepArgs,
    ActionKind.BASH: BashArgs,
    ActionKind.FINISH: FinishArgs,
}


class Decision(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"


class SemanticStatus(str, enum.Enum):
    KNOWN = "semantic_known"
    UNKNOWN = "semantic_unknown"


@dataclass(frozen=True)
class CanonicalAction:
    """The immutable, policy-relevant canonicalization of one worker action
    (spec §4.3)."""

    schema_version: int
    call_id: str
    turn: int
    kind: ActionKind
    args: CanonicalArgs
    capabilities: "frozenset[Capability]"
    semantic_status: SemanticStatus

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ActionKind):
            raise FirewallInternalError("kind must be an ActionKind member")
        if not isinstance(self.semantic_status, SemanticStatus):
            raise FirewallInternalError("semantic_status must be a SemanticStatus member")
        if type(self.args) is not ARGS_FOR_KIND[self.kind]:
            raise FirewallInternalError("args type does not match kind")
        if not valid_call_id(self.call_id):
            raise FirewallInternalError("call_id invalid")
        _int_field(self.turn, "turn", 1, MAX_INT)
        if not isinstance(self.capabilities, frozenset) or not self.capabilities:
            raise FirewallInternalError("capabilities must be a nonempty frozenset")
        for cap in self.capabilities:
            if not isinstance(cap, Capability):
                raise FirewallInternalError("capabilities members must be Capability")
        if not BASE_CAPABILITIES[self.kind] <= self.capabilities:
            raise FirewallInternalError("capabilities missing the kind's base set")
        if self.schema_version != FIREWALL_SCHEMA_VERSION:
            raise FirewallInternalError("schema_version mismatch")


@dataclass(frozen=True)
class PolicyDecision:
    """The Firewall's decision on one action or rejection (spec §8)."""

    decision: Decision
    reason_code: Optional[ReasonCode]
    detail: str

    def __post_init__(self) -> None:
        allow_ok = self.decision is Decision.ALLOW and self.reason_code is None
        deny_ok = self.decision is Decision.DENY and isinstance(self.reason_code, ReasonCode)
        if not (allow_ok or deny_ok):
            raise FirewallInternalError(
                "PolicyDecision must be ALLOW+None or DENY+ReasonCode"
            )
        _str_field(self.detail, "detail", MAX_DETAIL_CHARS)


IDENTITY_FIELDS: "dict[ActionKind, tuple]" = {
    ActionKind.READ_FILE: ("path",),
    ActionKind.WRITE_FILE: ("path",),
    ActionKind.APPEND_FILE: ("path",),
    ActionKind.EDIT_FILE: ("path",),
    ActionKind.APPLY_EDITS: ("path",),
    ActionKind.INSERT_BEFORE: ("path",),
    ActionKind.INSERT_AFTER: ("path",),
    ActionKind.LIST_DIR: ("path",),
    ActionKind.GREP: ("pattern", "path", "glob"),
    ActionKind.BASH: ("command",),
    ActionKind.FINISH: (),
}


def _digest(lines: list) -> str:
    """`surrogatepass` keeps hashing total: a raw tool name may carry a lone
    surrogate that strict UTF-8 refuses to encode, and a rejection must still
    get an identity."""
    text = f"dirtywork-firewall-identity/{IDENTITY_VERSION}\n" + "".join(line + "\n" for line in lines)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def action_identity(action: CanonicalAction) -> str:
    """SHA-256 hex of the versioned, policy-relevant identity of an action
    (spec §9.1)."""
    lines = [action.kind.value]
    for field_name in IDENTITY_FIELDS[action.kind]:
        value = getattr(action.args, field_name)
        lines.append(f"{field_name}={_json(value)}")
    return _digest(lines)


def rejection_identity(request: ActionRequest, rejection: Any) -> str:
    """SHA-256 hex identity of a request-stage rejection (spec §9.1).

    Coarse by design: only the reason code and the raw tool name are hashed,
    because a rejected request has no canonical target and parsing its
    arguments for one would be a second, untrusted normalization. It is an
    audit key, not an exact-equivalent denial key; request-stage events
    count toward total denial volume only (spec §9.2).

    `rejection` is duck-typed (only `.reason_code` is used) because
    `Rejection` lives in request.py, which imports this module.
    """
    reason_code = getattr(rejection, "reason_code", None)
    if not isinstance(reason_code, ReasonCode):
        raise FirewallInternalError("rejection.reason_code must be a ReasonCode")
    name = request.tool_name
    if not (isinstance(name, str) and 0 < len(name) <= MAX_TOOL_NAME_CHARS):
        name = ""
    return _digest(["rejection", reason_code.value, name])


@dataclass(frozen=True)
class FirewallEvent:
    """One event shape covering both a request-stage rejection and an
    action-stage decision (spec §9.2)."""

    schema_version: int
    stage: str
    turn: int
    call_id: str
    kind: Optional[ActionKind]
    capabilities: "tuple[str, ...]"
    decision: Decision
    reason_code: Optional[ReasonCode]
    reason_class: Optional[ReasonClass]
    action_identity: str
    semantic_status: Optional[SemanticStatus]

    def __post_init__(self) -> None:
        if self.stage == "request":
            if self.decision is not Decision.DENY:
                raise FirewallInternalError("request-stage event must be DENY")
            if self.kind is not None:
                raise FirewallInternalError("request-stage event must have kind=None")
            if self.capabilities != ():
                raise FirewallInternalError("request-stage event must have capabilities=()")
            if self.semantic_status is not None:
                raise FirewallInternalError("request-stage event must have semantic_status=None")
        elif self.stage == "action":
            if self.kind is None or self.semantic_status is None:
                raise FirewallInternalError("action-stage event requires kind and semantic_status")
            if not self.capabilities:
                raise FirewallInternalError("action-stage event requires nonempty capabilities")
        else:
            raise FirewallInternalError("stage must be 'request' or 'action'")

        allow = self.decision is Decision.ALLOW
        if allow != (self.reason_code is None):
            raise FirewallInternalError("reason_code is None iff decision is ALLOW")
        if allow != (self.reason_class is None):
            raise FirewallInternalError("reason_class is None iff decision is ALLOW")

    @classmethod
    def from_action(cls, action: CanonicalAction, policy: PolicyDecision) -> "FirewallEvent":
        return cls(
            schema_version=FIREWALL_SCHEMA_VERSION,
            stage="action",
            turn=action.turn,
            call_id=action.call_id,
            kind=action.kind,
            capabilities=tuple(sorted(c.value for c in action.capabilities)),
            decision=policy.decision,
            reason_code=policy.reason_code,
            reason_class=(
                None if policy.reason_code is None else reason_class(policy.reason_code)
            ),
            action_identity=action_identity(action),
            semantic_status=action.semantic_status,
        )

    @classmethod
    def from_rejection(cls, request: ActionRequest, rejection: Any) -> "FirewallEvent":
        reason_code = rejection.reason_code
        return cls(
            schema_version=FIREWALL_SCHEMA_VERSION,
            stage="request",
            turn=request.turn,
            call_id=request.call_id if valid_call_id(request.call_id) else "",
            kind=None,
            capabilities=(),
            decision=Decision.DENY,
            reason_code=reason_code,
            reason_class=reason_class(reason_code),
            action_identity=rejection_identity(request, rejection),
            semantic_status=None,
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "turn": self.turn,
            "call_id": self.call_id,
            "kind": None if self.kind is None else self.kind.value,
            "capabilities": sorted(self.capabilities),
            "decision": self.decision.value,
            "reason_code": None if self.reason_code is None else self.reason_code.value,
            "reason_class": None if self.reason_class is None else self.reason_class.value,
            "action_identity": self.action_identity,
            "semantic_status": None if self.semantic_status is None else self.semantic_status.value,
        }
=== END dirtywork/firewall/schema.py ===

FILE tests/test_firewall_schema.py (new, 455 lines) — write_file with exactly:
=== BEGIN tests/test_firewall_schema.py ===
from __future__ import annotations

from types import SimpleNamespace

import pytest

from dirtywork.firewall import bounds
from dirtywork.firewall.capabilities import ActionKind, Capability
from dirtywork.firewall.errors import FirewallInternalError
from dirtywork.firewall.reasons import ReasonClass, ReasonCode
from dirtywork.firewall import schema
from dirtywork.firewall.schema import (
    ActionRequest,
    ApplyEditsArgs,
    BashArgs,
    CanonicalAction,
    Decision,
    Edit,
    FirewallEvent,
    GrepArgs,
    InsertArgs,
    PolicyDecision,
    ReadFileArgs,
    SemanticStatus,
    WriteFileArgs,
    action_identity,
    rejection_identity,
    valid_call_id,
)

EXPECTED_DECISIONS = sorted(["allow", "deny"])
EXPECTED_SEMANTIC_STATUSES = sorted(["semantic_known", "semantic_unknown"])


class _FakeToolCall:
    def __init__(self, id="call_1", name="write_file", arguments=None, error=None,
                 raw_arguments=""):
        self.id = id
        self.name = name
        self.arguments = arguments
        self.error = error
        self.raw_arguments = raw_arguments


def _write_action(**overrides):
    kwargs = dict(
        schema_version=bounds.FIREWALL_SCHEMA_VERSION,
        call_id="call_1",
        turn=1,
        kind=ActionKind.WRITE_FILE,
        args=WriteFileArgs(path="src/app.py", content="print(1)\n"),
        capabilities=frozenset({Capability.WORKSPACE_WRITE}),
        semantic_status=SemanticStatus.KNOWN,
    )
    kwargs.update(overrides)
    return CanonicalAction(**kwargs)


# --- group 1: vocabulary pins ---------------------------------------------


def test_decision_vocabulary_pin():
    assert sorted(m.value for m in Decision) == EXPECTED_DECISIONS


def test_semantic_status_vocabulary_pin():
    assert sorted(m.value for m in SemanticStatus) == EXPECTED_SEMANTIC_STATUSES


# --- group 9: PolicyDecision invariants ------------------------------------


def test_policy_decision_allow_with_reason_code_raises():
    with pytest.raises(FirewallInternalError):
        PolicyDecision(Decision.ALLOW, ReasonCode.TOOL_UNKNOWN, "")


def test_policy_decision_deny_with_none_raises():
    with pytest.raises(FirewallInternalError):
        PolicyDecision(Decision.DENY, None, "")


def test_policy_decision_deny_with_bare_string_raises():
    with pytest.raises(FirewallInternalError):
        PolicyDecision(Decision.DENY, "tool_unknown", "")


def test_policy_decision_valid_allow_and_deny():
    allow = PolicyDecision(Decision.ALLOW, None, "")
    assert allow.decision is Decision.ALLOW
    deny = PolicyDecision(Decision.DENY, ReasonCode.TOOL_UNKNOWN, "unknown tool")
    assert deny.reason_code is ReasonCode.TOOL_UNKNOWN


# --- group 10: semantic_unknown cannot deny --------------------------------


def test_policy_decision_rejects_semantic_unknown_as_reason_code():
    with pytest.raises(FirewallInternalError):
        PolicyDecision(Decision.DENY, "semantic_unknown", "")
    assert "semantic_unknown" not in [m.value for m in ReasonCode]


def test_canonical_action_semantic_unknown_constructs_with_no_reason_code():
    action = _write_action(semantic_status=SemanticStatus.UNKNOWN)
    assert action.semantic_status is SemanticStatus.UNKNOWN


# --- group 11: constructor invariants --------------------------------------


def test_canonical_action_wrong_args_class_for_kind_raises():
    with pytest.raises(FirewallInternalError):
        _write_action(args=ReadFileArgs(path="a", offset=0, limit=1))


def test_canonical_action_string_kind_raises():
    with pytest.raises(FirewallInternalError):
        _write_action(kind="bash")


def test_canonical_action_empty_capabilities_raises():
    with pytest.raises(FirewallInternalError):
        _write_action(capabilities=frozenset())


def test_canonical_action_capabilities_missing_base_raises():
    with pytest.raises(FirewallInternalError):
        _write_action(capabilities=frozenset({Capability.WORKSPACE_READ}))


def test_canonical_action_str_inside_capabilities_raises():
    # A str equal to a Capability's value collapses into the frozenset (str
    # mixin equality), so it would not exercise the isinstance check; use a
    # look-alike value that is not any Capability's value instead.
    with pytest.raises(FirewallInternalError):
        _write_action(
            capabilities=frozenset({Capability.WORKSPACE_WRITE, "not_a_capability"})
        )


def test_canonical_action_schema_version_mismatch_raises():
    with pytest.raises(FirewallInternalError):
        _write_action(schema_version=2)


def test_canonical_action_turn_zero_raises():
    with pytest.raises(FirewallInternalError):
        _write_action(turn=0)


def test_apply_edits_args_list_instead_of_tuple_raises():
    with pytest.raises(FirewallInternalError):
        ApplyEditsArgs(path="a", edits=[Edit(old="x", new="y")])


def test_apply_edits_args_empty_tuple_raises():
    with pytest.raises(FirewallInternalError):
        ApplyEditsArgs(path="a", edits=())


def test_apply_edits_args_too_many_edits_raises():
    edits = tuple(Edit(old="x", new="y") for _ in range(101))
    with pytest.raises(FirewallInternalError):
        ApplyEditsArgs(path="a", edits=edits)


def test_apply_edits_args_within_bound_constructs():
    edits = tuple(Edit(old="x", new="y") for _ in range(100))
    args = ApplyEditsArgs(path="a", edits=edits)
    assert len(args.edits) == 100


def test_edit_empty_old_raises():
    with pytest.raises(FirewallInternalError):
        Edit(old="", new="y")


def test_read_file_args_negative_offset_raises():
    with pytest.raises(FirewallInternalError):
        ReadFileArgs(path="a", offset=-1, limit=1)


def test_bash_args_timeout_over_max_raises():
    with pytest.raises(FirewallInternalError):
        BashArgs(command="ls", timeout=601)


def test_bash_args_timeout_bool_raises():
    with pytest.raises(FirewallInternalError):
        BashArgs(command="ls", timeout=True)


def test_path_over_max_chars_raises():
    with pytest.raises(FirewallInternalError):
        ReadFileArgs(path="a" * (bounds.MAX_PATH_CHARS + 1), offset=0, limit=1)


def test_grep_args_glob_none_constructs():
    args = GrepArgs(pattern="x", path=".", glob=None)
    assert args.glob is None


def test_bytes_for_str_field_raises():
    with pytest.raises(FirewallInternalError):
        ReadFileArgs(path=b"a", offset=0, limit=1)


# --- group 12: identity -----------------------------------------------------


def test_identity_same_kind_and_path_different_content_hash_equal():
    a = _write_action(args=WriteFileArgs(path="src/app.py", content="one"))
    b = _write_action(args=WriteFileArgs(path="src/app.py", content="two"))
    assert action_identity(a) == action_identity(b)


def test_identity_insert_before_vs_after_same_path_differ():
    before = _write_action(
        kind=ActionKind.INSERT_BEFORE,
        args=InsertArgs(path="a", anchor="x", text="y"),
        capabilities=frozenset({Capability.WORKSPACE_WRITE}),
    )
    after = _write_action(
        kind=ActionKind.INSERT_AFTER,
        args=InsertArgs(path="a", anchor="x", text="y"),
        capabilities=frozenset({Capability.WORKSPACE_WRITE}),
    )
    assert action_identity(before) != action_identity(after)


def test_identity_bash_commands_differing_by_flag_differ():
    a = _write_action(
        kind=ActionKind.BASH,
        args=BashArgs(command="ls -la", timeout=30),
        capabilities=frozenset({Capability.SHELL}),
    )
    b = _write_action(
        kind=ActionKind.BASH,
        args=BashArgs(command="ls -l", timeout=30),
        capabilities=frozenset({Capability.SHELL}),
    )
    assert action_identity(a) != action_identity(b)


def test_identity_read_file_different_offset_hash_equal():
    a = _write_action(
        kind=ActionKind.READ_FILE,
        args=ReadFileArgs(path="a", offset=0, limit=100),
        capabilities=frozenset({Capability.WORKSPACE_READ}),
    )
    b = _write_action(
        kind=ActionKind.READ_FILE,
        args=ReadFileArgs(path="a", offset=50, limit=400),
        capabilities=frozenset({Capability.WORKSPACE_READ}),
    )
    assert action_identity(a) == action_identity(b)


def test_identity_pinned_literal():
    action = CanonicalAction(
        schema_version=1,
        call_id="call_1",
        turn=1,
        kind=ActionKind.WRITE_FILE,
        args=WriteFileArgs(path="src/app.py", content="print(1)\n"),
        capabilities=frozenset({Capability.WORKSPACE_WRITE}),
        semantic_status=SemanticStatus.KNOWN,
    )
    assert action_identity(action) == (
        "3fcfda5eb96c0f0052810597adeb47a8f7d16cfe7bc46a29456e660ff74af783"
    )


def test_identity_changes_with_identity_version(monkeypatch):
    action = _write_action()
    original = action_identity(action)
    monkeypatch.setattr(schema, "IDENTITY_VERSION", schema.IDENTITY_VERSION + 1)
    assert action_identity(action) != original


# --- group 13: FirewallEvent -------------------------------------------------


def test_firewall_event_to_dict_keys_and_value_types():
    action = _write_action()
    policy = PolicyDecision(Decision.ALLOW, None, "")
    event = FirewallEvent.from_action(action, policy)
    d = event.to_dict()
    assert set(d.keys()) == {
        "schema_version", "stage", "turn", "call_id", "kind", "capabilities",
        "decision", "reason_code", "reason_class", "action_identity",
        "semantic_status",
    }
    for forbidden in ("args", "arguments", "content", "command", "path"):
        assert forbidden not in d
    assert isinstance(d["capabilities"], list)
    assert all(isinstance(c, str) for c in d["capabilities"])
    assert d["capabilities"] == sorted(d["capabilities"])
    for key in ("schema_version", "stage", "turn", "call_id", "decision", "action_identity"):
        assert isinstance(d[key], (str, int))


def test_firewall_event_from_action_allow_has_no_reason():
    action = _write_action()
    policy = PolicyDecision(Decision.ALLOW, None, "")
    event = FirewallEvent.from_action(action, policy)
    assert event.reason_code is None
    assert event.reason_class is None


def test_firewall_event_from_rejection_tool_unknown():
    request = ActionRequest(
        call_id="call_1", tool_name="read_files", arguments={}, parse_error=None,
        raw_chars=2, turn=1, batch_index=0, batch_size=1,
    )
    rejection = SimpleNamespace(reason_code=ReasonCode.TOOL_UNKNOWN, detail="")
    event = FirewallEvent.from_rejection(request, rejection)
    assert event.stage == "request"
    assert event.kind is None
    assert event.capabilities == ()
    assert event.semantic_status is None
    d = event.to_dict()
    assert d["capabilities"] == []


def test_firewall_event_from_rejection_call_id_invalid_gives_empty_call_id():
    request = ActionRequest(
        call_id="", tool_name="read_file", arguments={}, parse_error=None,
        raw_chars=2, turn=1, batch_index=0, batch_size=1,
    )
    rejection = SimpleNamespace(reason_code=ReasonCode.CALL_ID_INVALID, detail="")
    event = FirewallEvent.from_rejection(request, rejection)
    assert event.call_id == ""


def test_firewall_event_request_stage_allow_raises():
    with pytest.raises(FirewallInternalError):
        FirewallEvent(
            schema_version=bounds.FIREWALL_SCHEMA_VERSION,
            stage="request",
            turn=1,
            call_id="call_1",
            kind=None,
            capabilities=(),
            decision=Decision.ALLOW,
            reason_code=None,
            reason_class=None,
            action_identity="0" * 64,
            semantic_status=None,
        )


def test_firewall_event_action_stage_no_kind_raises():
    with pytest.raises(FirewallInternalError):
        FirewallEvent(
            schema_version=bounds.FIREWALL_SCHEMA_VERSION,
            stage="action",
            turn=1,
            call_id="call_1",
            kind=None,
            capabilities=("workspace_write",),
            decision=Decision.ALLOW,
            reason_code=None,
            reason_class=None,
            action_identity="0" * 64,
            semantic_status=SemanticStatus.KNOWN,
        )


# --- group 14: rejection identity -------------------------------------------


def _request_for(tool_name, call_id="call_1"):
    return ActionRequest(
        call_id=call_id, tool_name=tool_name, arguments={}, parse_error=None,
        raw_chars=2, turn=1, batch_index=0, batch_size=1,
    )


def test_rejection_identity_same_name_hash_equal():
    r1 = _request_for("read_files")
    r2 = _request_for("read_files")
    rejection = SimpleNamespace(reason_code=ReasonCode.TOOL_UNKNOWN, detail="")
    assert rejection_identity(r1, rejection) == rejection_identity(r2, rejection)


def test_rejection_identity_different_name_differ():
    r1 = _request_for("read_files")
    r2 = _request_for("writee_file")
    rejection = SimpleNamespace(reason_code=ReasonCode.TOOL_UNKNOWN, detail="")
    assert rejection_identity(r1, rejection) != rejection_identity(r2, rejection)


def test_rejection_identity_different_reason_code_differs():
    r = _request_for("read_files")
    rej_unknown = SimpleNamespace(reason_code=ReasonCode.TOOL_UNKNOWN, detail="")
    rej_payload = SimpleNamespace(reason_code=ReasonCode.PAYLOAD_TOO_LARGE, detail="")
    assert rejection_identity(r, rej_unknown) != rejection_identity(r, rej_payload)


def test_rejection_identity_survives_a_lone_surrogate_in_the_name():
    r = _request_for("\ud800")
    rejection = SimpleNamespace(reason_code=ReasonCode.TOOL_UNKNOWN, detail="")
    assert len(rejection_identity(r, rejection)) == 64
    assert FirewallEvent.from_rejection(r, rejection).stage == "request"


def test_rejection_identity_raw_name_absent_from_hash_and_to_dict():
    sentinel = "sentinel-tool-name-zzyzx"
    r = _request_for(sentinel)
    rejection = SimpleNamespace(reason_code=ReasonCode.TOOL_UNKNOWN, detail="")
    identity = rejection_identity(r, rejection)
    assert sentinel not in identity
    event = FirewallEvent.from_rejection(r, rejection)
    assert sentinel not in str(event.to_dict())


# --- ActionRequest.from_tool_call -------------------------------------------


def test_action_request_from_tool_call_copies_fields():
    tc = _FakeToolCall(id="call_9", name="bash", arguments={"command": "ls"},
                       error=None, raw_arguments='{"command": "ls"}')
    request = ActionRequest.from_tool_call(tc, turn=3, batch_index=1, batch_size=2)
    assert request.call_id == "call_9"
    assert request.tool_name == "bash"
    assert request.arguments == {"command": "ls"}
    assert request.parse_error is None
    assert request.raw_chars == len('{"command": "ls"}')
    assert request.turn == 3
    assert request.batch_index == 1
    assert request.batch_size == 2


def test_action_request_from_tool_call_raw_chars_none():
    tc = _FakeToolCall(raw_arguments=None)
    request = ActionRequest.from_tool_call(tc, turn=1, batch_index=0, batch_size=1)
    assert request.raw_chars == 0


# --- valid_call_id -----------------------------------------------------------


def test_valid_call_id_accepts_printable_ascii_no_whitespace():
    assert valid_call_id("call_1")
    assert valid_call_id("toolu_ABC123")


def test_valid_call_id_rejects_empty_oversized_and_whitespace():
    assert not valid_call_id("")
    assert not valid_call_id("a" * (bounds.MAX_CALL_ID_CHARS + 1))
    assert not valid_call_id("call 1")
    assert not valid_call_id("call\t1")
    assert not valid_call_id(None)
=== END tests/test_firewall_schema.py ===

VERIFY: run python3 -m pytest -q -p no:cacheprovider tests/test_firewall_schema.py and expect 47 passed. Then run python3 -m pytest -q -p no:cacheprovider with timeout=300 and expect all passed, 0 failed. Finish when both pass.
```

---
### Task 5: boundary validator and package re-exports

**Files:**
- Create: `dirtywork/firewall/request.py`
- Modify: `dirtywork/firewall/__init__.py:1` (replace the one-line docstring file with the re-exports and `__all__`)
- Test: `tests/test_firewall_request.py`

**Review fold-in (2026-09-08):** the walker checks each key together with its value, in order, so first-failure order holds across keys and values; key strings are bounded by `MAX_STRING_CHARS` like any other string (two regression tests in groups 6 and 7). Lists and dicts are walked by separate loops so no sentinel key can be confused with a real one; a `None` dict key, top-level or nested, is `argument_type_invalid` (two more tests in group 6).

**Interfaces:**
- Consumes: `ActionRequest`, `valid_call_id`, `_str_field` from Task 4; `ActionKind`; `ReasonCode`; bounds
- Produces: `Rejection(reason_code: ReasonCode, detail: str)` (frozen, validated); `check_request(request: ActionRequest) -> Rejection | None` implementing spec §6.2 checks 1–8 in order; `dirtywork.firewall.__all__` equal to the spec §2 literal with every name resolving. This closes issue #135.

- [x] **Dry-run on the scratch clone** (2026-09-08, clone of `main` at `d3c7ce8`): the files below written, `tests/test_firewall_*.py` for this task 40 passed, full host suite 1812 passed (baseline 1709), `ast.parse(..., feature_version=(3, 9))` clean. The brief text is the reference: its file blocks are the dry-run files byte for byte (checked by a round-trip script).
- [ ] **Confirm the base.** `main` must be at the head that includes Task 4's PR. Re-check that `dirtywork/firewall/__init__.py` is still the one-line docstring quoted as the edit_file old string.
- [ ] **Launch** the brief below verbatim through the invocation in the header, sampler on.
- [ ] **Review** against the gates in the header: `diff` every produced file against the brief's block (extract with the round-trip script or by eye); `files_changed` is exactly the brief's file list; host suite 1812 passed; `git diff --check` clean; 3.9 AST check silent.
- [ ] The isolation test spawns a subprocess with only `PYTHONPATH` and `PATH` in its environment; it passed on the host dry-run and needs no Docker.
- [ ] **Ledger row** in `docs/superpowers/bench/2026-09-XX-issue-135-w5-ledger.md` (status, turns, wall, tokens, tok/s, nudges, verdict, tool-call counts, sampler summary, diff-vs-brief result).
- [ ] **PR** `dirtywork/<slug>` → `main`, titled `feat(firewall): issue #135 W5 — boundary validator and package re-exports`, body naming the plan, the ledger and the spec; `Closes #135`. Wait for the owner's explicit merge go.

### Worker brief W5

```text
Issue #135 task W5 of 5 (Worker Action Firewall B): add dirtywork/firewall/request.py, the tool-agnostic boundary validator (Rejection and check_request) with its test, and replace the one-line dirtywork/firewall/__init__.py with the package's explicit re-exports and __all__. dirtywork/firewall/errors.py, bounds.py, reasons.py, capabilities.py and schema.py already exist from tasks W1-W4. Nothing outside the package imports it yet. Spec: docs/superpowers/specs/2026-09-08-issue-135-firewall-schema-design.md sections 2 and 6 (the files below are its exact content).

Touch ONLY dirtywork/firewall/request.py, tests/test_firewall_request.py, dirtywork/firewall/__init__.py. Create each NEW file with ONE write_file call whose content is exactly the text between its BEGIN and END marker lines below (byte for byte; keep every blank line; the file ends with a newline after its last line; the marker lines themselves are not part of the file). The ONE existing-file change is an edit_file on dirtywork/firewall/__init__.py with the exact old/new below. No other files, no docs, no commits, nothing else.

EDIT edit_file on dirtywork/firewall/__init__.py (line 1, the whole one-line file; the new text is the complete 48-line file). old:
"""Worker Action Firewall: canonical schema, bounds, capabilities and reason codes."""
new:
"""Worker Action Firewall: canonical schema, bounds, capabilities and reason codes."""
from __future__ import annotations

from .bounds import FIREWALL_SCHEMA_VERSION, IDENTITY_VERSION
from .capabilities import (
    ActionKind,
    BASE_CAPABILITIES,
    Capability,
    FILE_TARGET_RULES,
    LEGACY_RULES,
)
from .errors import FirewallInternalError
from .reasons import ReasonClass, ReasonCode, reason_class
from .request import Rejection, check_request
from .schema import (
    ActionRequest,
    ApplyEditsArgs,
    AppendFileArgs,
    BashArgs,
    CanonicalAction,
    CanonicalArgs,
    Decision,
    Edit,
    EditFileArgs,
    FinishArgs,
    FirewallEvent,
    GrepArgs,
    InsertArgs,
    ListDirArgs,
    PolicyDecision,
    ReadFileArgs,
    SemanticStatus,
    WriteFileArgs,
    action_identity,
    rejection_identity,
)

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


FILE dirtywork/firewall/request.py (new, 162 lines) — write_file with exactly:
=== BEGIN dirtywork/firewall/request.py ===
"""The tool-agnostic boundary validator: Rejection and check_request (spec §6)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .bounds import (
    MAX_ARGUMENT_KEYS,
    MAX_BATCH_CALLS,
    MAX_CALL_ID_CHARS,
    MAX_COLLECTION_ITEMS,
    MAX_DETAIL_CHARS,
    MAX_INT,
    MAX_NESTED_KEYS,
    MAX_NESTING_DEPTH,
    MAX_RAW_ARGUMENT_CHARS,
    MAX_STRING_CHARS,
    MAX_TOOL_NAME_CHARS,
    MIN_INT,
)
from .capabilities import ActionKind
from .errors import FirewallInternalError
from .reasons import ReasonCode
from .schema import ActionRequest, _str_field, valid_call_id


@dataclass(frozen=True)
class Rejection:
    """A request-stage denial: a ReasonCode plus harness-composed prose that
    never quotes worker-supplied text (spec §6.2)."""

    reason_code: ReasonCode
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.reason_code, ReasonCode):
            raise FirewallInternalError("reason_code must be a ReasonCode")
        _str_field(self.detail, "detail", MAX_DETAIL_CHARS)


def _check_scalar(value) -> Optional[ReasonCode]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if not (MIN_INT <= value <= MAX_INT):
            return ReasonCode.NUMBER_OUT_OF_RANGE
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return ReasonCode.NUMBER_OUT_OF_RANGE
        return None
    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            return ReasonCode.STRING_TOO_LONG
        return None
    return ReasonCode.ARGUMENT_TYPE_INVALID


def _check_key(key) -> Optional[ReasonCode]:
    if not isinstance(key, str):
        return ReasonCode.ARGUMENT_TYPE_INVALID
    return _check_scalar(key)


def _walk(node, depth: int) -> Optional[ReasonCode]:
    """Depth-first, key-order structural walk of one dict or list. `depth` is
    the container's own depth (the top-level `arguments` dict is depth 1).
    Depth and size are checked on entering a container, before its children
    are visited, so an oversized or too-deep structure is rejected without
    being traversed. Each key is checked together with its value, in order,
    so the first failure reported is the first one met."""
    if depth > MAX_NESTING_DEPTH:
        return ReasonCode.NESTING_TOO_DEEP
    if isinstance(node, dict):
        if len(node) > (MAX_ARGUMENT_KEYS if depth == 1 else MAX_NESTED_KEYS):
            return ReasonCode.COLLECTION_TOO_LARGE
        for key, child in node.items():
            rc = _check_key(key)
            if rc is None:
                rc = _child(child, depth)
            if rc is not None:
                return rc
        return None
    if len(node) > MAX_COLLECTION_ITEMS:
        return ReasonCode.COLLECTION_TOO_LARGE
    for child in node:
        rc = _child(child, depth)
        if rc is not None:
            return rc
    return None


def _child(child, depth: int) -> Optional[ReasonCode]:
    return _walk(child, depth + 1) if isinstance(child, (dict, list)) else _check_scalar(child)


_DETAIL_BY_CODE = {
    ReasonCode.BATCH_TOO_LARGE: f"batch_size exceeds {MAX_BATCH_CALLS}",
    ReasonCode.CALL_ID_INVALID: (
        f"call_id must be nonempty printable ASCII without whitespace, "
        f"<= {MAX_CALL_ID_CHARS} chars"
    ),
    ReasonCode.TOOL_NAME_INVALID: f"tool_name must be nonempty, <= {MAX_TOOL_NAME_CHARS} chars",
    ReasonCode.TOOL_UNKNOWN: "tool_name is not a known action kind",
    ReasonCode.ARGUMENTS_UNPARSEABLE: "arguments could not be parsed",
    ReasonCode.PAYLOAD_TOO_LARGE: f"raw_chars exceeds {MAX_RAW_ARGUMENT_CHARS}",
    ReasonCode.ARGUMENTS_NOT_OBJECT: "arguments must be a JSON object",
    ReasonCode.NESTING_TOO_DEEP: f"nesting exceeds depth {MAX_NESTING_DEPTH}",
    ReasonCode.COLLECTION_TOO_LARGE: "a collection exceeds its key or item limit",
    ReasonCode.ARGUMENT_TYPE_INVALID: "an argument has an unsupported type",
    ReasonCode.STRING_TOO_LONG: f"a string exceeds {MAX_STRING_CHARS} chars",
    ReasonCode.NUMBER_OUT_OF_RANGE: "a number is outside the supported range",
}


def _reject(code: ReasonCode) -> Rejection:
    return Rejection(reason_code=code, detail=_DETAIL_BY_CODE[code])


def check_request(request: ActionRequest) -> Optional[Rejection]:
    """Tool-agnostic structural validator (spec §6.2). Checks run in this
    fixed order and stop at the first failure."""
    # 1. batch size
    if request.batch_size > MAX_BATCH_CALLS:
        return _reject(ReasonCode.BATCH_TOO_LARGE)

    # 2. call_id
    if not valid_call_id(request.call_id):
        return _reject(ReasonCode.CALL_ID_INVALID)

    # 3. tool_name shape
    tool_name = request.tool_name
    if not isinstance(tool_name, str) or not 0 < len(tool_name) <= MAX_TOOL_NAME_CHARS:
        return _reject(ReasonCode.TOOL_NAME_INVALID)

    # 4. tool_name known
    try:
        ActionKind(tool_name)
    except ValueError:
        return _reject(ReasonCode.TOOL_UNKNOWN)

    # 5. parseable
    if request.parse_error is not None or request.arguments is None:
        return _reject(ReasonCode.ARGUMENTS_UNPARSEABLE)

    # 6. payload size
    if request.raw_chars > MAX_RAW_ARGUMENT_CHARS:
        return _reject(ReasonCode.PAYLOAD_TOO_LARGE)

    # 7. arguments is an object
    if not isinstance(request.arguments, dict):
        return _reject(ReasonCode.ARGUMENTS_NOT_OBJECT)

    # 8. structural walk
    code = _walk(request.arguments, 1)
    if code is not None:
        return _reject(code)

    return None
=== END dirtywork/firewall/request.py ===

FILE tests/test_firewall_request.py (new, 299 lines) — write_file with exactly:
=== BEGIN tests/test_firewall_request.py ===
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from dirtywork.firewall import bounds
from dirtywork.firewall.errors import FirewallInternalError
from dirtywork.firewall.reasons import ReasonCode
from dirtywork.firewall.request import Rejection, check_request
from dirtywork.firewall.schema import ActionRequest


def _request(**overrides):
    kwargs = dict(
        call_id="call_1",
        tool_name="read_file",
        arguments={"path": "a"},
        parse_error=None,
        raw_chars=12,
        turn=1,
        batch_index=0,
        batch_size=1,
    )
    kwargs.update(overrides)
    return ActionRequest(**kwargs)


# --- group 9 (Rejection invariants) ----------------------------------------


def test_rejection_valid_construction():
    r = Rejection(reason_code=ReasonCode.TOOL_UNKNOWN, detail="tool_name is not known")
    assert r.reason_code is ReasonCode.TOOL_UNKNOWN


def test_rejection_bad_reason_code_raises():
    with pytest.raises(FirewallInternalError):
        Rejection(reason_code="tool_unknown", detail="")


def test_rejection_detail_too_long_raises():
    with pytest.raises(FirewallInternalError):
        Rejection(reason_code=ReasonCode.TOOL_UNKNOWN, detail="x" * (bounds.MAX_DETAIL_CHARS + 1))


def test_rejection_detail_not_str_raises():
    with pytest.raises(FirewallInternalError):
        Rejection(reason_code=ReasonCode.TOOL_UNKNOWN, detail=None)


# --- group 6: check_request malformed input, one case per code ------------


def test_batch_too_large():
    r = check_request(_request(batch_size=33))
    assert r.reason_code is ReasonCode.BATCH_TOO_LARGE


def test_call_id_empty():
    r = check_request(_request(call_id=""))
    assert r.reason_code is ReasonCode.CALL_ID_INVALID


def test_call_id_oversized():
    r = check_request(_request(call_id="a" * (bounds.MAX_CALL_ID_CHARS + 1)))
    assert r.reason_code is ReasonCode.CALL_ID_INVALID


def test_call_id_whitespace():
    r = check_request(_request(call_id="call 1"))
    assert r.reason_code is ReasonCode.CALL_ID_INVALID


def test_tool_name_empty():
    r = check_request(_request(tool_name=""))
    assert r.reason_code is ReasonCode.TOOL_NAME_INVALID


def test_tool_name_oversized():
    r = check_request(_request(tool_name="a" * (bounds.MAX_TOOL_NAME_CHARS + 1)))
    assert r.reason_code is ReasonCode.TOOL_NAME_INVALID


def test_tool_name_unknown():
    r = check_request(_request(tool_name="read_files"))
    assert r.reason_code is ReasonCode.TOOL_UNKNOWN


def test_tool_name_unknown_marker_polluted():
    r = check_request(_request(tool_name="functions.read_file"))
    assert r.reason_code is ReasonCode.TOOL_UNKNOWN


def test_parse_error_set():
    r = check_request(_request(parse_error="bad json"))
    assert r.reason_code is ReasonCode.ARGUMENTS_UNPARSEABLE


def test_arguments_none():
    r = check_request(_request(arguments=None))
    assert r.reason_code is ReasonCode.ARGUMENTS_UNPARSEABLE


def test_raw_chars_over():
    r = check_request(_request(raw_chars=bounds.MAX_RAW_ARGUMENT_CHARS + 1))
    assert r.reason_code is ReasonCode.PAYLOAD_TOO_LARGE


def test_arguments_is_a_list():
    r = check_request(_request(arguments=["a"]))
    assert r.reason_code is ReasonCode.ARGUMENTS_NOT_OBJECT


def test_arguments_is_a_str():
    r = check_request(_request(arguments="a"))
    assert r.reason_code is ReasonCode.ARGUMENTS_NOT_OBJECT


def test_arguments_is_an_int():
    r = check_request(_request(arguments=1))
    assert r.reason_code is ReasonCode.ARGUMENTS_NOT_OBJECT


def test_depth_5():
    r = check_request(_request(arguments={"a": [[[{}]]]}))
    assert r.reason_code is ReasonCode.NESTING_TOO_DEEP


def test_33_top_level_keys():
    args = {f"k{i}": i for i in range(33)}
    r = check_request(_request(arguments=args))
    assert r.reason_code is ReasonCode.COLLECTION_TOO_LARGE


def test_nested_object_with_9_keys():
    nested = {f"k{i}": i for i in range(9)}
    r = check_request(_request(arguments={"path": nested}))
    assert r.reason_code is ReasonCode.COLLECTION_TOO_LARGE


def test_101_item_list():
    r = check_request(_request(arguments={"edits": list(range(101))}))
    assert r.reason_code is ReasonCode.COLLECTION_TOO_LARGE


def test_non_str_key():
    r = check_request(_request(arguments={1: "a"}))
    assert r.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_none_key_top_level():
    r = check_request(_request(arguments={None: "a"}))
    assert r.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_none_key_nested():
    r = check_request(_request(arguments={"path": {None: "a"}}))
    assert r.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_string_too_long():
    r = check_request(_request(arguments={"path": "a" * (bounds.MAX_STRING_CHARS + 1)}))
    assert r.reason_code is ReasonCode.STRING_TOO_LONG


def test_key_too_long():
    r = check_request(_request(arguments={"k" * (bounds.MAX_STRING_CHARS + 1): "a"}))
    assert r.reason_code is ReasonCode.STRING_TOO_LONG


def test_int_out_of_range():
    r = check_request(_request(arguments={"offset": 2**31}))
    assert r.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


def test_float_infinite():
    r = check_request(_request(arguments={"offset": float("inf")}))
    assert r.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


def test_bytes_value():
    r = check_request(_request(arguments={"path": b"a"}))
    assert r.reason_code is ReasonCode.ARGUMENT_TYPE_INVALID


def test_apply_edits_100_edits_depth_3_well_formed():
    edits = [{"old": "x", "new": "y"} for _ in range(100)]
    r = check_request(
        _request(tool_name="apply_edits", arguments={"path": "a", "edits": edits})
    )
    assert r is None


def test_glob_none_returns_none():
    r = check_request(_request(tool_name="grep", arguments={"pattern": "x", "glob": None}))
    assert r is None


def test_null_extra_returns_none():
    r = check_request(_request(arguments={"path": "a", "extra": None}))
    assert r is None


# --- group 7: first-failure order ------------------------------------------


def test_unknown_tool_and_oversized_reports_tool_unknown():
    r = check_request(
        _request(
            tool_name="not_a_tool",
            raw_chars=bounds.MAX_RAW_ARGUMENT_CHARS + 1,
        )
    )
    assert r.reason_code is ReasonCode.TOOL_UNKNOWN


def test_bad_value_before_later_bad_key_reports_the_value():
    r = check_request(_request(arguments={"first": float("inf"), 1: "a"}))
    assert r.reason_code is ReasonCode.NUMBER_OUT_OF_RANGE


def test_nonobject_and_oversized_reports_payload_too_large():
    r = check_request(
        _request(
            arguments="not an object",
            raw_chars=bounds.MAX_RAW_ARGUMENT_CHARS + 1,
        )
    )
    assert r.reason_code is ReasonCode.PAYLOAD_TOO_LARGE


# --- group 8: rejection detail never contains worker-supplied text --------


def test_detail_never_contains_worker_supplied_text():
    sentinel = "SENTINEL_VALUE_ZZYZX"
    r = check_request(
        _request(
            tool_name=sentinel,
            arguments={sentinel: sentinel},
        )
    )
    assert r is not None
    assert sentinel not in r.detail
    assert len(r.detail) <= bounds.MAX_DETAIL_CHARS


def test_detail_sentinel_absent_in_structural_rejection():
    sentinel = "OTHER_SENTINEL_1234"
    r = check_request(_request(arguments={sentinel: "a" * (bounds.MAX_STRING_CHARS + 1)}))
    assert r is not None
    assert sentinel not in r.detail
    assert len(r.detail) <= bounds.MAX_DETAIL_CHARS


# --- group 16: isolation ----------------------------------------------------


def test_isolation_no_executor_modules_imported():
    repo_root = str(pathlib.Path(__file__).resolve().parent.parent)
    code = (
        "import sys, dirtywork.firewall; "
        "bad = [m for m in ('dirtywork.tools','dirtywork.builtin_tools',"
        "'dirtywork.guardrails','dirtywork.runner','dirtywork.sandbox') "
        "if m in sys.modules]; "
        "sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env={"PYTHONPATH": repo_root, "PATH": os.environ.get("PATH", "")},
    )
    assert proc.returncode == 0


# --- __all__ / package re-export completeness ------------------------------


def test_package_all_matches_spec_and_every_name_resolves():
    import dirtywork.firewall as fw

    expected = [
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
    assert fw.__all__ == expected
    for name in expected:
        assert hasattr(fw, name), f"{name} does not resolve on the package"
=== END tests/test_firewall_request.py ===

VERIFY: run python3 -m pytest -q -p no:cacheprovider tests/test_firewall_request.py and expect 40 passed. Then run python3 -m pytest -q -p no:cacheprovider with timeout=300 and expect all passed, 0 failed. Finish when both pass.
```

---
## Delivery

Five PRs, merged in order, each after its own review and the owner's go. After the fifth merges: comment on issue #135 with the five ledger links and the final suite count; update the Firewall cycle memory; issue #136 (normalization and canonical identity) is next and must be briefed against the merged package, not against its issue text. No release is cut for this issue on its own; the owner decides when the Firewall work ships.

## Self-review against the spec

- §2 layout, import order, `__init__` staging, `__all__`, `pyproject` registration: Tasks 1 and 5.
- §3 `ActionRequest` and `from_tool_call`: Task 4.
- §4 `ActionKind`: Task 3; args classes, constructor invariants, `CanonicalAction`, `SemanticStatus`: Task 4.
- §5 `Capability`, `BASE_CAPABILITIES`, `LEGACY_RULES`, `FILE_TARGET_RULES`: Task 3. §5.4 (custom tools) is deferred by the spec; no task.
- §6.1 bounds: Task 1. §6.2 `check_request` and `Rejection`: Task 5. §6.3 `FirewallInternalError`: Task 1.
- §7 reason classes and codes: Task 2.
- §8 `Decision`, `PolicyDecision`: Task 4.
- §9 identities and `FirewallEvent`: Task 4.
- §10 versioning: constants in Task 1; nothing else to build.
- §11 test groups: 1 (Tasks 2, 3, 4), 2–5 (Task 3, Task 1, Tasks 2–3), 6–8 (Task 5), 9–14 (Task 4), 15 (Task 1), 16 (Task 5).
- One deviation from the spec's test text: §11 group 11 says "a `str` inside `capabilities` raises"; a `str` equal to a real capability value collapses into the frozenset by str-equality before the constructor runs, so the test uses a look-alike string with no matching value. The spec's intent (a non-`Capability` member is rejected) is what the test proves.
