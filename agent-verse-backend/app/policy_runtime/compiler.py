from __future__ import annotations

from typing import TypedDict

from app.orchestration.runtime_profile import RiskLevel
from app.tenancy.context import PlanTier


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
