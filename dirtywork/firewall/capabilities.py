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
