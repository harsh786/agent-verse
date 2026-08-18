"""SUPPLEMENT A — Autonomy Levels L0-L5 (Detailed).

Enforces the autonomy level constraints for every action in the org system.
Configurable at: Organization → Department → Team → Agent → Action
(most specific wins — per spec SUPPLEMENT A)

L0 — OBSERVE:         No actions. Shadow mode only.
L1 — RECOMMEND:       Proposes actions; human approves everything.
L2 — SUPERVISED:      Executes read-only + drafts; blocks all writes.
L3 — STANDARD:        Autonomous with configurable approval gates.
L4 — AUTONOMOUS:      Highly autonomous within policy.
L5 — MISSION LEVEL:   End-to-end autonomous on submitted goals.

Hard limits at ALL autonomy levels (never auto):
  - Production infrastructure destruction
  - Mass deletion of customer data
  - External financial transfers > $10k
  - Binding legal agreements
  - Press releases / public communications
  - Mass PII bulk operations
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

import structlog
from fastapi import HTTPException, status
from opentelemetry import trace

from app.observability.logging import get_logger

_log = get_logger(__name__)
_tracer = trace.get_tracer(__name__)


class AutonomyLevel(IntEnum):
    L0_OBSERVE          = 0
    L1_RECOMMEND        = 1
    L2_SUPERVISED       = 2
    L3_STANDARD         = 3
    L4_AUTONOMOUS       = 4
    L5_MISSION          = 5


# ── Hard limits — NEVER auto-execute at any autonomy level ────────────────────

HARD_LIMITS: frozenset[str] = frozenset({
    "production_infra_destruction",
    "mass_customer_data_deletion",
    "external_financial_transfer_gt_10k",
    "binding_legal_agreements",
    "press_releases",
    "public_communications",
    "mass_pii_bulk_operations",
    "database_schema_modification",
})


@dataclass
class AutonomyLevelConfig:
    """Detailed config for a single autonomy level."""
    level: AutonomyLevel
    label: str
    description: str
    can_execute_read: bool
    can_execute_write: bool
    can_send_external: bool
    can_deploy: bool
    approval_required_for: list[str] = field(default_factory=list)
    blocked_actions: list[str] = field(default_factory=list)
    use_when: str = ""


# ── Per-level configs ─────────────────────────────────────────────────────────

AUTONOMY_CONFIGS: dict[int, AutonomyLevelConfig] = {
    0: AutonomyLevelConfig(
        level=AutonomyLevel.L0_OBSERVE,
        label="L0 — Observe",
        description="Shadow mode. Produces observation reports only. No actions taken.",
        can_execute_read=False,
        can_execute_write=False,
        can_send_external=False,
        can_deploy=False,
        approval_required_for=["EVERYTHING"],
        blocked_actions=["all_actions", "read_operations", "write_operations"],
        use_when="First deploy of sensitive departments (Legal, Finance, Security)",
    ),
    1: AutonomyLevelConfig(
        level=AutonomyLevel.L1_RECOMMEND,
        label="L1 — Recommend",
        description="Analyzes and proposes actions. Never executes independently.",
        can_execute_read=False,
        can_execute_write=False,
        can_send_external=False,
        can_deploy=False,
        approval_required_for=["all_actions"],
        use_when="Auditable contexts, compliance-heavy operations",
    ),
    2: AutonomyLevelConfig(
        level=AutonomyLevel.L2_SUPERVISED,
        label="L2 — Supervised",
        description="Executes read-only actions and draft creation. Blocks all writes.",
        can_execute_read=True,
        can_execute_write=False,
        can_send_external=False,
        can_deploy=False,
        approval_required_for=["write_actions", "email_send", "external_publish"],
        blocked_actions=["write_actions", "email_send", "external_publish"],
        use_when="Research, analysis, content drafting departments",
    ),
    3: AutonomyLevelConfig(
        level=AutonomyLevel.L3_STANDARD,
        label="L3 — Standard",
        description="Autonomous with configurable approval gates on configured triggers.",
        can_execute_read=True,
        can_execute_write=True,
        can_send_external=False,
        can_deploy=False,
        approval_required_for=[
            "spend_over_threshold",
            "external_api_write",
            "email_send_gt_100_recipients",
            "code_deploy_staging",
            "data_export",
        ],
        blocked_actions=["infrastructure_modify", "financial_gt_10k", "legal_binding"],
        use_when="Standard operating mode for most departments",
    ),
    4: AutonomyLevelConfig(
        level=AutonomyLevel.L4_AUTONOMOUS,
        label="L4 — Autonomous",
        description="Highly autonomous within policy. Minimal approval gates.",
        can_execute_read=True,
        can_execute_write=True,
        can_send_external=True,
        can_deploy=False,
        approval_required_for=[
            "constitutional_violations",
            "budget_cap_exceeded",
        ],
        blocked_actions=[
            "infrastructure_modify",
            "financial_gt_10k",
            "legal_binding",
            "pii_bulk_operations",
        ],
        use_when="Mature departments with strong reputation scores",
    ),
    5: AutonomyLevelConfig(
        level=AutonomyLevel.L5_MISSION,
        label="L5 — Mission Autonomous",
        description="End-to-end autonomous. User receives status updates and digest.",
        can_execute_read=True,
        can_execute_write=True,
        can_send_external=True,
        can_deploy=True,
        approval_required_for=[],   # hard limits always apply
        blocked_actions=[           # hard limits
            "production_infra_destruction",
            "mass_customer_data_deletion",
            "external_financial_transfer_gt_10k",
            "binding_legal_agreements",
            "press_releases",
            "mass_pii_bulk_operations",
        ],
        use_when="Highly trusted orgs on well-bounded missions",
    ),
}


class AutonomyEnforcer:
    """
    SUPPLEMENT A — Enforces autonomy level constraints on every action.

    Resolution order (most specific wins):
      Action override → Agent level → Team level → Dept level → Org level
    """

    def __init__(self) -> None:
        self._overrides: dict[str, int] = {}    # "org_id:dept_id:action" → level

    def resolve_level(
        self,
        org_level: int,
        dept_level_override: int | None = None,
        team_level_override: int | None = None,
        agent_level_override: int | None = None,
        action_level_override: int | None = None,
    ) -> int:
        """Resolve effective autonomy level. Most specific wins."""
        # Action override takes highest precedence
        if action_level_override is not None:
            return action_level_override
        if agent_level_override is not None:
            return agent_level_override
        if team_level_override is not None:
            return team_level_override
        if dept_level_override is not None:
            return dept_level_override
        return org_level

    def check_action(
        self,
        action: str,
        effective_level: int,
        raise_on_block: bool = True,
    ) -> tuple[bool, str]:
        """
        Check if an action is permitted at the given autonomy level.
        Returns (allowed, reason).
        """
        with _tracer.start_as_current_span("autonomy.check") as span:
            span.set_attribute("action", action)
            span.set_attribute("level", effective_level)

            # Hard limits apply at ALL levels
            action_lower = action.lower()
            for limit in HARD_LIMITS:
                if limit in action_lower or action_lower in limit:
                    reason = f"Hard limit: '{action}' is never permitted at any autonomy level."
                    _log.warning("autonomy.hard_limit", action=action, level=effective_level)
                    if raise_on_block:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail={"type": "autonomy-hard-limit", "title": "Action blocked", "detail": reason},
                        )
                    return False, reason

            config = AUTONOMY_CONFIGS.get(effective_level, AUTONOMY_CONFIGS[3])

            # Check blocked actions
            for blocked in config.blocked_actions:
                if blocked in action_lower or action_lower == blocked:
                    reason = f"Action '{action}' is blocked at {config.label}"
                    _log.info("autonomy.blocked", action=action, level=effective_level)
                    if raise_on_block:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail={"type": "autonomy-blocked", "title": "Action blocked", "detail": reason},
                        )
                    return False, reason

            # Check if approval required
            needs_approval = any(gate in action_lower for gate in config.approval_required_for)
            if needs_approval:
                return True, f"Action requires approval at {config.label}"

            span.set_attribute("allowed", True)
            return True, f"Action permitted at {config.label}"

    def requires_approval(self, action: str, effective_level: int) -> bool:
        """Return True if this action needs human approval at the given level."""
        config = AUTONOMY_CONFIGS.get(effective_level, AUTONOMY_CONFIGS[3])
        action_lower = action.lower()
        return any(gate in action_lower for gate in config.approval_required_for)

    def get_config(self, level: int) -> AutonomyLevelConfig:
        return AUTONOMY_CONFIGS.get(level, AUTONOMY_CONFIGS[3])

    def check_write_permitted(self, effective_level: int) -> bool:
        config = AUTONOMY_CONFIGS.get(effective_level, AUTONOMY_CONFIGS[3])
        return config.can_execute_write

    def check_external_send_permitted(self, effective_level: int) -> bool:
        config = AUTONOMY_CONFIGS.get(effective_level, AUTONOMY_CONFIGS[3])
        return config.can_send_external


# Global singleton
autonomy_enforcer = AutonomyEnforcer()
