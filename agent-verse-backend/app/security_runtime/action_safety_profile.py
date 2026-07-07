"""ActionSafetyProfile — per-action risk assessment (spec §Layer 1).

Determines the safety level for each tool call:
  SAFE         — execute directly, no extra gates
  LOG_ONLY     — execute + audit log
  HITL_REQUIRED — pause for human approval
  BLOCKED      — hard block, no approval path

Based on: tool name, tool args, connector risk, action patterns.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any


class ActionSafetyLevel(str, enum.Enum):
    SAFE = "safe"
    LOG_ONLY = "log_only"
    HITL_REQUIRED = "hitl"
    BLOCKED = "blocked"


_DESTRUCTIVE_RE = re.compile(
    r"(?i)\b(delete|drop|truncate|destroy|wipe|purge|rm -rf)\b"
)
_WRITE_HIGH_RE = re.compile(
    r"(?i)\b(deploy|publish|release|send|charge|payment|grant admin|revoke)\b"
)


@dataclass
class ActionSafetyProfile:
    """Per-action safety determination."""
    tool_name: str
    safety_level: ActionSafetyLevel
    requires_hitl: bool
    rollback_registered: bool
    audit_required: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "safety_level": self.safety_level.value,
            "requires_hitl": self.requires_hitl,
            "rollback_registered": self.rollback_registered,
            "audit_required": self.audit_required,
            "reason": self.reason,
        }


class ActionSafetyProfileSelector:
    """Selects ActionSafetyProfile based on tool name, args, and risk level."""

    def select(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        risk_level: str = "low",
    ) -> ActionSafetyProfile:
        combined = f"{tool_name} {' '.join(str(v) for v in tool_args.values())}"

        if risk_level == "critical" or _DESTRUCTIVE_RE.search(combined):
            return ActionSafetyProfile(
                tool_name=tool_name, safety_level=ActionSafetyLevel.HITL_REQUIRED,
                requires_hitl=True, rollback_registered=True, audit_required=True,
                reason="destructive or critical-risk action",
            )
        if risk_level == "high" or _WRITE_HIGH_RE.search(combined):
            return ActionSafetyProfile(
                tool_name=tool_name, safety_level=ActionSafetyLevel.HITL_REQUIRED,
                requires_hitl=True, rollback_registered=True, audit_required=True,
                reason="write_high risk action",
            )
        if risk_level == "medium":
            return ActionSafetyProfile(
                tool_name=tool_name, safety_level=ActionSafetyLevel.LOG_ONLY,
                requires_hitl=False, rollback_registered=True, audit_required=True,
                reason="write_low risk — log and execute",
            )
        return ActionSafetyProfile(
            tool_name=tool_name, safety_level=ActionSafetyLevel.SAFE,
            requires_hitl=False, rollback_registered=False, audit_required=False,
            reason="read-only or safe action",
        )
