# Issue #137: Firewall deterministic policy engine and fail-closed behavior — implementation plan

> **For agentic workers:** this plan is executed by the released dirtywork (repository `CLAUDE.md` dogfood rule), one run per task, each from the brief quoted verbatim under that task. The orchestrator dry-runs every brief on a scratch clone first, so each brief's files are the reviewed reference and the review gate is a byte comparison. Claude plans, briefs and reviews; the worker writes the files.

**Goal:** land the Firewall's decision layer from the spec: a run context, three ordered file-target rules, the shell analyzer copied by value from `guardrails.py`, a pure `evaluate`, and the fail-closed `decide` and `decide_batch` entry points, with no runtime behavior change.

**Architecture:** `shell.py` (rules table, worktree-reference rewrite, first-match analyzer) below `policy.py` (context, verdict, outcome, evaluate, decide), both importing only siblings; `normalize.py` gains `duplicate_positions`. Three tasks: the analyzer with its drift and parity tests, the engine with its precedence and fail-closed tests, then the re-exports and pins. Each is green before the next exists.

**Tech Stack:** Python >=3.9 (stdlib only: `re`, `posixpath`, `dataclasses`, `enum`), pytest. No dependencies added.

**Spec:** [`docs/superpowers/specs/2026-09-19-issue-137-firewall-policy-design.md`](../specs/2026-09-19-issue-137-firewall-policy-design.md) (approved 2026-09-19). Section numbers below refer to it.

## Global Constraints

