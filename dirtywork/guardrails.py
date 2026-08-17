from __future__ import annotations

import os
import re
import site
import sys
from pathlib import Path


class GuardrailError(Exception):
    """Raised when a tool call violates a containment rule."""


def resolve_in_worktree(path_str: str, worktree: Path, writing: bool = False) -> Path:
    """Resolve a tool-supplied path and require it to land inside the worktree.

    Symlinks are followed (Path.resolve), so a link pointing outside is caught.
    For not-yet-existing paths, resolve() still normalizes .. components.
    """
    wt = worktree.resolve()
    raw = Path(path_str)
    candidate = raw if raw.is_absolute() else wt / raw
    resolved = candidate.resolve()

    if not (resolved == wt or wt in resolved.parents):
        raise GuardrailError(
            f"Path '{path_str}' resolves outside the worktree ({resolved}). "
            f"Use paths relative to the worktree root."
        )
    if writing:
        rel_parts = resolved.relative_to(wt).parts
        if rel_parts and rel_parts[0] == ".git":
            raise GuardrailError(
                f"Writing inside .git/ is not allowed (got '{path_str}')."
            )
    return resolved

# (reason, pattern) — case-insensitive. BEST-EFFORT accident guards, NOT a
# security boundary: bash is a general shell, so a determined or prompt-injected
# model can still read absolute host paths or obfuscate its way past these. The
# real containment is OS-level sandboxing (see SECURITY.md); these only raise the
# bar for a *confused* model. The escape-target rules match the natural accident
# forms — absolute (/), home (~), and parent-relative (..) — since a worktree at
# <repo>/.worktrees/dw-<slug> is escaped by `../..`. We deliberately do NOT match
# a leading `$`: HOME is relocated into the worktree (so $HOME/~ stay confined),
# and a blanket `$` would reject ordinary idioms like `rm -rf "$BUILD_DIR"`.
#
# WHY the cd/pushd and redirect rules below get a worktree-aware rewrite before
# they run: absolute paths INTO the worktree are legitimate — local models cd
# there by absolute path constantly (`cd /abs/repo/.worktrees/dw-slug && pytest`)
# — while everything else absolute (or parent-relative past the root) stays
# blocked. See check_bash_command().
#
# `(?:\./)*` prefix on the escape target: rewriting the worktree root to `.`
# can leave a real escape directly behind it, e.g. `cd /wt/../x` rewrites to
# `cd ./../x` — the target must still match the `..` past that leading `./`.
# NOTE ON SCOPE: none of this — including the git-subcommand rules below —
# blocks a model from writing outside the worktree via an *interpreter*, e.g.
# `python3 -c "open('/tmp/x','w').write('y')"`. Enumerating every interpreter's
# write primitive is not a regex-shaped problem. Host mode (`--sandbox none`)
# does not close that gap; the fix is a real OS process boundary (the Docker
# sandbox, sub-project 2), not a bigger denylist. Documented in README.md and
# SECURITY.md.
#
# ONE ORDERED LIST, because the reported reason for a two-rule-match command
# is a documented transcript field (`guardrail_block.reason`) an orchestrating
# agent may key on — the scan ORDER IS THE CONTRACT, matching main's original
# order exactly (see git show 23a9c22:dirtywork/guardrails.py). Each entry is
# tagged with a scope instead of being split into separate lists, so adding
# docker-mode filtering never reorders anything:
#   - "always" is mode-independent POLICY (not containment): no push (leave
#     changes uncommitted for review), no sudo, no piping a download into an
#     interpreter, no system-control commands. These hold regardless of how
#     the command is contained, so they apply in both modes.
#   - "host" rules exist to protect the HOST filesystem and the host repo's
#     shared refs/config (the git config/remote/update-ref/gc/filter-branch/
#     reflog/worktree/branch -d|-D|-m|-M/tag -d rules, plus the
#     rm/mv/chmod/chown/redirect/cd/pushd escape-target rules). In docker
#     mode the container is its own filesystem with its own throwaway
#     /gitdir (see lifecycle.init_worker_git) — there is no host filesystem
#     or shared parent repo for a docker-mode command to reach, so these
#     rules would just be false positives there. The real boundary in docker
#     mode is the container itself (--network none, --read-only rootfs,
#     --cap-drop ALL, no host path mounted in but a read-only object store
#     copy — see SECURITY.md); check_bash_command(sandboxed=True) scans only
#     the "always" subset (in this same original order) and skips the
#     worktree-rewrite step (there is no worktree path for the container's
#     commands to be rewritten against).
_ESCAPE_TARGET = r"(?:\./)*(?:/|~|\.\.)"
# git accepts global options (-C <path>, -c <key>=<value>, --<flag>[=value],
# -<x>) before the subcommand. The old \bgit\s+<subcommand> rules didn't skip
# these, so `git -C ../.. config ...` or `git -c core.hooksPath=x push` had the
# exact same effect as the plain form but slipped past the denylist. Every
# git-subcommand rule below is prefixed with this instead of a bare `\bgit\s+`.
_GIT_OPTS = r"\bgit\s+(?:(?:-C\s+\S+|-c\s+\S+|--\S+|-[A-Za-z]\S*)\s+)*"
_RULES: list[tuple[str, str, str]] = [  # (scope, reason, pattern) — ORDER IS THE CONTRACT
    ("always", "sudo is not allowed", r"\bsudo\b"),
    ("always", "git push is not allowed — leave changes uncommitted for review",
     _GIT_OPTS + r"push\b"),
    # A linked worktree SHARES refs/config/objects with the parent repo, so
    # these git subcommands mutate the parent's state from inside the
    # worktree. core.hooksPath in particular is a persistent
    # host-code-execution pivot. Read-only forms (config --get/--list,
    # remote -v, worktree list, bare reflog) are intentionally NOT matched.
    ("host", "git command that writes the parent repo's shared refs/config is not allowed",
     # config: allowlist the read forms (--get*/--list/-l) and block everything
     # else. Enumerating write flags is whack-a-mole (--local/--global/--system/
     # --file/--unset/… all write shared config from a linked worktree), so we
     # invert it: block `git config` unless a read flag precedes the next separator.
     _GIT_OPTS + r"config\b(?![^;|&]*\s(?:--get\S*|--list|-l)\b)"
     r"|" + _GIT_OPTS + r"remote\s+(add|set-url|remove|rm|rename)\b"
     r"|" + _GIT_OPTS + r"(update-ref|gc|filter-branch)\b"
     r"|" + _GIT_OPTS + r"reflog\s+(expire|delete)\b"
     r"|" + _GIT_OPTS + r"worktree\s+(add|remove|prune|move)\b"
     r"|" + _GIT_OPTS + r"branch\s+(-[dDmM]\b|--(delete|move)\b)"
     r"|" + _GIT_OPTS + r"tag\s+(-d\b|--delete\b)"),
    ("host", "destructive command targeting a path outside the worktree",
     r"\b(rm|mv|chmod|chown)\b[^|;&]*\s['\"]?" + _ESCAPE_TARGET),
    ("always", "piping a download into an interpreter is not allowed",
     r"\b(curl|wget)\b[^|;&]*\|\s*['\"]?\w*\s*"
     r"((ba|z|da)?sh|python[0-9.]*|node|ruby|perl)\b"),
    ("always", "system-control commands are not allowed",
     r"\b(osascript|launchctl|shutdown|reboot|killall)\b"),
    ("host", "redirecting output outside the worktree is not allowed",
     r">>?\s*['\"]?(?!/dev/null)" + _ESCAPE_TARGET),
    ("host", "changing directory out of the worktree is not allowed",
     r"\b(cd|pushd)\s+['\"]?" + _ESCAPE_TARGET),
]

