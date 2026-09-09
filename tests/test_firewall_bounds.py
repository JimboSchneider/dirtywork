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
