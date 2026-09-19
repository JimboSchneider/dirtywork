"""Worker Action Firewall: canonical schema, bounds, capabilities and reason codes."""
from __future__ import annotations

from .bounds import FIREWALL_SCHEMA_VERSION, IDENTITY_VERSION
from .capabilities import (
    ActionKind,
    BASE_CAPABILITIES,
    Capability,
    FILE_TARGET_RULES,
    LEGACY_RULES,
)
from .errors import FirewallInternalError
from .reasons import ReasonClass, ReasonCode, reason_class
from .request import Rejection, check_request
from .schema import (
    ActionRequest,
    ApplyEditsArgs,
    AppendFileArgs,
    BashArgs,
    CanonicalAction,
    CanonicalArgs,
    Decision,
    Edit,
    EditFileArgs,
    FinishArgs,
    FirewallEvent,
    GrepArgs,
    InsertArgs,
    ListDirArgs,
    PolicyDecision,
    ReadFileArgs,
    SemanticStatus,
    WriteFileArgs,
    action_identity,
    rejection_identity,
)

__all__ = [
    "FIREWALL_SCHEMA_VERSION", "IDENTITY_VERSION",
    "FirewallInternalError",
    "ReasonClass", "ReasonCode", "reason_class",
    "ActionKind", "Capability", "BASE_CAPABILITIES", "LEGACY_RULES", "FILE_TARGET_RULES",
    "ActionRequest", "Edit", "ReadFileArgs", "WriteFileArgs", "AppendFileArgs", "EditFileArgs",
    "ApplyEditsArgs", "InsertArgs", "ListDirArgs", "GrepArgs", "BashArgs", "FinishArgs",
    "CanonicalArgs", "CanonicalAction", "Decision", "SemanticStatus", "PolicyDecision",
    "FirewallEvent", "action_identity", "rejection_identity",
    "Rejection", "check_request",
]
