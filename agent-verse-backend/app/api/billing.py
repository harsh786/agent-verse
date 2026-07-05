"""Billing & subscription management API."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/billing", tags=["billing"])


def _require_tenant(request: Request) -> Any:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return tenant


@router.get("/usage")
async def get_usage(request: Request) -> dict:
    """Get usage summary for the current tenant."""
    tenant_ctx = _require_tenant(request)

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
    tenant_ctx = _require_tenant(request)

    from app.core.config import get_settings
    settings = get_settings()

    return {
        "tenant_id": tenant_ctx.tenant_id,
        "plan": tenant_ctx.plan.value,
        "status": "active",
        "stripe_configured": bool(settings.stripe_api_key),
        "checkout_url": "/billing/checkout",
    }


class UpgradeRequest(BaseModel):
    plan: str  # starter | professional | enterprise


class CheckoutRequest(BaseModel):
    plan: str  # starter | professional | enterprise


@router.post("/upgrade")
async def request_upgrade(body: UpgradeRequest, request: Request) -> dict:
    """Request a plan upgrade — delegates to /billing/checkout for Stripe flow."""
    tenant_ctx = _require_tenant(request)

    valid_plans = {"starter", "professional", "enterprise"}
    if body.plan not in valid_plans:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {body.plan}")

    from app.core.config import get_settings
    settings = get_settings()
    if not settings.stripe_api_key:
        return {
            "status": "pending",
            "plan": body.plan,
            "message": "Stripe not configured — contact support to upgrade",
        }

    # Delegate to the real checkout endpoint logic
    checkout_body = CheckoutRequest(plan=body.plan)
    return await create_checkout_session(request=request, body=checkout_body)


@router.post("/checkout")
async def create_checkout_session(
    request: Request,
    body: CheckoutRequest,
) -> dict[str, Any]:
    """Create a real Stripe Checkout Session for the given plan."""
    tenant = _require_tenant(request)
    from app.core.config import get_settings
    settings = get_settings()
    if not settings.stripe_api_key:
        raise HTTPException(503, "Billing not configured. Set STRIPE_API_KEY.")
    try:
        import stripe
        stripe.api_key = settings.stripe_api_key
        price_map = {
            "starter": os.getenv("STRIPE_PRICE_STARTER", ""),
            "professional": os.getenv("STRIPE_PRICE_PROFESSIONAL", ""),
            "enterprise": os.getenv("STRIPE_PRICE_ENTERPRISE", ""),
        }
        price_id = price_map.get(body.plan.lower(), "")
        if not price_id:
            raise HTTPException(400, f"Unknown plan or price not configured: {body.plan}")
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=os.getenv(
                "STRIPE_SUCCESS_URL",
                "https://app.agentverse.ai/settings/billing?success=1",
            ),
            cancel_url=os.getenv(
                "STRIPE_CANCEL_URL",
                "https://app.agentverse.ai/settings/billing?cancelled=1",
            ),
            client_reference_id=tenant.tenant_id,
            metadata={"tenant_id": tenant.tenant_id, "plan": body.plan},
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except ImportError:
        raise HTTPException(503, "stripe package not installed. Run: pip install stripe")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Billing error: {exc}")


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
