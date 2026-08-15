from __future__ import annotations

import os
import re
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
# bar for a *confused* model. The escape-target rules therefore match the natural
# accident forms — absolute (/), home (~), parent-relative (..), and env-var
# indirection ($HOME/$VAR) — since a worktree at <repo>/.worktrees/dw-<slug> is
# escaped by `../..`, and $HOME expands to the operator's home.
_ESCAPE_TARGET = r"(/|~|\.\.|\$)"
_DENYLIST: list[tuple[str, str]] = [
    ("sudo is not allowed", r"\bsudo\b"),
    ("git push is not allowed — leave changes uncommitted for review",
     r"\bgit\s+push\b"),
    # A linked worktree SHARES refs/config/objects with the parent repo, so these
    # git subcommands mutate the parent's state from inside the worktree.
    # core.hooksPath in particular is a persistent host-code-execution pivot.
    ("git command that writes the parent repo's shared refs/config/tags is not allowed",
     r"\bgit\s+(config|update-ref|reflog|gc|worktree|filter-branch)\b"
     r"|\bgit\s+branch\s+-[dD]\b|\bgit\s+tag\s+-d\b"),
    ("destructive command targeting a path outside the worktree",
     r"\b(rm|mv|chmod|chown)\b[^|;&]*\s['\"]?" + _ESCAPE_TARGET),
    ("piping a download into an interpreter is not allowed",
     r"\b(curl|wget)\b[^|;&]*\|\s*['\"]?\w*\s*"
     r"((ba|z|da)?sh|python[0-9.]*|node|ruby|perl)\b"),
    ("system-control commands are not allowed",
     r"\b(osascript|launchctl|shutdown|reboot|killall)\b"),
    ("redirecting output outside the worktree is not allowed",
     r">>?\s*['\"]?(?!/dev/null)" + _ESCAPE_TARGET),
    ("changing directory out of the worktree is not allowed",
     r"\b(cd|pushd)\s+['\"]?" + _ESCAPE_TARGET),
]

_COMPILED = [(reason, re.compile(pat, re.IGNORECASE)) for reason, pat in _DENYLIST]


def check_bash_command(command: str) -> str | None:
    """Return a rejection reason if the command matches the denylist, else None."""
    for reason, rx in _COMPILED:
        if rx.search(command):
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

    This is NOT a sandbox: bash is a general shell and can still reference absolute
    host paths (``cat /etc/...``). See SECURITY.md for the real containment story.
    """
    keep = ("PATH", "TERM", "LANG", "TMPDIR")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["HOME"] = str(home)
    return env
