"""PolicyBundleSelector — produces a CompiledRuntimePolicy before graph execution."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile
    from app.tenancy.context import TenantContext


@dataclass
class CompiledRuntimePolicy:
    allowed_capabilities: list[str] = field(default_factory=list)
    denied_capabilities: list[str] = field(default_factory=list)
    required_approvals: list[str] = field(default_factory=list)
    max_cost_usd: float = 10.0
    max_latency_ms: int = 120_000
    data_classes_allowed: list[str] = field(default_factory=lambda: ["public", "internal"])
    audit_level: str = "standard"
    compliance_constraints: list[str] = field(default_factory=list)


class PolicyBundleSelector:
    def select(self, profile: "GoalRuntimeProfile", *, tenant_ctx: "TenantContext") -> CompiledRuntimePolicy:
        from app.orchestration.runtime_profile import RiskLevel
        from app.tenancy.context import PlanTier
        risk = profile.properties.risk
        compliance = list(profile.security.compliance_tags)
        hitl = profile.security.hitl_required
        cost_by_plan = {
            PlanTier.FREE: 2.0, PlanTier.STARTER: 10.0,
            PlanTier.PROFESSIONAL: 50.0, PlanTier.ENTERPRISE: 500.0,
        }
        max_cost = cost_by_plan.get(tenant_ctx.plan, 10.0)
        audit = "standard"
        if risk == RiskLevel.CRITICAL:
            audit = "forensic"
        elif risk == RiskLevel.HIGH:
            audit = "full"
        elif compliance:
            audit = "full"
        approvals = ["hitl"] if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL) else []
        denied: list[str] = []
        from app.tenancy.context import PlanTier as PT
        if tenant_ctx.plan == PT.FREE:
            denied = ["tool:shell"]
        data_classes = ["public", "internal", "confidential"] if compliance else ["public", "internal"]
        return CompiledRuntimePolicy(
            allowed_capabilities=[], denied_capabilities=denied, required_approvals=approvals,
            max_cost_usd=max_cost, audit_level=audit, data_classes_allowed=data_classes,
            compliance_constraints=compliance,
        )
