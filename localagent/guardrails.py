from __future__ import annotations

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