- Repository `CLAUDE.md`: the latest released dirtywork plus a local worker implements code; Claude plans, briefs and reviews. PyPI checked 2026-09-19: `dirtywork==0.13.2`. Worker: `qwen3.6-35b-a3b-splash` (Inco AI Splash engine) served by LM Studio Bionic 1.1.5 on `http://localhost:1234/v1`; the model reasons by default and the released dirtywork cannot send `reasoning_effort` (issue #173), so every run passes `--max-tokens 16384` (Task 2, whose brief carries a 30 KB write, passes `--max-tokens 24576`). Docker sandbox, image `dirtywork-worker-pytest:0.13`, network off.
- The worker must be the only resident model: a Splash run beside a second 45 GB model died on an engine memory timeout (issue #136 W2 attempt 1). `lms ps` before every launch.
- No brief may contain a literal tool-call marker (`<tool_call>` and its kin): the worker's chat template treats them as special tokens and the engine consumes them inside a tool-call argument (issue #136 W2 attempt 2). Reference files build such strings by concatenation; the generator greps every brief for literals.
- Python 3.9 compatible source: `from __future__ import annotations` first in every module and test; `class X(str, enum.Enum)`; no `StrEnum`, `match`, `kw_only`, `slots=True`.
- Package rules (spec §2): stdlib only; `dirtywork/firewall/shell.py` and `policy.py` import nothing from `dirtywork/` outside the package; tests may import `dirtywork.guardrails`; tests import from submodules, never the package root, until Task 3 adds the re-exports.
- No runtime behavior change: nothing outside the package imports it after this issue (spec §1, §12). `guardrails.py` and `check_bash_command` are untouched.
- Every task's brief names its files; the worker touches only those. New files are written with `write_file`; every change to an existing file is an exact `edit_file` pair with its base line numbers. Brief blocks below are the reference files: re-extract them to compare a worker diff.
- Each task runs from `main` at the head the dry run used, after the previous task's PR has merged, or stacked with `--branch-from @<previous slug>` (then retarget each PR to `main` after the one below merges, close/reopen for CI). One PR per task, one ledger row per run under `docs/superpowers/bench/`, sampler on.
- No merge and no release without the owner's explicit per-action go.

## Invocation (every task)

```bash
S=<scratchpad>; ~/.lmstudio/bin/lms ps | grep -q qwen3.6-35b-a3b-splash || ~/.lmstudio/bin/lms load qwen3.6-35b-a3b-splash -y
tools/soak_sampler.sh $S/2026-09-XX-issue-137-w<N>-sampler.csv &
pipx run --spec 'dirtywork==0.13.2' dirtywork run "$(cat $S/brief-137-w<N>.txt)" \
  --repo /Users/jimschneider/repos/dirtywork [--branch-from @<previous slug>] \
  --model qwen3.6-35b-a3b-splash --max-tokens 16384 \
  --sandbox docker --image dirtywork-worker-pytest:0.13 \
  --verify "python3 -m pytest -q -p no:cacheprovider" --verify-rounds 2 \
  --max-turns 60 --timeout 1800
tools/soak_sampler.sh $S/2026-09-XX-issue-137-w<N>-sampler.csv --stop
```

The brief is passed as one argv element. The sampler CSV is committed beside the ledger on the run branch.

## Review gates (every task)

- Worker diff matches the dry-run files byte for byte: apply the brief's edit pairs to the base and `cmp` every produced file (the #136 series' `review136.py` does exactly this). A trailing-newline or blank-line fix by the orchestrator is allowed and must be disclosed in the commit and the ledger; anything else is a reject or, if it is plainly the more correct file, an accepted deviation folded back into the reference and the plan (issue #136 W3 precedent).
- `files_changed` lists only the brief's files.
- Host: `PYTHONPATH=. pipx run --spec pytest pytest -q -p no:cacheprovider` all green with the expected count; `git diff --check` clean; `python3 -c "import ast,sys; [ast.parse(open(f).read(), feature_version=(3,9)) for f in sys.argv[1:]]" dirtywork/firewall/*.py tests/test_firewall_*.py` silent; the drift, parity and import-isolation tests green.
- Copy `diff.patch` and `orchestrator/` receipts out of the run dir before any `runs clean`.
- Ledger row: status, turns, wall, prompt/completion tokens, tok/s, nudges, verdict, tool-call counts, sampler summary, diff-vs-brief result.

---
### Task 1: shell: the analyzer and the drift pin

**Files:**
- Create: `dirtywork/firewall/shell.py`
- Test: `tests/test_firewall_shell.py`

**Interfaces:**
- Consumes: `Capability` (`capabilities.py`), `ReasonCode` (`reasons.py`, unchanged), `FirewallInternalError`. The tests import `dirtywork.guardrails` (`_RULES`, `_ROOT_BOUNDARY`, `_rewrite_worktree_refs`, `check_bash_command`) and `LEGACY_RULES` for the drift pin.
- Produces: `ShellRule(index, scope, pattern, legacy_reason, capability, reason_code)` frozen; `SHELL_RULES` (eight, in `guardrails._RULES` order); `ROOT_BOUNDARY`; `ShellMatch(index, capability, reason_code, legacy_reason)`; `rewrite_worktree_refs(command, roots) -> str`; `analyze_command(command, *, mode, worktree_roots) -> Optional[ShellMatch]` (spec §5). Task 2's `evaluate` calls `analyze_command` for every `bash` action.

- [x] **Dry-run on the scratch clone** (2026-09-19, clone of `main` at `d8638e4`): the files below written, `tests/test_firewall_shell.py` 8 passed (drift pin, host and Docker parity on a 31-command corpus against `check_bash_command`, the rewrite against `_rewrite_worktree_refs` on a symlinked temporary worktree, multi-match precedence, empty and benign commands, never-raises, bad inputs), full host suite 2188 passed (baseline 2180), `ast.parse(..., feature_version=(3, 9))` silent, `git diff --check` clean. A first draft added a reason code for option-like paths; review showed #145 and #153 had already closed that gap, and the code was dropped before any run (spec §3).
- [ ] **Confirm the base.** `main` at `d8638e4` or later with `dirtywork/firewall/` unchanged since (`git log --oneline -1 -- dirtywork/firewall` shows `d1da433`).
- [ ] **Launch** the brief below verbatim through the invocation in the header, sampler on, only the worker model resident. Two `write_file`s.
- [ ] **Review** against the gates in the header: `cmp` the two new files against their blocks; `files_changed` is exactly the two files; host suite 2188 passed; `diff --check`; 3.9 grammar; the drift and parity tests green in the produced test file.
- [ ] **Ledger** `docs/superpowers/bench/2026-09-XX-issue-137-w1-shell-ledger.md` plus the sampler CSV, committed on the run branch.
- [ ] **PR** `dirtywork/<slug>` → `main` (or stacked), titled `feat(firewall): issue #137 W1 — shell analyzer and drift pin`, body naming the plan, the spec and the ledger; part 1 of 3 for issue #137 (does not close it).

### Worker brief W1

```text
Issue #137 task W1 of 3 (Worker Action Firewall D): add dirtywork/firewall/shell.py, the deterministic shell-command analyzer (the eight guardrail rules copied by value, the worktree-reference rewrite, analyze_command) with its test. The rest of dirtywork/firewall/ exists from issues #135 and #136. Nothing outside the package imports it. Spec: docs/superpowers/specs/2026-09-19-issue-137-firewall-policy-design.md sections 3, 5 and 11 (the files below are its exact content).

Touch ONLY dirtywork/firewall/shell.py, tests/test_firewall_shell.py. Create each NEW file with ONE write_file call whose content is exactly the text between its BEGIN and END marker lines below (byte for byte; keep every blank line; the file ends with a newline after its last line; the marker lines themselves are not part of the file). Use relative paths exactly as written (never an absolute /work/... path). No other files, no docs, no commits, nothing else.

FILE dirtywork/firewall/shell.py (new, 158 lines) — write_file with exactly:
=== BEGIN dirtywork/firewall/shell.py ===
"""The deterministic shell-command analyzer: the eight guardrail denylist
rules copied by value from `dirtywork.guardrails`, pinned to it by a drift
test, and the worktree-reference rewrite without filesystem access (spec
§5)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .capabilities import Capability
from .errors import FirewallInternalError
from .reasons import ReasonCode

# ORDER IS THE CONTRACT: the reported reason for a two-rule-match command is
# a documented transcript field (`guardrail_block.reason`) an orchestrating
# agent may key on, so the scan order below matches guardrails._RULES
# exactly. "always" rules are mode-independent policy; "host" rules protect
# the host filesystem and the host repo's shared refs/config and are
# meaningless in docker mode, where the container itself is the boundary.
#
# WHY the escape targets look the way they do: absolute (/), home (~) and
# parent-relative (..) are the natural accident forms for escaping a
# worktree. `$HOME` is deliberately NOT matched (it resolves inside the
# worktree); the small closed list of toolchain-manager home variables
# (_HOME_KEYED_VARS) IS matched, because those point at the operator's real
# home even though HOME itself is relocated into the worktree. See
# guardrails.py for the full rationale; these fragments are the same Python
# expressions, copied by value, so the two modules read alike.
_ESCAPE_TARGET = r"(?:\./)*(?:/|~|\.\.)"
_TOOLCHAIN_HOMES = (
    ("VOLTA_HOME", ".volta"),
    ("RUSTUP_HOME", ".rustup"),
    ("CARGO_HOME", ".cargo"),
    ("NVM_DIR", ".nvm"),
    ("PYENV_ROOT", ".pyenv"),
)
_HOME_KEYED_VARS = (r"\$\{?(?:" + "|".join(var for var, _ in _TOOLCHAIN_HOMES) + r")\b")
_HOME_ESCAPE_TARGET = r"(?:" + _ESCAPE_TARGET + r"|" + _HOME_KEYED_VARS + r")"
# git accepts global options (-C <path>, -c <key>=<value>, --<flag>[=value],
# -<x>) before the subcommand; every git-subcommand rule below is prefixed
# with this instead of a bare `\bgit\s+`.
_GIT_OPTS = r"\bgit\s+(?:(?:-C\s+\S+|-c\s+\S+|--\S+|-[A-Za-z]\S*)\s+)*"


@dataclass(frozen=True)
class ShellRule:
    """One denylist rule: `scope`, `pattern` and `legacy_reason` are copied
    by value from `guardrails._RULES[index]`; `capability` and `reason_code`
    are `guardrails.LEGACY_RULES[index]`'s (spec §5)."""

    index: int
    scope: str  # "always" | "host"
    pattern: str
    legacy_reason: str
    capability: Capability
    reason_code: ReasonCode


SHELL_RULES: "tuple[ShellRule, ...]" = (
    ShellRule(0, "always", r"\bsudo\b",
              "sudo is not allowed",
              Capability.PRIVILEGE, ReasonCode.PRIVILEGE_ESCALATION),
    ShellRule(1, "always", _GIT_OPTS + r"push\b",
              "git push is not allowed — leave changes uncommitted for review",
              Capability.REPO_PUBLISH, ReasonCode.REPO_PUBLISH),
    # A linked worktree SHARES refs/config/objects with the parent repo, so
    # these git subcommands mutate the parent's state from inside the
    # worktree. Read-only forms (config --get/--list, remote -v, worktree
    # list, bare reflog) are intentionally NOT matched.
    ShellRule(2, "host",
              _GIT_OPTS + r"config\b(?![^;|&]*\s(?:--get\S*|--list|-l)\b)"
              r"|" + _GIT_OPTS + r"remote\s+(add|set-url|remove|rm|rename)\b"
              r"|" + _GIT_OPTS + r"(update-ref|gc|filter-branch)\b"
              r"|" + _GIT_OPTS + r"reflog\s+(expire|delete)\b"
              r"|" + _GIT_OPTS + r"worktree\s+(add|remove|prune|move)\b"
              r"|" + _GIT_OPTS + r"branch\s+(-[dDmM]\b|--(delete|move)\b)"
              r"|" + _GIT_OPTS + r"tag\s+(-d\b|--delete\b)",
              "git command that writes the parent repo's shared refs/config is not allowed",
              Capability.REPO_CONTROL, ReasonCode.REPO_CONTROL),
    ShellRule(3, "host",
              r"\b(rm|mv|chmod|chown)\b[^|;&]*\s['\"]?" + _HOME_ESCAPE_TARGET,
              "destructive command targeting a path outside the worktree",
              Capability.HOST_FS, ReasonCode.HOST_FS_DESTRUCTIVE),
    ShellRule(4, "always",
              r"\b(curl|wget)\b[^|;&]*\|\s*['\"]?\w*\s*"
              r"((ba|z|da)?sh|python[0-9.]*|node|ruby|perl)\b",
              "piping a download into an interpreter is not allowed",
              Capability.REMOTE_CODE_EXEC, ReasonCode.REMOTE_CODE_EXEC),
    ShellRule(5, "always", r"\b(osascript|launchctl|shutdown|reboot|killall)\b",
              "system-control commands are not allowed",
              Capability.SYSTEM_CONTROL, ReasonCode.SYSTEM_CONTROL),
    ShellRule(6, "host",
              r">>?\s*['\"]?(?!/dev/null)" + _HOME_ESCAPE_TARGET,
              "redirecting output outside the worktree is not allowed",
              Capability.HOST_FS, ReasonCode.HOST_FS_REDIRECT),
    ShellRule(7, "host",
              r"\b(cd|pushd)\s+['\"]?" + _HOME_ESCAPE_TARGET,
              "changing directory out of the worktree is not allowed",
              Capability.HOST_FS, ReasonCode.HOST_FS_CHDIR),
)

_COMPILED: "tuple[re.Pattern, ...]" = tuple(
    re.compile(rule.pattern, re.IGNORECASE) for rule in SHELL_RULES
)

ROOT_BOUNDARY = r"""(?=[/\s'"]|$)"""  # root must end at a path/word boundary


@dataclass(frozen=True)
class ShellMatch:
    """The first matching rule for a command (spec §5)."""

    index: int
    capability: Capability
    reason_code: ReasonCode
    legacy_reason: str


def rewrite_worktree_refs(command: str, roots: "tuple[str, ...]") -> str:
    """Rewrite absolute references to a worktree root to a relative `.` form
    in the string being checked; the command that would actually execute is
    never touched (spec §5). `roots` must already be given longest first;
    this never sorts, never resolves, and never touches the filesystem."""
    checked = command
    for root in roots:
        checked = re.sub(re.escape(root) + ROOT_BOUNDARY, ".", checked)
    return checked


def analyze_command(
    command: str, *, mode: str, worktree_roots: "tuple[str, ...]"
) -> Optional[ShellMatch]:
    """Return the first matching rule for `command`, or None (spec §5).

    Host mode rewrites worktree references first, then scans every rule, in
    the original order (`guardrail_block.reason` is a documented transcript
    field an orchestrating agent may key on). Docker mode skips the rewrite
    (there is no worktree path for a container-scoped command to be
    rewritten against) and skips "host"-scoped rules, since the container
    itself is the boundary they would otherwise duplicate. Never raises on
    any `str` command; raises FirewallInternalError on an unknown mode or a
    non-str command, so unrecognized internal state fails closed instead of
    falling through to ALLOW.
    """
    if mode not in ("host", "docker"):
        raise FirewallInternalError(f"unknown mode: {mode!r}")
    if not isinstance(command, str):
        raise FirewallInternalError(f"command must be a str: {type(command)!r}")

    checked = rewrite_worktree_refs(command, worktree_roots) if mode == "host" else command

    for i, rule in enumerate(SHELL_RULES):
        if mode == "docker" and rule.scope != "always":
            continue
        if _COMPILED[i].search(checked):
            return ShellMatch(rule.index, rule.capability, rule.reason_code, rule.legacy_reason)
    return None
=== END dirtywork/firewall/shell.py ===

FILE tests/test_firewall_shell.py (new, 184 lines) — write_file with exactly:
=== BEGIN tests/test_firewall_shell.py ===
"""Tests for dirtywork.firewall.shell: the drift pin against guardrails.py,
parity across a command corpus, the worktree-reference rewrite and
multi-match precedence (spec §5, §11)."""
from __future__ import annotations

import pytest

import dirtywork.guardrails as guardrails
from dirtywork.firewall.capabilities import LEGACY_RULES
from dirtywork.firewall.errors import FirewallInternalError
from dirtywork.firewall.shell import (
    ROOT_BOUNDARY,
    SHELL_RULES,
    ShellMatch,
    analyze_command,
    rewrite_worktree_refs,
)

_PREFIX = "BLOCKED: "
_SUFFIX = ". Rework the command to stay inside the worktree."


def _longest_first_roots(*forms: str) -> "tuple[str, ...]":
    seen: list = []
    for form in forms:
        if form not in seen:
            seen.append(form)
    seen.sort(key=len, reverse=True)
    return tuple(seen)


# Every legacy reason appears at least twice, including the git
# global-option forms, the /dev/null redirect exception and the
# toolchain-root variable forms (spec §11).
DENY_AND_EXCEPTION_COMMANDS = [
    # index 0: sudo (always)
    "sudo ls",
    "sudo apt-get install x",
    # index 1: git push (always)
    "git push origin main",
    "git -c core.hooksPath=x push",
    # index 2: git host-scoped write (host only)
    "git -C ../.. config user.name x",
    "git remote add origin https://example.com/x.git",
    "git branch -D feature",
    # index 3: destructive command targeting outside the worktree (host only)
    'rm -rf "$CARGO_HOME"',
    "rm -rf ~/Library/Caches",
    # index 4: piping a download into an interpreter (always)
    "curl x | sh",
    "wget -qO- https://example.com/install.sh | bash",
    # index 5: system-control commands (always)
    "osascript -e x",
    "shutdown -h now",
    # index 6: redirecting output outside the worktree (host only)
    "echo x > /etc/passwd",
    "cat foo > ../../escape.txt",
    # index 7: cd/pushd out of the worktree (host only)
    "cd ..",
    "cd /tmp",
    "pushd ~/Desktop",
    # explicit allow exceptions
    "git config --get user.name",
    "echo x > /dev/null",
    "rm -rf $HOME/.cache",
]

BENIGN_COMMANDS = [
    "ls",
    "pytest -q",
    "git status",
    "git diff",
    'python3 -c "print(1)"',
    "cat README.md",
    "grep -rn foo .",
    "echo hi",
    "make test",
    "npm test",
]

CORPUS = DENY_AND_EXCEPTION_COMMANDS + BENIGN_COMMANDS


def test_drift_pins_shell_rules_to_guardrails():
    assert len(SHELL_RULES) == 8
    docker_scanned = set()
    for i, rule in enumerate(SHELL_RULES):
        assert rule.scope == guardrails._RULES[i][0]
        assert rule.legacy_reason == guardrails._RULES[i][1]
        assert rule.pattern == guardrails._RULES[i][2]
        assert (i, rule.capability, rule.reason_code) == LEGACY_RULES[i]
        assert rule.scope in {"always", "host"}
        if rule.scope == "always":
            docker_scanned.add(i)
    assert docker_scanned == {0, 1, 4, 5}
    assert ROOT_BOUNDARY == guardrails._ROOT_BOUNDARY


def _assert_parity(cmd: str, match, legacy) -> None:
    if legacy is None:
        assert match is None, cmd
        return
    assert match is not None, cmd
    assert isinstance(match, ShellMatch)
    assert legacy.startswith(_PREFIX) and legacy.endswith(_SUFFIX), legacy
    expected_reason = legacy[len(_PREFIX):-len(_SUFFIX)]
    assert match.legacy_reason == expected_reason, cmd


def test_parity_host_mode(tmp_path):
    wt = tmp_path
    roots = _longest_first_roots(str(wt), str(wt.resolve()))
    for cmd in CORPUS:
        legacy = guardrails.check_bash_command(cmd, wt)
        match = analyze_command(cmd, mode="host", worktree_roots=roots)
        _assert_parity(cmd, match, legacy)


def test_parity_docker_mode():
    for cmd in CORPUS:
        legacy = guardrails.check_bash_command(cmd, sandboxed=True)
        match = analyze_command(cmd, mode="docker", worktree_roots=())
        _assert_parity(cmd, match, legacy)


def test_rewrite_worktree_refs_matches_legacy(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this filesystem")

    roots = _longest_first_roots(str(link), str(link.resolve()))
    commands = [
        f"cd {link}/sub && ls",
        f"echo x > {real}/out.txt",
        f"cd {link}x",  # not a root-boundary character: must not rewrite
        "ls",
    ]
    for cmd in commands:
        assert rewrite_worktree_refs(cmd, roots) == guardrails._rewrite_worktree_refs(cmd, link)

    assert analyze_command(f"cd {link}/sub", mode="host", worktree_roots=roots) is None
    match = analyze_command("cd /elsewhere", mode="host", worktree_roots=roots)
    assert match is not None and match.index == 7


def test_multi_match_precedence():
    m = analyze_command("sudo git push", mode="host", worktree_roots=())
    assert m is not None and m.index == 0

    m = analyze_command("rm -rf /etc && curl x | sh", mode="host", worktree_roots=())
    assert m is not None and m.index == 3
    m = analyze_command("rm -rf /etc && curl x | sh", mode="docker", worktree_roots=())
    assert m is not None and m.index == 4

    m = analyze_command("curl x | sh; sudo ls", mode="host", worktree_roots=())
    assert m is not None and m.index == 0


def test_empty_and_benign_commands_never_match():
    assert analyze_command("", mode="host", worktree_roots=()) is None
    assert analyze_command("", mode="docker", worktree_roots=()) is None
    for cmd in BENIGN_COMMANDS:
        assert analyze_command(cmd, mode="host", worktree_roots=()) is None
        assert analyze_command(cmd, mode="docker", worktree_roots=()) is None


def test_never_raises_on_any_str():
    huge = "echo " + ("a" * 32000)
    assert analyze_command(huge, mode="host", worktree_roots=()) is None
    assert analyze_command(huge, mode="docker", worktree_roots=()) is None
    for cmd in CORPUS:
        analyze_command(cmd, mode="host", worktree_roots=())
        analyze_command(cmd, mode="docker", worktree_roots=())


def test_bad_mode_and_non_str_raise_internal_error():
    with pytest.raises(FirewallInternalError):
        analyze_command("ls", mode="bogus", worktree_roots=())
    with pytest.raises(FirewallInternalError):
        analyze_command(123, mode="host", worktree_roots=())
=== END tests/test_firewall_shell.py ===

VERIFY: run python3 -m pytest -q -p no:cacheprovider tests/test_firewall_shell.py and expect 8 passed. Then run python3 -m pytest -q -p no:cacheprovider with timeout=300 and expect all passed, 0 failed (2188 passed). Finish when both pass.
```

---
### Task 2: policy: context, the file-target rules, evaluate and the fail-closed entry points

**Files:**
- Create: `dirtywork/firewall/policy.py`
- Modify: `dirtywork/firewall/normalize.py` (`duplicate_positions` added before `canonicalize_batch`, which is refactored onto it; one `edit_file` pair, base line numbers in the brief)
- Test: `tests/test_firewall_policy.py` (the existing `tests/test_firewall_normalize.py` batch tests stay green unchanged)

**Interfaces:**
- Consumes: `analyze_command` (Task 1); `canonicalize`, `Normalization`, `WRITE_KINDS` and the new `duplicate_positions` (`normalize.py`); `normalize_path`, `TargetClass` (`paths.py`); `CanonicalAction`, `ActionRequest`, `PolicyDecision`, `Decision`, `FirewallEvent` (`schema.py`); `Rejection`; `ReasonCode`; `ActionKind`; `FirewallInternalError`.
- Produces: `PolicyContext(mode, worktree_roots)` frozen with its invariants; `Verdict(action, policy)`; `Outcome(action, policy, event, dropped_keys)`; `evaluate(action, context) -> Verdict`; `decide(request, context) -> Outcome`; `decide_batch(requests, context) -> list[Outcome]`; the two detail constants `DETAIL_REPO_METADATA_TARGET`, `DETAIL_PATH_OUTSIDE_WORKSPACE`; `normalize.duplicate_positions(requests) -> frozenset[int]` (spec §4, §6, §7). This is the surface #138 calls.

- [x] **Dry-run on the scratch clone** (2026-09-19, on top of Task 1): the files and the edit below applied, `tests/test_firewall_policy.py` 87 passed (context and root-shape invariants; every file-target rule on a read kind and a write kind in both modes, the `.git` aliases and the `..` component cases, a benign path across all nine path kinds, every file-target denial carrying its `FILE_TARGET_RULES` capability in the event (`repo_control`, `host_fs`) and every ALLOW returning the action unchanged; an evaluation failure keeping the canonical action with an action-stage `firewall_internal_error` event whose identity still distinguishes targets, with the request-stage and no-event fallbacks behind it, and the internal-error detail bounded before construction so a 300-character exception class name cannot make the guard itself raise; `finish`; every shell rule through `evaluate` with the capability added; detail bounds and no leakage; `evaluate`'s raise contract; `decide` accept, rejection, internal-error and double-failure paths; `decide_batch` mixed batches with per-request isolation; the parent design §19 invariants as named tests; import isolation), full host suite 2275 passed (Task 1: 2188), `ast.parse(..., feature_version=(3, 9))` silent, `git diff --check` clean.
- [ ] **Confirm the base.** Task 1's PR merged (or its run branch as `--branch-from`); `dirtywork/firewall/shell.py` present at the brief's content; the `normalize.py` anchor at the quoted line numbers.
- [ ] **Launch** the brief below verbatim through the invocation in the header, sampler on, only the worker model resident. One `edit_file` and two `write_file`s (about 14 KB and 41 KB; the 57 KB brief is the largest of the series). Pass `--max-tokens 24576` for this run so hidden reasoning cannot starve the 30 KB write; if either write lands truncated anyway, rerun fresh rather than resume.
- [ ] **Review** against the gates in the header: apply the brief's pair to the base with the round-trip script and `cmp` `normalize.py`; `cmp` both new files against their blocks; `files_changed` is exactly the three files; host suite 2275 passed; `diff --check`; 3.9 grammar; the §19 invariant tests and import isolation green in the produced test file.
- [ ] **Ledger** `docs/superpowers/bench/2026-09-XX-issue-137-w2-policy-ledger.md` plus the sampler CSV, committed on the run branch.
- [ ] **PR** titled `feat(firewall): issue #137 W2 — policy context, file-target rules, evaluate and fail-closed decide`, body naming the plan, the spec and the ledger; part 2 of 3 for issue #137 (does not close it).

### Worker brief W2

```text
Issue #137 task W2 of 3 (Worker Action Firewall D): add dirtywork/firewall/policy.py, the deterministic policy engine (PolicyContext, Verdict, Outcome, the ordered file-target rules, evaluate, and the fail-closed decide and decide_batch) with its test, and add duplicate_positions to dirtywork/firewall/normalize.py with canonicalize_batch refactored onto it. dirtywork/firewall/shell.py exists from task W1; the rest of the package from issues #135 and #136. Nothing outside the package imports it. The package re-exports are task W3, not this one. Spec: docs/superpowers/specs/2026-09-19-issue-137-firewall-policy-design.md sections 2, 3, 4, 6, 7 and 11 (the texts below are its exact content).

Touch ONLY dirtywork/firewall/normalize.py, dirtywork/firewall/policy.py, tests/test_firewall_policy.py. First apply the ONE edit_file edit below with the exact old and new text (byte for byte; keep indentation and blank lines; the "old:"/"new:" labels and the === marker lines are not part of the text; NEVER use write_file or append_file on an existing file). Then create each NEW file with ONE write_file call whose content is exactly the text between its BEGIN and END marker lines (byte for byte; the file ends with a newline after its last line). Use relative paths exactly as written (never an absolute /work/... path). Line numbers refer to the file before the edit. Do not edit dirtywork/firewall/__init__.py. No other files, no docs, no commits, nothing else.

EDIT edit_file on dirtywork/firewall/normalize.py (old is lines 391-424; the new text is 47 lines). old:
def canonicalize_batch(requests: "Sequence[ActionRequest]") -> "list[Normalization]":
    """Canonicalize a batch of requests in order (spec §9): the first request
    carrying a given `call_id` goes through `canonicalize` normally, whatever
    it decides; every later request whose `call_id` equals an earlier one's
    -- compared with plain `==` on the id as given, before any validation, so
    a non-string id is compared as-is -- is rejected with
    `Rejection(ReasonCode.CALL_ID_DUPLICATE, ...)` naming only its batch
    index, and is never canonicalized. Every other request is independent:
    one request's rejection never affects its neighbours. Only `str` ids
    take part: a non-string id is never a duplicate and is left to
    `check_request`, which rejects it as `call_id_invalid`, so a malformed
    id can neither crash the batch (comparing two deeply nested lists
    recurses) nor be reported as a duplicate of another malformed id."""
    results: "list[Normalization]" = []
    seen: set = set()
    for index, request in enumerate(requests):
        call_id = request.call_id
        is_duplicate = isinstance(call_id, str) and call_id in seen
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
        if isinstance(call_id, str):
            seen.add(call_id)
        results.append(canonicalize(request))
    return results
new:
def duplicate_positions(requests: "Sequence[ActionRequest]") -> "frozenset[int]":
    """The batch indexes whose `call_id` is a `str` equal to an earlier
    request's `str` `call_id` (spec §9): compared with plain `==` on the id
    as given, before any validation, so a non-string id is compared as-is.
    Only `str` ids take part: a non-string id never marks and is never
    marked as a duplicate, so a malformed id can neither crash the batch
    (comparing two deeply nested lists recurses) nor be reported as a
    duplicate of another malformed id. The first request carrying a given
    `call_id` is never included, only every later one that repeats it.
    Never raises."""
    positions: "set[int]" = set()
    seen: set = set()
    for index, request in enumerate(requests):
        call_id = request.call_id
        if not isinstance(call_id, str):
            continue
        if call_id in seen:
            positions.add(index)
        else:
            seen.add(call_id)
    return frozenset(positions)


def canonicalize_batch(requests: "Sequence[ActionRequest]") -> "list[Normalization]":
    """Canonicalize a batch of requests in order (spec §9): the first request
    carrying a given `call_id` goes through `canonicalize` normally, whatever
    it decides; every later request at a `duplicate_positions` index is
    rejected with `Rejection(ReasonCode.CALL_ID_DUPLICATE, ...)` naming only
    its batch index, and is never canonicalized. Every other request is
    independent: one request's rejection never affects its neighbours."""
    positions = duplicate_positions(requests)
    results: "list[Normalization]" = []
    for index, request in enumerate(requests):
        if index in positions:
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
        results.append(canonicalize(request))
    return results

FILE dirtywork/firewall/policy.py (new, 369 lines) — write_file with exactly:
=== BEGIN dirtywork/firewall/policy.py ===
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
=== END dirtywork/firewall/policy.py ===

FILE tests/test_firewall_policy.py (new, 949 lines) — write_file with exactly:
=== BEGIN tests/test_firewall_policy.py ===
"""Tests for dirtywork.firewall.policy: PolicyContext invariants (including
worktree root validation), the file-target rules (backend-aware `.git`/`..`
alias classification included), the bash denylist wiring, evaluate's
fail-hard contract, decide/decide_batch's fail-closed contract, the parent
design §19 invariants this layer owns, and import isolation (spec §11)."""
from __future__ import annotations

import ast
import json
import pathlib
from dataclasses import replace

import pytest

from dirtywork.firewall.bounds import MAX_DETAIL_CHARS
from dirtywork.firewall.capabilities import ActionKind, Capability
from dirtywork.firewall.errors import FirewallInternalError
from dirtywork.firewall.normalize import canonicalize
from dirtywork.firewall.policy import (
    DETAIL_PATH_OUTSIDE_WORKSPACE,
    DETAIL_REPO_METADATA_TARGET,
    Outcome,
    PolicyContext,
    Verdict,
    decide,
    decide_batch,
    evaluate,
)
from dirtywork.firewall.reasons import ReasonCode
from dirtywork.firewall.schema import (
    ActionRequest,
    Decision,
    FirewallEvent,
    SemanticStatus,
    action_identity,
)
from dirtywork.firewall.shell import SHELL_RULES


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


def _action(tool, arguments):
    return canonicalize(_req(tool, arguments)).action


def _ctx_host(*roots: str) -> PolicyContext:
    return PolicyContext(mode="host", worktree_roots=tuple(roots))


def _ctx_docker() -> PolicyContext:
    return PolicyContext(mode="docker", worktree_roots=())


# --- group 1 (PolicyContext invariants) -------------------------------------


def test_policy_context_invariants():
    with pytest.raises(FirewallInternalError):
        PolicyContext(mode="sandbox", worktree_roots=("/x",))
    with pytest.raises(FirewallInternalError):
        PolicyContext(mode="host", worktree_roots=["/x"])
    with pytest.raises(FirewallInternalError):
        PolicyContext(mode="host", worktree_roots=())
    with pytest.raises(FirewallInternalError):
        PolicyContext(mode="docker", worktree_roots=("/x",))
    PolicyContext(mode="host", worktree_roots=("/x",))
    PolicyContext(mode="docker", worktree_roots=())


def test_policy_context_root_validation():
    for roots in (
        ("",),
        ("wt",),
        ("/",),
        ("/wt/",),
        ("/wt/../x",),
        ("/wt", ""),
    ):
        with pytest.raises(FirewallInternalError):
            PolicyContext(mode="host", worktree_roots=roots)
    PolicyContext(mode="host", worktree_roots=("/wt",))
    PolicyContext(mode="host", worktree_roots=("/private/tmp/wt", "/tmp/wt"))


def test_policy_context_empty_root_cannot_allow_etc_passwd():
    # A prior version matched any absolute path's prefix against an empty
    # root string, silently ALLOWing anything outside the worktree; an
    # empty root must now fail construction instead (spec §3).
    with pytest.raises(FirewallInternalError):
        PolicyContext(mode="host", worktree_roots=("",))


# --- group 2 (file-target rules) --------------------------------------------

_PATH_KIND_ARGS = {
    "read_file": lambda p: {"path": p},
    "write_file": lambda p: {"path": p, "content": "x"},
    "append_file": lambda p: {"path": p, "text": "x"},
    "edit_file": lambda p: {"path": p, "old_string": "a", "new_string": "b"},
    "apply_edits": lambda p: {"path": p, "edits": [{"old": "a", "new": "b"}]},
    "insert_before": lambda p: {"path": p, "anchor": "a", "text": "b"},
    "insert_after": lambda p: {"path": p, "anchor": "a", "text": "b"},
    "list_dir": lambda p: {"path": p},
    "grep": lambda p: {"pattern": "x", "path": p},
}


@pytest.mark.parametrize("kind", sorted(_PATH_KIND_ARGS))
def test_benign_relative_path_allowed_for_every_path_kind(kind):
    # No option-like rule any more (decision reversed): a plain relative
    # path -- including one that used to be flagged as option-like, such as
    # a leading "-" -- ALLOWs for every path-bearing ActionKind, in both
    # modes, so kind coverage is preserved (spec §4).
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action(kind, _PATH_KIND_ARGS[kind]("src/thing.py"))
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.ALLOW
        assert v.action is action


def test_git_metadata_read_allow_write_deny():
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        read_action = _action("read_file", {"path": ".git/config"})
        v = evaluate(read_action, ctx)
        assert v.policy.decision is Decision.ALLOW
        assert v.action is read_action

        write_action = _action("write_file", {"path": ".git/config", "content": "x"})
        v = evaluate(write_action, ctx)
        assert v.policy.decision is Decision.DENY
        assert v.policy.reason_code is ReasonCode.REPO_METADATA_TARGET
        assert v.policy.detail == DETAIL_REPO_METADATA_TARGET


def test_gitignore_write_allowed():
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("write_file", {"path": ".gitignore", "content": "x"})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.ALLOW


def test_leading_parent_component_outside_both_modes():
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("read_file", {"path": "../x"})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.DENY
        assert v.policy.reason_code is ReasonCode.PATH_OUTSIDE_WORKSPACE
        assert v.policy.detail == DETAIL_PATH_OUTSIDE_WORKSPACE


def test_absolute_outside_denied_both_modes():
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("read_file", {"path": "/etc/passwd"})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.DENY
        assert v.policy.reason_code is ReasonCode.PATH_OUTSIDE_WORKSPACE


def test_host_absolute_inside_root_allowed():
    ctx = _ctx_host("/wt")
    for path in ("/wt", "/wt/src/x.py"):
        action = _action("read_file", {"path": path})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.ALLOW, path


def test_host_absolute_outside_root_denied():
    ctx = _ctx_host("/wt")
    for path in ("/wtx/y", "/other/x"):
        action = _action("read_file", {"path": path})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.DENY, path
        assert v.policy.reason_code is ReasonCode.PATH_OUTSIDE_WORKSPACE


def test_docker_absolute_always_denied_even_if_root_like():
    ctx = _ctx_docker()
    action = _action("read_file", {"path": "/wt/src/x.py"})
    v = evaluate(action, ctx)
    assert v.policy.decision is Decision.DENY
    assert v.policy.reason_code is ReasonCode.PATH_OUTSIDE_WORKSPACE


def test_parent_ref_shallow_allowed_both_modes():
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("read_file", {"path": "src/a/../x.py"})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.ALLOW, ctx.mode


def test_parent_ref_escaping_denied_both_modes():
    # posixpath.normpath("a/../../x") == "../x": a leading ".." after lexical
    # resolution is outside in both modes now (spec §4).
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("read_file", {"path": "a/../../x"})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.DENY, ctx.mode
        assert v.policy.reason_code is ReasonCode.PATH_OUTSIDE_WORKSPACE


def test_parent_ref_non_dotdot_lookalike_allowed_both_modes():
    # posixpath.normpath("src/../..cache/data") == "../..cache/data" isn't
    # quite right -- "..cache" is a real component, not a ".." reference, so
    # it never denies (spec §4).
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("read_file", {"path": "src/../..cache/data"})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.ALLOW, ctx.mode


def test_parent_ref_git_alias_write_denied_both_modes():
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("write_file", {"path": "src/../.git/config", "content": "x"})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.DENY, ctx.mode
        assert v.policy.reason_code is ReasonCode.REPO_METADATA_TARGET
        assert v.policy.detail == DETAIL_REPO_METADATA_TARGET


def test_host_absolute_git_alias_via_root_write_denied():
    ctx = _ctx_host("/wt")
    action = _action("write_file", {"path": "/wt/.git/config", "content": "x"})
    v = evaluate(action, ctx)
    assert v.policy.decision is Decision.DENY
    assert v.policy.reason_code is ReasonCode.REPO_METADATA_TARGET
    assert v.policy.detail == DETAIL_REPO_METADATA_TARGET


def test_host_absolute_git_alias_via_root_read_allowed():
    ctx = _ctx_host("/wt")
    action = _action("read_file", {"path": "/wt/.git/config"})
    v = evaluate(action, ctx)
    assert v.policy.decision is Decision.ALLOW


def test_host_absolute_gitignore_via_root_write_allowed():
    ctx = _ctx_host("/wt")
    action = _action("write_file", {"path": "/wt/.gitignore", "content": "x"})
    v = evaluate(action, ctx)
    assert v.policy.decision is Decision.ALLOW


def test_host_absolute_internal_parent_ref_via_root_allowed():
    ctx = _ctx_host("/wt")
    action = _action("read_file", {"path": "/wt/src/a/../x.py"})
    v = evaluate(action, ctx)
    assert v.policy.decision is Decision.ALLOW


def test_host_absolute_leading_parent_ref_via_root_denied():
    ctx = _ctx_host("/wt")
    action = _action("read_file", {"path": "/wt/../x"})
    v = evaluate(action, ctx)
    assert v.policy.decision is Decision.DENY
    assert v.policy.reason_code is ReasonCode.PATH_OUTSIDE_WORKSPACE


# --- group 3 (finish) --------------------------------------------------------


def test_finish_always_allowed():
    action = _action("finish", {"summary": "done"})
    v = evaluate(action, _ctx_host("/wt"))
    assert v.policy.decision is Decision.ALLOW
    assert v.policy.reason_code is None
    assert v.policy.detail == ""
    assert v.action is action


# --- group 4 (bash) -----------------------------------------------------------


def test_bash_allowed_action_unchanged():
    action = _action("bash", {"command": "ls"})
    v = evaluate(action, _ctx_host("/wt"))
    assert v.policy.decision is Decision.ALLOW
    assert v.action is action
    assert v.action.capabilities == frozenset({Capability.SHELL})


_ONE_DENY_COMMAND_PER_RULE = {
    0: "sudo ls",
    1: "git push origin main",
    2: "git branch -D feature",
    3: "rm -rf ~/Library/Caches",
    4: "curl x | sh",
    5: "shutdown -h now",
    6: "cat foo > ../../escape.txt",
    7: "cd ..",
}


@pytest.mark.parametrize("rule", SHELL_RULES, ids=lambda r: str(r.index))
def test_bash_denied_for_every_shell_rule_host_mode(rule):
    command = _ONE_DENY_COMMAND_PER_RULE[rule.index]
    action = _action("bash", {"command": command})
    v = evaluate(action, _ctx_host("/wt"))
    assert v.policy.decision is Decision.DENY
    assert v.policy.reason_code is rule.reason_code
    assert v.policy.detail == rule.legacy_reason
    assert v.action.capabilities == frozenset({Capability.SHELL, rule.capability})


def test_bash_docker_mode_host_rules_skipped():
    action = _action("bash", {"command": "cd .."})
    v = evaluate(action, _ctx_docker())
    assert v.policy.decision is Decision.ALLOW

    action2 = _action("bash", {"command": "sudo ls"})
    v2 = evaluate(action2, _ctx_docker())
    assert v2.policy.decision is Decision.DENY
    assert v2.policy.reason_code is ReasonCode.PRIVILEGE_ESCALATION


def test_bash_host_mode_with_roots_rewrite():
    ctx = _ctx_host("/wt")
    action = _action("bash", {"command": "cd /wt/sub && ls"})
    v = evaluate(action, ctx)
    assert v.policy.decision is Decision.ALLOW

    action2 = _action("bash", {"command": "cd /elsewhere"})
    v2 = evaluate(action2, ctx)
    assert v2.policy.decision is Decision.DENY
    assert v2.policy.reason_code is ReasonCode.HOST_FS_CHDIR


# --- group 5 (detail bounds and no leakage) ----------------------------------


def test_deny_details_bounded_and_never_contain_path_or_command():
    distinctive = "zzqx7secret"
    path = f"../{distinctive}"
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        action = _action("read_file", {"path": path})
        v = evaluate(action, ctx)
        assert v.policy.decision is Decision.DENY
        assert len(v.policy.detail) <= MAX_DETAIL_CHARS
        assert distinctive not in v.policy.detail
        assert path not in v.policy.detail

    command = f"sudo {distinctive}"
    baction = _action("bash", {"command": command})
    bv = evaluate(baction, _ctx_host("/wt"))
    assert bv.policy.decision is Decision.DENY
    assert len(bv.policy.detail) <= MAX_DETAIL_CHARS
    assert distinctive not in bv.policy.detail
    assert command not in bv.policy.detail


def test_all_fixed_details_and_legacy_reasons_bounded():
    for detail in (DETAIL_REPO_METADATA_TARGET, DETAIL_PATH_OUTSIDE_WORKSPACE):
        assert len(detail) <= MAX_DETAIL_CHARS
    for rule in SHELL_RULES:
        assert len(rule.legacy_reason) <= MAX_DETAIL_CHARS


# --- group 6 (evaluate raises) ------------------------------------------------


def test_evaluate_raises_on_dict_action():
    with pytest.raises(FirewallInternalError):
        evaluate({"kind": "read_file"}, _ctx_host("/wt"))


def test_evaluate_raises_on_normalization_instead_of_action():
    n = canonicalize(_req("read_file", {"path": "x"}))
    with pytest.raises(FirewallInternalError):
        evaluate(n, _ctx_host("/wt"))


def test_evaluate_raises_on_rejection_instead_of_action():
    n = canonicalize(_req("nope", {}))
    assert n.rejection is not None
    with pytest.raises(FirewallInternalError):
        evaluate(n.rejection, _ctx_host("/wt"))


def test_evaluate_raises_on_dict_context():
    action = _action("read_file", {"path": "x"})
    with pytest.raises(FirewallInternalError):
        evaluate(action, {"mode": "host", "worktree_roots": ()})


def test_evaluate_raises_on_weird_context_mode_for_bash():
    ctx = object.__new__(PolicyContext)
    object.__setattr__(ctx, "mode", "weird")
    object.__setattr__(ctx, "worktree_roots", ())
    action = _action("bash", {"command": "ls"})
    with pytest.raises(FirewallInternalError):
        evaluate(action, ctx)


# --- group 7 (decide) ---------------------------------------------------------


def test_decide_accept_path():
    req = _req("read_file", {"path": "x"})
    outcome = decide(req, _ctx_host("/wt"))
    assert isinstance(outcome, Outcome)
    assert outcome.action is not None
    assert outcome.policy.decision is Decision.ALLOW
    assert outcome.event is not None
    assert outcome.event.stage == "action"
    assert outcome.event.decision is Decision.ALLOW
    assert outcome.dropped_keys == 0


def test_decide_rejection_path():
    req = _req("nope", {})
    outcome = decide(req, _ctx_host("/wt"))
    assert outcome.action is None
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.TOOL_UNKNOWN
    assert outcome.event is not None
    assert outcome.event.stage == "request"
    assert outcome.dropped_keys == 0


def test_decide_dropped_keys_propagate():
    req = _req("read_file", {"path": "x", "bogus": 1})
    outcome = decide(req, _ctx_host("/wt"))
    assert outcome.policy.decision is Decision.ALLOW
    assert outcome.dropped_keys == 1


def test_decide_internal_error_canonicalize_raises(monkeypatch):
    def boom(request):
        raise RuntimeError("secret text")

    monkeypatch.setattr("dirtywork.firewall.policy.canonicalize", boom)
    req = _req("read_file", {"path": "x"})
    outcome = decide(req, _ctx_host("/wt"))
    assert outcome.action is None
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcome.policy.detail == "firewall internal error: RuntimeError"
    assert "secret" not in outcome.policy.detail
    assert outcome.event is not None
    assert outcome.event.stage == "request"


def test_decide_internal_error_evaluate_raises(monkeypatch):
    def boom(action, context):
        raise FirewallInternalError("nope")

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", boom)
    req = _req("read_file", {"path": "x"})
    outcome = decide(req, _ctx_host("/wt"))
    # The canonical action already existed when evaluate raised, so it is
    # retained and the event is action-stage, not discarded down to a
    # request-stage identity (PR #180 review, spec §9.2).
    assert outcome.action == canonicalize(req).action
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcome.policy.detail == "firewall internal error: FirewallInternalError"
    assert outcome.event is not None
    assert outcome.event.stage == "action"


def test_decide_internal_error_double_failure_no_event(monkeypatch):
    def boom_canonicalize(request):
        raise RuntimeError("secret text")

    def boom_event(request, rejection):
        raise RuntimeError("event boom")

    monkeypatch.setattr("dirtywork.firewall.policy.canonicalize", boom_canonicalize)
    monkeypatch.setattr(FirewallEvent, "from_rejection", boom_event)

    req = _req("read_file", {"path": "x"})
    outcome = decide(req, _ctx_host("/wt"))
    assert outcome.action is None
    assert outcome.event is None
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcome.policy.detail.endswith("; no event")
    assert "secret" not in outcome.policy.detail


def test_decide_keyboard_interrupt_propagates(monkeypatch):
    def boom(request):
        raise KeyboardInterrupt()

    monkeypatch.setattr("dirtywork.firewall.policy.canonicalize", boom)
    req = _req("read_file", {"path": "x"})
    with pytest.raises(KeyboardInterrupt):
        decide(req, _ctx_host("/wt"))


# --- group 8 (decide_batch) ---------------------------------------------------


def test_decide_batch_mixed_in_order():
    ctx = _ctx_host("/wt")
    requests = [
        _req("read_file", {"path": "x"}, call_id="c1"),
        _req("bash", {"command": "sudo ls"}, call_id="c2"),
        _req("nope", {}, call_id="c3"),
        _req("read_file", {"path": "y"}, call_id="c1"),  # duplicate call_id
    ]
    outcomes = decide_batch(requests, ctx)
    assert len(outcomes) == 4

    assert outcomes[0].policy.decision is Decision.ALLOW
    assert outcomes[0].action is not None

    assert outcomes[1].policy.decision is Decision.DENY
    assert outcomes[1].policy.reason_code is ReasonCode.PRIVILEGE_ESCALATION

    assert outcomes[2].policy.decision is Decision.DENY
    assert outcomes[2].policy.reason_code is ReasonCode.TOOL_UNKNOWN
    assert outcomes[2].event.stage == "request"

    assert outcomes[3].policy.decision is Decision.DENY
    assert outcomes[3].policy.reason_code is ReasonCode.CALL_ID_DUPLICATE
    assert outcomes[3].event.stage == "request"


def test_decide_batch_one_bash_failure_does_not_affect_neighbours(monkeypatch):
    ctx = _ctx_host("/wt")
    real_evaluate = evaluate

    def wrapped(action, context):
        if action.kind is ActionKind.BASH:
            raise RuntimeError("boom")
        return real_evaluate(action, context)

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", wrapped)

    requests = [
        _req("read_file", {"path": "x"}, call_id="c1"),
        _req("bash", {"command": "ls"}, call_id="c2"),
        _req("read_file", {"path": "y"}, call_id="c3"),
    ]
    outcomes = decide_batch(requests, ctx)
    assert outcomes[0].policy.decision is Decision.ALLOW
    assert outcomes[1].policy.decision is Decision.DENY
    assert outcomes[1].policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcomes[2].policy.decision is Decision.ALLOW


def test_decide_batch_duplicate_positions_raises_every_outcome_internal_error(monkeypatch):
    # decide_batch no longer calls canonicalize_batch; the batch-wide guard
    # is now around duplicate_positions.
    def boom(requests):
        raise RuntimeError("batch boom")

    monkeypatch.setattr("dirtywork.firewall.policy.duplicate_positions", boom)
    requests = [
        _req("read_file", {"path": "x"}, call_id="c1"),
        _req("read_file", {"path": "y"}, call_id="c2"),
    ]
    outcomes = decide_batch(requests, _ctx_host("/wt"))
    assert len(outcomes) == 2
    for outcome in outcomes:
        assert outcome.action is None
        assert outcome.policy.decision is Decision.DENY
        assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
        assert outcome.policy.detail == "firewall internal error: RuntimeError"


def test_decide_batch_canonicalize_raises_for_one_request_only(monkeypatch):
    real_canonicalize = canonicalize

    def wrapped(request):
        if request.call_id == "c2":
            raise RuntimeError("boom")
        return real_canonicalize(request)

    monkeypatch.setattr("dirtywork.firewall.policy.canonicalize", wrapped)

    requests = [
        _req("read_file", {"path": "x"}, call_id="c1"),
        _req("read_file", {"path": "y"}, call_id="c2"),
        _req("read_file", {"path": "z"}, call_id="c3"),
    ]
    outcomes = decide_batch(requests, _ctx_host("/wt"))
    assert outcomes[0].policy.decision is Decision.ALLOW
    assert outcomes[1].policy.decision is Decision.DENY
    assert outcomes[1].policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcomes[2].policy.decision is Decision.ALLOW


def test_decide_batch_duplicate_detection():
    ctx = _ctx_host("/wt")
    requests = [
        _req("read_file", {"path": "x"}, call_id="a"),
        _req("read_file", {"path": "y"}, call_id="b"),
        _req("read_file", {"path": "z"}, call_id="a"),
    ]
    outcomes = decide_batch(requests, ctx)
    assert outcomes[0].policy.decision is Decision.ALLOW
    assert outcomes[1].policy.decision is Decision.ALLOW
    assert outcomes[2].policy.decision is Decision.DENY
    assert outcomes[2].policy.reason_code is ReasonCode.CALL_ID_DUPLICATE


def test_decide_batch_empty():
    assert decide_batch([], _ctx_host("/wt")) == []


# --- group 9 (parent design §19 invariants) -----------------------------------


def test_invariant_raw_dict_cannot_reach_rules():
    with pytest.raises(FirewallInternalError):
        evaluate({"kind": "read_file", "args": {"path": "x"}}, _ctx_host("/wt"))


def test_invariant_malformed_input_cannot_become_allow_through_exception_handling():
    self_ref: dict = {}
    self_ref["self"] = self_ref
    req = ActionRequest(
        call_id="call_1",
        tool_name="read_file",
        arguments=self_ref,
        parse_error=None,
        raw_chars=100,
        turn=1,
        batch_index=0,
        batch_size=1,
    )
    outcome = decide(req, _ctx_host("/wt"))
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.decision is not Decision.ALLOW


def test_invariant_precedence_is_deterministic():
    ctx = _ctx_host("/wt")
    requests = [
        _req("bash", {"command": cmd}, call_id=f"c{i}")
        for i, cmd in enumerate(_ONE_DENY_COMMAND_PER_RULE.values())
    ]
    outcomes1 = decide_batch(requests, ctx)
    outcomes2 = decide_batch(requests, ctx)
    assert outcomes1 == outcomes2


def test_invariant_event_fields_are_members_of_closed_vocabularies():
    ctx = _ctx_host("/wt")
    requests = [
        _req("read_file", {"path": "x"}, call_id="c1"),
        _req("bash", {"command": "sudo ls"}, call_id="c2"),
        _req("nope", {}, call_id="c3"),
        _req("bash", {"command": "ls"}, call_id="c4"),
    ]
    capability_values = {c.value for c in Capability}
    for outcome in decide_batch(requests, ctx):
        event = outcome.event
        assert event is not None
        if event.reason_code is not None:
            assert isinstance(event.reason_code, ReasonCode)
        for cap in event.capabilities:
            assert cap in capability_values
        assert isinstance(event.action_identity, str) and event.action_identity


def test_invariant_semantic_unknown_never_denies_without_a_shell_rule():
    ctx = _ctx_host("/wt")
    shell_codes = {rule.reason_code for rule in SHELL_RULES}

    deny_action = _action("bash", {"command": "sudo ls"})
    v = evaluate(deny_action, ctx)
    assert v.policy.decision is Decision.DENY
    assert v.policy.reason_code in shell_codes

    allow_action = _action("bash", {"command": "ls"})
    v2 = evaluate(allow_action, ctx)
    assert v2.policy.decision is Decision.ALLOW
    assert v2.action.semantic_status is SemanticStatus.UNKNOWN


def test_invariant_internal_failure_fails_closed(monkeypatch):
    def boom(request):
        raise RuntimeError("boom")

    monkeypatch.setattr("dirtywork.firewall.policy.canonicalize", boom)
    req = _req("read_file", {"path": "x"})
    outcome = decide(req, _ctx_host("/wt"))
    assert outcome.policy.decision is Decision.DENY
    assert outcome.action is None


# --- group 10 (import isolation) ----------------------------------------------


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
    for rel in ("dirtywork/firewall/shell.py", "dirtywork/firewall/policy.py"):
        assert _forbidden_imports(root / rel) == [], rel


# --- group 11 (host absolute-inside remainder is resolved lexically) ---------


def test_host_absolute_inside_git_alias_through_dotdot_is_denied():
    verdict = evaluate(_action("write_file", {"path": "/wt/src/../.git/config", "content": "x"}), _ctx_host("/wt"))
    assert verdict.policy.reason_code is ReasonCode.REPO_METADATA_TARGET


def test_host_absolute_inside_escape_through_dotdot_is_denied():
    verdict = evaluate(_action("read_file", {"path": "/wt/a/../../etc/passwd"}), _ctx_host("/wt"))
    assert verdict.policy.reason_code is ReasonCode.PATH_OUTSIDE_WORKSPACE


def test_host_absolute_inside_benign_dotdot_is_allowed():
    verdict = evaluate(_action("read_file", {"path": "/wt/src/a/../x.py"}), _ctx_host("/wt"))
    assert verdict.policy.decision is Decision.ALLOW


# --- group 12 (file-target denials carry their capability, PR #180 review) ---


@pytest.mark.parametrize(
    "mode, roots, kind, path, extra, expected",
    [
        ("docker", (), "write_file", "src/../.git/config", {"content": "x"}, frozenset({"repo_control"})),
        ("docker", (), "read_file", "a/../../x", {}, frozenset({"host_fs"})),
        ("host", ("/wt",), "write_file", "/wt/.git/config", {"content": "x"}, frozenset({"host_fs", "repo_control"})),
        ("host", ("/wt",), "write_file", "/wt/src/../.git/config", {"content": "x"}, frozenset({"repo_control"})),
        ("docker", (), "write_file", ".git/config", {"content": "x"}, frozenset({"repo_control"})),
        ("docker", (), "read_file", "/etc/passwd", {}, frozenset({"host_fs"})),
    ],
    ids=[
        "docker_write_git_alias_via_parent_ref",
        "docker_read_outside_via_leading_dotdot",
        "host_root_git_alias_write",
        "host_root_git_alias_via_parent_ref_write",
        "docker_write_literal_git_metadata_unchanged",
        "docker_read_absolute_outside_unchanged",
    ],
)
def test_file_target_denial_event_capabilities(mode, roots, kind, path, extra, expected):
    ctx = PolicyContext(mode=mode, worktree_roots=roots)
    arguments = {"path": path, **extra}
    outcome = decide(_req(kind, arguments), ctx)
    assert outcome.policy.decision is Decision.DENY
    capabilities = set(outcome.event.to_dict()["capabilities"])
    assert expected <= capabilities


def test_allow_path_verdict_action_is_input_unchanged():
    action = _action("read_file", {"path": "src/thing.py"})
    for ctx in (_ctx_host("/wt"), _ctx_docker()):
        verdict = evaluate(action, ctx)
        assert verdict.policy.decision is Decision.ALLOW
        assert verdict.action == action


def test_deny_path_verdict_action_differs_only_in_capabilities():
    action = _action("write_file", {"path": "src/../.git/config", "content": "x"})
    verdict = evaluate(action, _ctx_docker())
    assert verdict.policy.decision is Decision.DENY
    assert verdict.action != action
    assert replace(verdict.action, capabilities=action.capabilities) == action


# --- group 13 (evaluate-stage internal error keeps the canonical action, PR #180 review) ---


def test_decide_action_internal_error_keeps_action_and_builds_action_stage_event(monkeypatch):
    def boom(action, context):
        raise RuntimeError("secret")

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", boom)
    req = _req("read_file", {"path": "x", "bogus": 1})
    outcome = decide(req, _ctx_host("/wt"))

    expected_action = canonicalize(req).action
    assert outcome.action == expected_action
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcome.policy.detail == "firewall internal error: RuntimeError"
    assert "secret" not in outcome.policy.detail
    assert outcome.event is not None
    assert outcome.event.stage == "action"
    assert outcome.event.kind is ActionKind.READ_FILE
    assert set(outcome.event.capabilities) == {"workspace_read"}
    assert outcome.event.action_identity == action_identity(outcome.action)
    assert outcome.dropped_keys == 1


def test_decide_action_internal_error_identity_distinguishes_different_targets(monkeypatch):
    def boom(action, context):
        raise RuntimeError("boom")

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", boom)
    ctx = _ctx_host("/wt")

    outcome_a1 = decide(_req("read_file", {"path": "a.txt"}), ctx)
    outcome_a2 = decide(_req("read_file", {"path": "a.txt"}), ctx)
    outcome_b = decide(_req("read_file", {"path": "b.txt"}), ctx)

    assert outcome_a1.event.action_identity == outcome_a2.event.action_identity
    assert outcome_a1.event.action_identity != outcome_b.event.action_identity


def test_decide_action_internal_error_double_failure_falls_back_to_request_stage(monkeypatch):
    def boom_evaluate(action, context):
        raise RuntimeError("boom")

    def boom_from_action(action, policy):
        raise RuntimeError("event boom")

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", boom_evaluate)
    monkeypatch.setattr(FirewallEvent, "from_action", boom_from_action)

    req = _req("read_file", {"path": "x"})
    outcome = decide(req, _ctx_host("/wt"))

    assert outcome.action == canonicalize(req).action
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcome.policy.detail == "firewall internal error: RuntimeError"
    assert outcome.event is not None
    assert outcome.event.stage == "request"


def test_decide_action_internal_error_triple_failure_no_event(monkeypatch):
    def boom_evaluate(action, context):
        raise RuntimeError("boom")

    def boom_from_action(action, policy):
        raise RuntimeError("event boom")

    def boom_from_rejection(request, rejection):
        raise RuntimeError("rejection boom")

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", boom_evaluate)
    monkeypatch.setattr(FirewallEvent, "from_action", boom_from_action)
    monkeypatch.setattr(FirewallEvent, "from_rejection", boom_from_rejection)

    req = _req("read_file", {"path": "x"})
    outcome = decide(req, _ctx_host("/wt"))

    assert outcome.action == canonicalize(req).action
    assert outcome.event is None
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert outcome.policy.detail.endswith("; no event")
    assert outcome.policy.detail.startswith("firewall internal error: RuntimeError")


def test_decide_action_internal_error_keyboard_interrupt_propagates(monkeypatch):
    def boom(action, context):
        raise KeyboardInterrupt()

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", boom)
    req = _req("read_file", {"path": "x"})
    with pytest.raises(KeyboardInterrupt):
        decide(req, _ctx_host("/wt"))


def test_decide_batch_action_internal_error_middle_request_only(monkeypatch):
    real_evaluate = evaluate

    def wrapped(action, context):
        if action.args.path == "y":
            raise RuntimeError("boom")
        return real_evaluate(action, context)

    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", wrapped)

    requests = [
        _req("read_file", {"path": "x"}, call_id="c1"),
        _req("read_file", {"path": "y"}, call_id="c2"),
        _req("read_file", {"path": "z"}, call_id="c3"),
    ]
    outcomes = decide_batch(requests, _ctx_host("/wt"))

    assert outcomes[0].policy.decision is Decision.ALLOW
    assert outcomes[0].action is not None

    middle = outcomes[1]
    assert middle.policy.decision is Decision.DENY
    assert middle.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert middle.action is not None
    assert middle.action == canonicalize(requests[1]).action
    assert middle.event is not None
    assert middle.event.stage == "action"

    assert outcomes[2].policy.decision is Decision.ALLOW
    assert outcomes[2].action is not None


# --- group 14 (the internal-error detail is bounded before construction) ----


_LongName = type("E" * 300, (RuntimeError,), {})


@pytest.mark.parametrize("stage", ["canonicalize", "evaluate"])
def test_internal_error_detail_is_bounded_for_a_long_exception_class_name(monkeypatch, stage):
    def boom(*args, **kwargs):
        raise _LongName("x")
    monkeypatch.setattr("dirtywork.firewall.policy." + stage, boom)
    outcome = decide(_req("read_file", {"path": "x"}), _ctx_docker())
    assert outcome.policy.decision is Decision.DENY
    assert outcome.policy.reason_code is ReasonCode.FIREWALL_INTERNAL_ERROR
    assert len(outcome.policy.detail) <= MAX_DETAIL_CHARS
    assert outcome.policy.detail.startswith("firewall internal error: EEEE")
    assert outcome.event is not None


def test_internal_error_detail_with_no_event_suffix_stays_bounded(monkeypatch):
    def boom(*args, **kwargs):
        raise _LongName("x")
    monkeypatch.setattr("dirtywork.firewall.policy.evaluate", boom)
    monkeypatch.setattr(FirewallEvent, "from_action", boom)
    monkeypatch.setattr(FirewallEvent, "from_rejection", boom)
    outcome = decide(_req("read_file", {"path": "x"}), _ctx_docker())
    assert outcome.event is None
    assert outcome.policy.detail.endswith("; no event")
    assert len(outcome.policy.detail) <= MAX_DETAIL_CHARS
=== END tests/test_firewall_policy.py ===

VERIFY: run python3 -m pytest -q -p no:cacheprovider tests/test_firewall_policy.py tests/test_firewall_normalize.py and expect 317 passed (87 + 230). Then run python3 -m pytest -q -p no:cacheprovider with timeout=300 and expect all passed, 0 failed (2275 passed). Finish when both pass.
```

---
### Task 3: re-exports and the `__all__` pin

**Files:**
- Modify: `dirtywork/firewall/__init__.py:14-15` (the `.policy` import), `:38-40` (the `.shell` import after the `schema` block), `:51-52` (`__all__`); `tests/test_firewall_request.py:297-298` (the `__all__` pin grows to 48 names)

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: the package root re-exports `PolicyContext`, `Verdict`, `Outcome`, `evaluate`, `decide`, `decide_batch`, `SHELL_RULES` and `analyze_command`; `__all__` has 48 names (spec §2).

- [x] **Dry-run on the scratch clone** (2026-09-19, on top of Task 2): the four edits below applied, `tests/test_firewall_request.py` 40 passed with the grown pin, `len(dirtywork.firewall.__all__) == 48` and every name resolves, full host suite 2275 passed (no new tests), `ast.parse(..., feature_version=(3, 9))` silent, `git diff --check` clean.
- [ ] **Confirm the base.** Task 2's PR merged (or its run branch as `--branch-from`); the four anchors below must still be at the quoted line numbers.
- [ ] **Launch** the brief below verbatim through the invocation in the header, sampler on. Four `edit_file` calls; an `apply_edits` in place of several `edit_file`s is fine.
- [ ] **Review** against the gates in the header: apply the brief's four pairs to the base with the round-trip script and `cmp` both files; `files_changed` is exactly the two files; host suite 2275 passed; `diff --check`; 3.9 grammar; `python3 -c "import dirtywork.firewall as f; assert len(f.__all__) == 48"` from the run's worktree.
- [ ] **Ledger** `docs/superpowers/bench/2026-09-XX-issue-137-w3-reexports-ledger.md` plus the sampler CSV, committed on the run branch.
- [ ] **PR** titled `feat(firewall): issue #137 W3 — re-exports and the __all__ pin`, body naming the plan, the spec and the ledger; part 3 of 3; **closes #137**.
- [ ] After merge: comment on issue #137 with the three ledgers; issue #138 (Runner integration) is next and is briefed against the merged package.

### Worker brief W3

```text
Issue #137 task W3 of 3 (Worker Action Firewall D): re-export the eight new policy and shell names from dirtywork/firewall/__init__.py and grow the __all__ pin in tests/test_firewall_request.py to match (40 to 48 names). shell.py (W1) and policy.py (W2) exist. Nothing outside the package imports it. Spec: docs/superpowers/specs/2026-09-19-issue-137-firewall-policy-design.md section 2 (the texts below are its exact content).

Touch ONLY dirtywork/firewall/__init__.py, tests/test_firewall_request.py. Apply exactly the FOUR edit_file edits below, each with the exact old and new text (byte for byte; keep indentation and blank lines; the "old:"/"new:" labels and the === marker lines are not part of the text; NEVER use write_file or append_file). Use relative paths exactly as written (never an absolute /work/... path). Line numbers refer to the files before any of these edits. No other files, no docs, no commits, nothing else.

EDIT edit_file on dirtywork/firewall/__init__.py (old is lines 14-15; the new text is 3 lines). old:
from .paths import NormalizedPath, TargetClass, normalize_path
from .reasons import ReasonClass, ReasonCode, reason_class
new:
from .paths import NormalizedPath, TargetClass, normalize_path
from .policy import Outcome, PolicyContext, Verdict, decide, decide_batch, evaluate
from .reasons import ReasonClass, ReasonCode, reason_class

EDIT edit_file on dirtywork/firewall/__init__.py (old is lines 38-40; the new text is 4 lines). old:
)

__all__ = [
new:
)
from .shell import SHELL_RULES, analyze_command

__all__ = [

EDIT edit_file on dirtywork/firewall/__init__.py (old is lines 51-52; the new text is 4 lines). old:
    "NormalizedPath", "TargetClass", "normalize_path",
]
new:
    "NormalizedPath", "TargetClass", "normalize_path",
    "PolicyContext", "Verdict", "Outcome", "evaluate", "decide", "decide_batch",
    "SHELL_RULES", "analyze_command",
]

EDIT edit_file on tests/test_firewall_request.py (old is lines 297-298; the new text is 4 lines). old:
        "NormalizedPath", "TargetClass", "normalize_path",
    ]
new:
        "NormalizedPath", "TargetClass", "normalize_path",
        "PolicyContext", "Verdict", "Outcome", "evaluate", "decide", "decide_batch",
        "SHELL_RULES", "analyze_command",
    ]

VERIFY: run python3 -m pytest -q -p no:cacheprovider tests/test_firewall_request.py tests/test_firewall_policy.py tests/test_firewall_shell.py and expect 135 passed (40 + 87 + 8). Then run python3 -c "import dirtywork.firewall as f; assert len(f.__all__) == 48; [getattr(f, n) for n in f.__all__]" and expect no output. Then run python3 -m pytest -q -p no:cacheprovider with timeout=300 and expect all passed, 0 failed (2275 passed). Finish when all pass.
```
