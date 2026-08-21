"""SLA tiers — per-plan uptime guarantees and support response times."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.tenancy.context import PlanTier

router = APIRouter(prefix="/sla", tags=["sla"])

_SLA_BY_PLAN: dict[str, dict[str, Any]] = {
    "free": {
        "uptime_sla_pct": None,
        "support_response_hours": None,
        "support_channels": ["community_forum"],
        "incident_notification": False,
        "description": "Best-effort, no SLA",
    },
    "starter": {
        "uptime_sla_pct": 99.0,
        "support_response_hours": 72,
        "support_channels": ["email"],
        "incident_notification": False,
        "description": "99% uptime, email support within 72h",
    },
    "professional": {
        "uptime_sla_pct": 99.5,
        "support_response_hours": 24,
        "support_channels": ["email", "slack"],
        "incident_notification": True,
        "description": "99.5% uptime, 24h response, Slack channel",
    },
    "enterprise": {
        "uptime_sla_pct": 99.9,
        "support_response_hours": 4,
        "support_channels": ["email", "slack", "phone", "dedicated_csm"],
        "incident_notification": True,
        "description": "99.9% uptime, 4h response, dedicated CSM",
        "credit_schedule": {
            "99.0_to_99.9": "10% monthly credit",
            "95.0_to_99.0": "25% monthly credit",
            "below_95.0": "50% monthly credit",
        },
    },
}


@router.get("/my-plan")
async def get_my_sla(request: Request) -> dict[str, Any]:
    """Return the SLA terms for the authenticated tenant's plan."""
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        return _SLA_BY_PLAN["free"]
    plan = getattr(tenant, "plan", PlanTier.FREE)
    plan_name = plan.value if hasattr(plan, "value") else str(plan).lower()
    return {"plan": plan_name, **_SLA_BY_PLAN.get(plan_name, _SLA_BY_PLAN["free"])}


@router.get("/plans")
async def list_sla_plans() -> list[dict[str, Any]]:
    """List all plan SLA tiers (public — for pricing page)."""
    return [{"plan": k, **v} for k, v in _SLA_BY_PLAN.items()]
