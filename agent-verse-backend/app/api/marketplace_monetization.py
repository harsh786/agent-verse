"""Marketplace monetization — paid templates, Stripe Connect, author payouts.

NOT IMPLEMENTED (reported honestly rather than faked): buying a paid template.
``POST /purchase/{id}`` used to create a real Stripe PaymentIntent and a
*pending* purchase row, but nothing ever marked the purchase paid (no
``payment_intent.succeeded`` handling), installs were not gated on payment and
the author was never paid (no Connect ``transfer_data`` / application fee) — a
buyer could be charged for nothing. Until purchase completion, install gating
and payouts exist, a paid purchase is refused with 501 *before* any Stripe call.
Free templates still answer ``{"status": "free"}``.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/marketplace/monetization", tags=["marketplace"])
_log = structlog.get_logger(__name__)


class PricingRequest(BaseModel):
    template_id: str
    price_usd: float = Field(default=0.0, ge=0.0, le=100_000.0)
    revenue_share_pct: int = Field(default=70, ge=0, le=100)


class OnboardAuthorRequest(BaseModel):
    payout_email: str
    return_url: str = "https://app.agentverse.ai/marketplace/author"


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


def _db(request: Request) -> Any:
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(503, "Database unavailable")
    return db


def _stripe() -> Any:
    try:
        import stripe

        from app.core.config import get_settings

        s = get_settings()
        if not s.stripe_api_key:
            raise HTTPException(503, "Stripe not configured. Set STRIPE_API_KEY.")
        stripe.api_key = s.stripe_api_key
        return stripe
    except ImportError as _b904_exc:
        raise HTTPException(503, "stripe package not installed. Run: pip install stripe") from _b904_exc  # noqa: E501


@router.post("/set-price")
async def set_template_price(body: PricingRequest, request: Request) -> dict[str, Any]:
    """Set pricing on one of the caller's OWN marketplace templates.

    The old statement matched a non-existent ``template_id`` column (every call
    was a SQL error) and had no ownership predicate — it would have let any
    tenant price, and claim authorship of, another tenant's template.
    """
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    tenant = _require_tenant(request)
    db = _db(request)
    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant.tenant_id):
        result = await session.execute(
            text(
                "UPDATE marketplace_templates "
                "SET price_usd = :price, author_tenant_id = :tid, revenue_share_pct = :share "
                "WHERE id = :tmpl_id AND tenant_id = :tid"
            ),
            {
                "price": body.price_usd,
                "tid": tenant.tenant_id,
                "share": body.revenue_share_pct,
                "tmpl_id": body.template_id,
            },
        )
        updated = int(getattr(result, "rowcount", 0) or 0)
    if updated == 0:
        raise HTTPException(404, "Template not found or not owned by this tenant")
    return {"template_id": body.template_id, "price_usd": body.price_usd}


@router.post("/onboard-author")
async def onboard_author(body: OnboardAuthorRequest, request: Request) -> dict[str, Any]:
    """Start Stripe Connect onboarding and persist the author's account id.

    The account id used to be returned but never stored, so payouts could never
    be routed to the author.
    """
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    tenant = _require_tenant(request)
    stripe = _stripe()
    db = _db(request)  # checked before creating anything at Stripe
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
    except Exception as exc:
        _log.error("stripe_onboarding_failed", error=str(exc)[:200])
        raise HTTPException(502, "Stripe onboarding failed") from exc
    try:
        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant.tenant_id),
        ):
            params = {
                "tid": tenant.tenant_id,
                "acct": account.id,
                "email": body.payout_email,
            }
            updated = await session.execute(
                text(
                    "UPDATE marketplace_author_accounts "
                    "SET stripe_account_id = :acct, payout_email = :email "
                    "WHERE tenant_id = :tid"
                ),
                params,
            )
            if not int(getattr(updated, "rowcount", 0) or 0):
                await session.execute(
                    text(
                        "INSERT INTO marketplace_author_accounts "
                        "(id, tenant_id, stripe_account_id, payout_email) "
                        "VALUES (:id, :tid, :acct, :email)"
                    ),
                    {**params, "id": uuid.uuid4().hex},
                )
    except Exception as exc:
        _log.error(
            "author_account_persist_failed",
            tenant_id=tenant.tenant_id,
            stripe_account_id=account.id,
            error=str(exc)[:200],
        )
        raise HTTPException(
            503, "Stripe account created but could not be recorded; retry onboarding"
        ) from exc
    return {"onboarding_url": link.url, "stripe_account_id": account.id}


_PAID_PURCHASE_UNAVAILABLE = (
    "Paid template purchases are not available yet: payment completion, install "
    "gating and author payouts are not implemented. No charge was created."
)


@router.post("/purchase/{template_id}")
async def purchase_template(template_id: str, request: Request) -> dict[str, Any]:
    """Purchase a template: free ones answer ``free``; paid ones are a 501.

    The template is read under the buyer's RLS context (the read policy exposes
    public/community templates and the buyer's own). A paid template is refused
    before Stripe is touched, so no PaymentIntent (and no charge) is created.
    """
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    tenant = _require_tenant(request)
    db = _db(request)
    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant.tenant_id):
        row = (
            await session.execute(
                text("SELECT price_usd FROM marketplace_templates WHERE id = :tid"),
                {"tid": template_id},
            )
        ).fetchone()
    if row is None:
        raise HTTPException(404, f"Template {template_id} not found")
    price_usd = float(row[0] or 0)
    if price_usd == 0:
        return {"status": "free", "template_id": template_id}
    _log.info(
        "marketplace_paid_purchase_refused",
        template_id=template_id,
        buyer_tenant_id=tenant.tenant_id,
    )
    raise HTTPException(501, _PAID_PURCHASE_UNAVAILABLE)