_COMPILED = [(scope, reason, re.compile(pat, re.IGNORECASE)) for scope, reason, pat in _RULES]


_ROOT_BOUNDARY = r"""(?=[/\s'"]|$)"""  # root must end at a path/word boundary


def _rewrite_worktree_refs(command: str, worktree: Path) -> str:
    """Rewrite absolute references to the worktree root to a relative `.` form.

    Tries both str(worktree) and str(worktree.resolve()) as roots (they can
    differ, e.g. macOS /tmp vs /private/tmp) so a caller-supplied symlinked
    worktree path and its resolved form are both recognized. Only rewrites
    the STRING BEING CHECKED, never the command that actually executes.
    """
    roots = []
    for root in (str(worktree), str(worktree.resolve())):
        if root not in roots:
            roots.append(root)
    roots.sort(key=len, reverse=True)  # longer (more specific) root first

    checked = command
    for root in roots:
        checked = re.sub(re.escape(root) + _ROOT_BOUNDARY, ".", checked)
    return checked


def check_bash_command(
    command: str, worktree: Path | None = None, *, sandboxed: bool = False
) -> str | None:
    """Return a rejection reason if the command matches the denylist, else None.

    When `worktree` is given, absolute references to the worktree root are
    rewritten to a relative `.` form before the denylist runs, so cd-ing or
    redirecting INTO the worktree by absolute path is allowed while escapes
    past the root are still blocked. See the WHY comment above _RULES.

    When `sandboxed=True` (docker mode), only the "always"-scoped rules are
    checked (in the same original relative order) and the worktree rewrite
    is skipped — the container is the boundary that makes the "host"-scoped
    rules meaningless (there is no host filesystem or shared parent repo for
    a docker-mode command to reach). See the WHY comment above _RULES.

    Host mode (sandboxed=False, the default) scans every rule in the
    ORIGINAL order (matching main's pre-docker-mode order exactly), because
    `guardrail_block.reason` is a documented transcript field an
    orchestrating agent may key on — which rule matches first for a
    two-rule-match command must not change.
    """
    checked = command if (sandboxed or worktree is None) else _rewrite_worktree_refs(command, worktree)
    for scope, reason, rx in _COMPILED:
        if sandboxed and scope != "always":
            continue
        if rx.search(checked):
            return f"BLOCKED: {reason}. Rework the command to stay inside the worktree."
    return None


