"""GovernanceProfileSelector — selects governance bundle per plan tier and risk."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile
    from app.tenancy.context import TenantContext


class GovernanceBundle(str, enum.Enum):
    FREE = "free"
    ENTERPRISE = "enterprise"
    REGULATED = "regulated"


@dataclass
class GovernanceConfig:
    name: GovernanceBundle
    hitl_enabled: bool = False
    cost_control_enabled: bool = True
    policy_engine_enabled: bool = False
    audit_enabled: bool = True
    compliance_reporting_enabled: bool = False
    rbac_enforcement: str = "basic"
    max_goal_cost_usd: float = 10.0
    allowed_tools: list[str] = field(default_factory=list)


class GovernanceProfileSelector:
    def select(self, profile: GoalRuntimeProfile, *, tenant_ctx: TenantContext) -> GovernanceConfig:
        from app.orchestration.runtime_profile import RiskLevel
        from app.tenancy.context import PlanTier

        plan = tenant_ctx.plan
        risk = profile.properties.risk
        hitl = profile.security.hitl_required
        compliance = profile.security.compliance_tags

        # Enterprise plan always gets enterprise governance
        if plan == PlanTier.ENTERPRISE:
            return GovernanceConfig(
                name=GovernanceBundle.ENTERPRISE,
                hitl_enabled=hitl,
                cost_control_enabled=True,
                policy_engine_enabled=True,
                audit_enabled=True,
                compliance_reporting_enabled=True,
                rbac_enforcement="full",
                max_goal_cost_usd=100.0,
            )

        # Compliance-tagged goals get regulated governance (regardless of plan tier)
        if compliance:
            return GovernanceConfig(
                name=GovernanceBundle.REGULATED,
                hitl_enabled=hitl,
                cost_control_enabled=True,
                policy_engine_enabled=True,
                audit_enabled=True,
                compliance_reporting_enabled=True,
                rbac_enforcement="full",
                max_goal_cost_usd=50.0,
            )

        # High/critical risk non-enterprise goals get elevated governance
        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return GovernanceConfig(
                name=GovernanceBundle.ENTERPRISE,
                hitl_enabled=hitl,
                cost_control_enabled=True,
                policy_engine_enabled=True,
                audit_enabled=True,
                compliance_reporting_enabled=False,
                rbac_enforcement="full",
                max_goal_cost_usd=50.0,
            )

        return GovernanceConfig(
            name=GovernanceBundle.FREE,
            hitl_enabled=hitl,
            cost_control_enabled=True,
            policy_engine_enabled=False,
            audit_enabled=True,
            rbac_enforcement="basic",
            max_goal_cost_usd=10.0,
        )
