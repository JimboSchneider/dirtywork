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
