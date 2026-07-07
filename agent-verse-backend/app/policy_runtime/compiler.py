from __future__ import annotations
from typing import TYPE_CHECKING
from app.policy_runtime.constraint_model import RuntimeConstraints

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile
    from app.tenancy.context import TenantContext


class PolicyCompiler:
    def compile(
        self, profile: "GoalRuntimeProfile", *, tenant_ctx: "TenantContext"
    ) -> RuntimeConstraints:
        from app.orchestration.runtime_profile import RiskLevel
        from app.tenancy.context import PlanTier
        risk = profile.properties.risk
        plan = tenant_ctx.plan
        compliance = list(profile.security.compliance_tags)
        cost_by_plan = {
            PlanTier.FREE: 2.0,
            PlanTier.STARTER: 10.0,
            PlanTier.PROFESSIONAL: 50.0,
            PlanTier.ENTERPRISE: 500.0,
        }
        max_cost = cost_by_plan.get(plan, 10.0)
        audit = "standard"
        if risk == RiskLevel.CRITICAL:
            audit = "forensic"
        elif risk == RiskLevel.HIGH:
            audit = "full"
        elif compliance:
            audit = "full"
        approvals = ["hitl"] if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL) else []
        denied = ["tool:shell"] if plan == PlanTier.FREE else []
        data_classes = ["public", "internal", "confidential"] if compliance else ["public", "internal"]
        return RuntimeConstraints(
            allowed_capabilities=[],
            denied_capabilities=denied,
            required_approvals=approvals,
            max_cost_usd=max_cost,
            audit_level=audit,
            data_classes_allowed=data_classes,
            compliance_constraints=compliance,
        )
