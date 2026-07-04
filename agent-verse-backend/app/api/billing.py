"""Billing & subscription management API."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/usage")
async def get_usage(request: Request) -> dict:
    """Get usage summary for the current tenant."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    usage_svc = getattr(request.app.state, "usage_service", None)
    if usage_svc is not None:
        return await usage_svc.get_usage_summary(tenant_ctx.tenant_id)

    return {
        "tenant_id": tenant_ctx.tenant_id,
        "period_days": 30,
        "usage": {},
        "costs": {},
        "total_cost_usd": 0.0,
    }


@router.get("/subscription")
async def get_subscription(request: Request) -> dict:
    """Get current subscription details."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    return {
        "tenant_id": tenant_ctx.tenant_id,
        "plan": tenant_ctx.plan.value,
        "status": "active",
        "stripe_configured": bool(os.getenv("STRIPE_SECRET_KEY")),
        "upgrade_url": "/billing/upgrade",
    }


class UpgradeRequest(BaseModel):
    plan: str  # starter | professional | enterprise


@router.post("/upgrade")
async def request_upgrade(body: UpgradeRequest, request: Request) -> dict:
    """Request a plan upgrade. Returns Stripe checkout URL if configured."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    valid_plans = {"starter", "professional", "enterprise"}
    if body.plan not in valid_plans:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {body.plan}")

    stripe_key = os.getenv("STRIPE_SECRET_KEY")
    if stripe_key:
        # TODO: Phase 1e full Stripe integration
        return {
            "status": "redirect",
            "checkout_url": f"https://checkout.stripe.com/placeholder?plan={body.plan}",
            "message": f"Redirecting to Stripe for {body.plan} plan",
        }

    return {
        "status": "pending",
        "plan": body.plan,
        "message": "Stripe not configured — contact support to upgrade",
    }


@router.get("/plans")
async def list_plans() -> dict:
    """List available plans with features and pricing."""
    return {
        "plans": [
            {
                "id": "free",
                "name": "Free",
                "price_usd_monthly": 0,
                "goals_per_day": 25,
                "max_agents": 3,
                "features": ["goals", "agents", "knowledge", "memory"],
            },
            {
                "id": "starter",
                "name": "Starter",
                "price_usd_monthly": 29,
                "goals_per_day": 200,
                "max_agents": 10,
                "features": [
                    "goals", "agents", "knowledge", "memory",
                    "marketplace", "byo_api_key",
                ],
            },
            {
                "id": "professional",
                "name": "Professional",
                "price_usd_monthly": 99,
                "goals_per_day": 1000,
                "max_agents": 50,
                "features": ["all_starter", "rpa", "a2a", "simulations", "advanced_guardrails"],
            },
            {
                "id": "enterprise",
                "name": "Enterprise",
                "price_usd_monthly": None,
                "goals_per_day": 10000,
                "max_agents": 500,
                "features": ["all_pro", "sso", "scim", "custom_roles", "sla", "compliance"],
            },
        ]
    }
