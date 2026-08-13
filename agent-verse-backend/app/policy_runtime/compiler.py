from __future__ import annotations

from typing import TypedDict

from app.orchestration.runtime_profile import GoalRuntimeProfile, RiskLevel
from app.policy_runtime.constraint_model import RuntimeConstraints
from app.tenancy.context import PlanTier, TenantContext


class _PolicyFields(TypedDict):
    max_cost: float
    audit: str
    approvals: list[str]
    denied: list[str]
    data_classes: list[str]


def _compute_policy_fields(
    risk: RiskLevel,
    plan: PlanTier,
    compliance: list[str],
    hitl: bool,
) -> _PolicyFields:
    """Compile the shared deterministic policy dimensions."""
    cost_by_plan = {
        PlanTier.FREE: 2.0,
        PlanTier.STARTER: 10.0,
        PlanTier.PROFESSIONAL: 50.0,
        PlanTier.ENTERPRISE: 500.0,
    }
    max_cost = cost_by_plan.get(plan, 10.0)
    if risk == RiskLevel.CRITICAL:
        audit = "forensic"
    elif risk == RiskLevel.HIGH or compliance:
        audit = "full"
    else:
        audit = "standard"
    approvals = ["hitl"] if hitl or risk in (RiskLevel.HIGH, RiskLevel.CRITICAL) else []
    denied = ["tool:shell"] if plan == PlanTier.FREE else []
    data_classes = ["public", "internal", "confidential"] if compliance else ["public", "internal"]
    return {
        "max_cost": max_cost,
        "audit": audit,
        "approvals": approvals,
        "denied": denied,
        "data_classes": data_classes,
    }


class PolicyCompiler:
    def compile(
        self, profile: GoalRuntimeProfile, *, tenant_ctx: TenantContext
    ) -> RuntimeConstraints:
        fields = _compute_policy_fields(
            risk=profile.properties.risk,
            plan=tenant_ctx.plan,
            compliance=list(profile.security.compliance_tags),
            hitl=profile.security.hitl_required,
        )
        return RuntimeConstraints(
            allowed_capabilities=[
                "model:completion",
                "rag:read",
                *(["tool:web_search"] if profile.properties.requires_web else []),
                *(["tool:code"] if profile.properties.requires_code else []),
            ],
            denied_capabilities=fields["denied"],
            required_approvals=fields["approvals"],
            max_cost_usd=fields["max_cost"],
            audit_level=fields["audit"],
            data_classes_allowed=fields["data_classes"],
            compliance_constraints=list(profile.security.compliance_tags),
        )
