from __future__ import annotations

from dirtywork.firewall.capabilities import (
    BASE_CAPABILITIES,
    FILE_TARGET_RULES,
    ActionKind,
    Capability,
    LEGACY_RULES,
)
from dirtywork.firewall.reasons import ReasonCode

EXPECTED_ACTION_KINDS = sorted(
    [
        "read_file",
        "write_file",
        "append_file",
        "edit_file",
        "apply_edits",
        "insert_before",
        "insert_after",
        "list_dir",
        "grep",
        "bash",
        "finish",
    ]
)

EXPECTED_CAPABILITIES = sorted(
    [
        "workspace_read",
        "workspace_write",
        "shell",
        "run_control",
        "repo_control",
        "repo_publish",
        "host_fs",
        "privilege",
        "system_control",
        "network",
        "remote_code_exec",
    ]
)

# (index, capability, reason_code) from spec §5.3, in guardrails._RULES order.
EXPECTED_LEGACY_RULES = (
    (0, Capability.PRIVILEGE, ReasonCode.PRIVILEGE_ESCALATION),
    (1, Capability.REPO_PUBLISH, ReasonCode.REPO_PUBLISH),
    (2, Capability.REPO_CONTROL, ReasonCode.REPO_CONTROL),
    (3, Capability.HOST_FS, ReasonCode.HOST_FS_DESTRUCTIVE),
    (4, Capability.REMOTE_CODE_EXEC, ReasonCode.REMOTE_CODE_EXEC),
    (5, Capability.SYSTEM_CONTROL, ReasonCode.SYSTEM_CONTROL),
    (6, Capability.HOST_FS, ReasonCode.HOST_FS_REDIRECT),
    (7, Capability.HOST_FS, ReasonCode.HOST_FS_CHDIR),
)


def test_action_kind_vocabulary_pin():
    assert sorted(m.value for m in ActionKind) == EXPECTED_ACTION_KINDS


def test_capability_vocabulary_pin():
    assert sorted(m.value for m in Capability) == EXPECTED_CAPABILITIES


def test_action_kind_lockstep_with_registry():
    from dirtywork.builtin_tools import BUILTIN_SPECS

    assert [k.value for k in ActionKind] == [s.name for s in BUILTIN_SPECS]


def test_legacy_rules_lockstep_with_guardrails():
    from dirtywork.guardrails import _RULES

    assert len(LEGACY_RULES) == len(_RULES)
    assert [t[0] for t in LEGACY_RULES] == list(range(8))
    assert LEGACY_RULES == EXPECTED_LEGACY_RULES

    reason_codes = [t[2] for t in LEGACY_RULES]
    assert len(set(reason_codes)) == len(reason_codes) == 8


def test_file_target_rules_pin():
    assert FILE_TARGET_RULES == (
        (Capability.REPO_CONTROL, ReasonCode.REPO_METADATA_TARGET),
        (Capability.HOST_FS, ReasonCode.PATH_OUTSIDE_WORKSPACE),
    )


def test_base_capabilities_totality():
    for kind in ActionKind:
        assert kind in BASE_CAPABILITIES
        assert len(BASE_CAPABILITIES[kind]) > 0


def test_every_capability_is_accounted_for():
    covered = set()
    for caps in BASE_CAPABILITIES.values():
        covered |= caps
    for _, cap, _ in LEGACY_RULES:
        covered.add(cap)
    for cap, _ in FILE_TARGET_RULES:
        covered.add(cap)
    covered.add(Capability.NETWORK)

    for cap in Capability:
        assert cap in covered
