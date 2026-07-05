"""Marketplace monetization — paid templates, Stripe Connect, author payouts."""
from __future__ import annotations
import uuid
from typing import Any
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/marketplace/monetization", tags=["marketplace"])


class PricingRequest(BaseModel):
    template_id: str
    price_usd: float = 0.0
    revenue_share_pct: int = 70


class OnboardAuthorRequest(BaseModel):
    payout_email: str
    return_url: str = "https://app.agentverse.ai/marketplace/author"


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


def _stripe():
    try:
        import stripe
        from app.core.config import get_settings
        s = get_settings()
        if not s.stripe_api_key:
            raise HTTPException(503, "Stripe not configured. Set STRIPE_API_KEY.")
        stripe.api_key = s.stripe_api_key
        return stripe
    except ImportError:
        raise HTTPException(503, "stripe package not installed. Run: pip install stripe")


@router.post("/set-price")
async def set_template_price(body: PricingRequest, request: Request) -> dict[str, Any]:
    """Set pricing on a marketplace template."""
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(503, "Database unavailable")
    from sqlalchemy import text
    from app.db.rls import sqlalchemy_rls_context
    async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
        await session.execute(
            text("""UPDATE marketplace_templates
                    SET price_usd = :price, author_tenant_id = :tid, revenue_share_pct = :share
                    WHERE template_id = :tmpl_id"""),
            {"price": body.price_usd, "tid": tenant.tenant_id,
             "share": body.revenue_share_pct, "tmpl_id": body.template_id},
        )
        await session.commit()
    return {"template_id": body.template_id, "price_usd": body.price_usd}


@router.post("/onboard-author")
async def onboard_author(body: OnboardAuthorRequest, request: Request) -> dict[str, Any]:
    """Start Stripe Connect onboarding for marketplace authors."""
    tenant = _require_tenant(request)
    stripe = _stripe()
    try:
        account = stripe.Account.create(
            type="express",
            email=body.payout_email,
            capabilities={"transfers": {"requested": True}},
            metadata={"tenant_id": tenant.tenant_id},
        )
        link = stripe.AccountLink.create(
            account=account.id,
            refresh_url=body.return_url + "?refresh=1",
            return_url=body.return_url + "?success=1",
            type="account_onboarding",
        )
        return {"onboarding_url": link.url, "stripe_account_id": account.id}
    except Exception as exc:
        raise HTTPException(500, f"Stripe onboarding failed: {exc}")


@router.post("/purchase/{template_id}")
async def purchase_template(template_id: str, request: Request) -> dict[str, Any]:
    """Purchase a paid marketplace template."""
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(503, "Database unavailable")
    from sqlalchemy import text
    async with db() as session:
        row = (await session.execute(
            text("SELECT price_usd FROM marketplace_templates WHERE template_id = :tid"),
            {"tid": template_id},
        )).fetchone()
    if row is None:
        raise HTTPException(404, f"Template {template_id} not found")
    price_usd = float(row[0] or 0)
    if price_usd == 0:
        return {"status": "free", "template_id": template_id}
    stripe = _stripe()
    try:
        intent = stripe.PaymentIntent.create(
            amount=int(price_usd * 100),
            currency="usd",
            metadata={"template_id": template_id, "buyer_tenant_id": tenant.tenant_id},
        )
        return {"client_secret": intent.client_secret, "amount_usd": price_usd}
    except Exception as exc:
        raise HTTPException(500, f"Purchase failed: {exc}")
