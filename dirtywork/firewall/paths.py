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
