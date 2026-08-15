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

# (reason, pattern) — case-insensitive. Blocks accidents, not adversaries.
_DENYLIST: list[tuple[str, str]] = [
    ("sudo is not allowed", r"\bsudo\b"),
    ("git push is not allowed — leave changes uncommitted for review",
     r"\bgit\s+push\b"),
    ("destructive command targeting an absolute or home path",
     r"\b(rm|mv|chmod|chown)\b[^|;&]*\s['\"]?(/|~)"),
    ("piping a download into a shell is not allowed",
     r"\b(curl|wget)\b[^|;&]*\|\s*['\"]?\w*\s*(ba|z|da)?sh\b"),
    ("system-control commands are not allowed",
     r"\b(osascript|launchctl|shutdown|reboot|killall)\b"),
    ("redirecting output to an absolute or home path outside the worktree",
     r">>?\s*['\"]?(?!/dev/null)(/|~)"),
    ("changing directory out of the worktree is not allowed",
     r"\b(cd|pushd)\s+['\"]?(/|~|\.\.)"),
]

_COMPILED = [(reason, re.compile(pat, re.IGNORECASE)) for reason, pat in _DENYLIST]


def check_bash_command(command: str) -> str | None:
    """Return a rejection reason if the command matches the denylist, else None."""
    for reason, rx in _COMPILED:
        if rx.search(command):
            return f"BLOCKED: {reason}. Rework the command to stay inside the worktree."
    return None


def build_env() -> dict:
    """Minimal env for bash subprocesses — parent shell secrets are not inherited."""
    keep = ("PATH", "HOME", "TERM", "LANG", "TMPDIR")
    return {k: os.environ[k] for k in keep if k in os.environ}
