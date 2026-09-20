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
