from __future__ import annotations
from typing import TYPE_CHECKING
from app.policy_runtime.constraint_model import RuntimeConstraints

if TYPE_CHECKING:
    from app.orchestration.runtime_profile import GoalRuntimeProfile
    from app.tenancy.context import TenantContext


def _compute_policy_fields(
    risk: "RiskLevel",
    plan: "PlanTier",
    compliance: list[str],
    hitl: bool,
) -> dict:
    """Shared business rules for policy compilation. Used by both PolicyCompiler and PolicyBundleSelector."""
    from app.orchestration.runtime_profile import RiskLevel as RL
    from app.tenancy.context import PlanTier as PT
    cost_by_plan = {PT.FREE: 2.0, PT.STARTER: 10.0, PT.PROFESSIONAL: 50.0, PT.ENTERPRISE: 500.0}
    max_cost = cost_by_plan.get(plan, 10.0)
    if risk == RL.CRITICAL:
        audit = "forensic"
    elif risk == RL.HIGH:
        audit = "full"
    elif compliance:
        audit = "full"
    else:
        audit = "standard"
    approvals = ["hitl"] if risk in (RL.HIGH, RL.CRITICAL) else []
    denied = ["tool:shell"] if plan == PT.FREE else []
    data_classes = ["public", "internal", "confidential"] if compliance else ["public", "internal"]
    return {"max_cost": max_cost, "audit": audit, "approvals": approvals,
            "denied": denied, "data_classes": data_classes}


class PolicyCompiler:
    def compile(
        self, profile: "GoalRuntimeProfile", *, tenant_ctx: "TenantContext"
    ) -> RuntimeConstraints:
        from app.orchestration.runtime_profile import RiskLevel
        fields = _compute_policy_fields(
            risk=profile.properties.risk, plan=tenant_ctx.plan,
            compliance=list(profile.security.compliance_tags),
            hitl=profile.security.hitl_required,
        )
        return RuntimeConstraints(
            allowed_capabilities=[], denied_capabilities=fields["denied"],
            required_approvals=fields["approvals"], max_cost_usd=fields["max_cost"],
            audit_level=fields["audit"], data_classes_allowed=fields["data_classes"],
            compliance_constraints=list(profile.security.compliance_tags),
        )