def build_env(home: str | Path) -> dict:
    """Environment for bash subprocesses.

    Env-var secrets from the parent shell (API keys, tokens) are NOT inherited —
    only PATH/TERM/LANG/TMPDIR are kept so tools stay runnable. HOME is redirected
    to ``home`` (the worktree) so ``~`` and ``$HOME`` resolve INSIDE the worktree
    instead of the operator's home directory — closing the easy secret-read/exfil
    path (``~/.ssh``, ``~/.aws``, ``~/.netrc``). Build caches that key on ``$HOME``
    land in the worktree as a result, which is the intended, more-hermetic trade.

    The operator's Python user site-packages (``pip install --user`` — where
    pytest usually lives) is put on PYTHONPATH so it stays importable under the
    redirected HOME; writes still go to the worktree because ``pip --user`` keys
    on HOME, not PYTHONPATH.

    This is NOT a sandbox: bash is a general shell and can still reference absolute
    host paths (``cat /etc/...``). See SECURITY.md for the real containment story.
    """
    keep = ("PATH", "TERM", "LANG", "TMPDIR")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["HOME"] = str(home)
    user_site = _operator_user_site()
    if user_site is not None:
        env["PYTHONPATH"] = user_site
    return env


def _operator_user_site() -> str | None:
    """The operator's per-user site-packages directory (where `pip install
    --user` puts pytest), or None when user site was explicitly disabled
    (`python -s` / PYTHONNOUSERSITE) or the directory does not exist.

    Deliberately NOT gated on site.ENABLE_USER_SITE: that flag is False inside
    every virtualenv — including the pipx venv dirtywork itself installs into —
    while the *worker's* python3 is the system/default interpreter whose user
    site is exactly what should stay reachable. Best effort: assumes the
    interpreter running dirtywork and the worker's `python3` are the same
    version (true for pipx with the default python and for the repo launcher);
    otherwise the computed directory simply does not exist and nothing is added."""
    if sys.flags.no_user_site or os.environ.get("PYTHONNOUSERSITE"):
        return None
    try:
        path = site.getusersitepackages()
    except Exception:  # pragma: no cover - platform oddities; never block a run on this
        return None
    return path if os.path.isdir(path) else None
