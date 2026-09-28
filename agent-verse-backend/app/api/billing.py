"""Billing & subscription management API — Razorpay + legacy Stripe support."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.tenancy.context import TenantContext

router = APIRouter(prefix="/billing", tags=["billing"])
_log = logging.getLogger(__name__)

# ── Plan definitions ───────────────────────────────────────────────────────────

PLAN_PRICES = {
    "starter": {"monthly": 2900, "annual": 27840},  # INR paise (₹29 / ₹278.40)
    "professional": {"monthly": 9900, "annual": 95040},  # INR paise (₹99 / ₹950.40)
    "enterprise": {"monthly": 49900, "annual": 479040},  # INR paise (₹499 / ₹4790.40)
}

PLAN_FEATURES: dict[str, dict[str, Any]] = {
    "starter": {
        "goals_per_day": 50,
        "tokens_per_month": 5_000_000,
        "tool_calls_per_day": 500,
        "agents": 5,
        "connectors": 10,
    },
    "professional": {
        "goals_per_day": 500,
        "tokens_per_month": 50_000_000,
        "tool_calls_per_day": 5000,
        "agents": 50,
        "connectors": 100,
    },
    "enterprise": {
        "goals_per_day": -1,
        "tokens_per_month": -1,
        "tool_calls_per_day": -1,
        "agents": -1,
        "connectors": -1,
    },
}

# ── Helpers ────────────────────────────────────────────────────────────────────


def _require_tenant(request: Request) -> TenantContext:
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return ctx


def _get_razorpay() -> Any | None:
    """Return a configured Razorpay client, or None if not configured."""
    try:
        import razorpay

        from app.core.config import get_settings

        settings = get_settings()
        if not settings.razorpay_key_secret:
            return None
        return razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    except Exception:
        return None


def _inr_to_usd(amount_inr: float) -> float:
    """Convert INR to approximate USD using the configurable exchange rate."""
    from app.core.config import get_settings

    rate = get_settings().inr_to_usd_rate
    return round(amount_inr / rate, 2)


# ── Request / response models ─────────────────────────────────────────────────


class UpgradeRequest(BaseModel):
    plan: str  # starter | professional | enterprise


class CheckoutRequest(BaseModel):
    plan: str  # starter | professional | enterprise


class CreateOrderRequest(BaseModel):
    plan: str = Field(..., pattern="^(starter|professional|enterprise)$")
    cycle: str = Field(default="monthly", pattern="^(monthly|annual)$")
    currency: str = Field(default="INR")


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    # Accepted for backward compatibility with existing clients but IGNORED: the
    # plan/cycle are read from the server-side order record (see _load_order).
    # Trusting these let any tenant pay for Starter and claim Enterprise.
    plan: str | None = None
    cycle: str | None = None


# ── Existing endpoints (usage / subscription / invoices / Stripe checkout) ────


@router.get("/usage")
async def get_usage(request: Request) -> dict[str, Any]:
    """Get usage summary for the current tenant."""
    tenant_ctx = _require_tenant(request)

    usage_svc = getattr(request.app.state, "usage_service", None)
    if usage_svc is not None:
        return await usage_svc.get_usage_summary(tenant_ctx.tenant_id)  # type: ignore[no-any-return]

    return {
        "tenant_id": tenant_ctx.tenant_id,
        "period_days": 30,
        "usage": {},
        "costs": {},
        "total_cost_usd": 0.0,
    }


@router.get("/subscription")
async def get_subscription(request: Request) -> dict[str, Any]:
    """Get current subscription details."""
    tenant_ctx = _require_tenant(request)

    from app.core.config import get_settings

    settings = get_settings()

    return {
        "tenant_id": tenant_ctx.tenant_id,
        "plan": tenant_ctx.plan.value,
        "status": "active",
        "stripe_configured": bool(settings.stripe_api_key),
        "razorpay_configured": bool(settings.razorpay_key_secret),
        "checkout_url": "/billing/checkout",
    }


@router.post("/upgrade")
async def request_upgrade(body: UpgradeRequest, request: Request) -> dict[str, Any]:
    """Request a plan upgrade — delegates to /billing/checkout for Stripe flow."""
    _require_tenant(request)

    valid_plans = {"starter", "professional", "enterprise"}
    if body.plan not in valid_plans:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {body.plan}")

    from app.core.config import get_settings

    settings = get_settings()
    if not settings.stripe_api_key:
        return {
            "status": "pending",
            "plan": body.plan,
            "message": "Stripe not configured — use Razorpay checkout instead",
        }

    checkout_body = CheckoutRequest(plan=body.plan)
    return await create_checkout_session(request=request, body=checkout_body)


@router.post("/checkout")
async def create_checkout_session(
    request: Request,
    body: CheckoutRequest,
) -> dict[str, Any]:
    """Create a Stripe Checkout Session for the given plan (legacy)."""
    tenant = _require_tenant(request)
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.stripe_api_key:
        raise HTTPException(
            503,
            "Stripe billing not configured. Use /billing/create-order for Razorpay.",
        )
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
            # Copied onto the Subscription so its lifecycle events
            # (customer.subscription.deleted) identify the tenant.
            subscription_data={
                "metadata": {"tenant_id": tenant.tenant_id, "plan": body.plan}
            },
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except ImportError as _b904_exc:
        raise HTTPException(503, "stripe package not installed. Run: pip install stripe") from _b904_exc  # noqa: E501
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Billing error: {exc}") from exc


@router.get("/invoices")
async def list_invoices(request: Request) -> list[dict[str, Any]]:
    """Return billing invoice history from Razorpay or empty list."""
    tenant = _require_tenant(request)

    rz = _get_razorpay()
    if rz is None:
        from app.core.config import get_settings

        settings = get_settings()
        if settings.environment != "development":
            return []  # No fake data in staging/production
        # Return demo invoices for development mode only
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        return [
            {
                "id": f"pay_demo_{i}",
                "date": (now - timedelta(days=30 * i)).isoformat(),
                "amount_usd": [29.0, 99.0, 29.0][i % 3],
                "status": "paid",
                "plan": ["starter", "professional", "starter"][i % 3],
                "cycle": "monthly",
                "pdf_url": None,
                "is_demo": True,
            }
            for i in range(3)
        ]

    try:
        # Fetch payments for this tenant from Razorpay
        payments = rz.payment.all(
            {
                "count": 20,
                "notes[tenant_id]": tenant.tenant_id,
            }
        )

        invoices = []
        for payment in payments.get("items", []) if isinstance(payments, dict) else []:
            notes = payment.get("notes", {})
            if notes.get("tenant_id") != tenant.tenant_id:
                continue  # Extra safety check

            amount_inr = float(payment.get("amount", 0)) / 100  # paise to rupees
            amount_usd = _inr_to_usd(amount_inr)

            import datetime as _dt

            invoices.append(
                {
                    "id": payment.get("id", ""),
                    "date": _dt.datetime.fromtimestamp(
                        payment.get("created_at", 0),
                        tz=_dt.UTC,
                    ).isoformat(),
                    "amount_usd": round(amount_usd, 2),
                    "amount_inr": round(amount_inr, 2),
                    "status": (
                        "paid"
                        if payment.get("status") == "captured"
                        else payment.get("status", "unknown")
                    ),
                    "plan": notes.get("plan", "unknown"),
                    "cycle": notes.get("cycle", "monthly"),
                    "pdf_url": None,  # Razorpay doesn't provide PDF invoices via API directly
                    "payment_id": payment.get("id"),
                }
            )

        return invoices
    except Exception as exc:
        _log.error("Failed to fetch Razorpay invoices: %s", exc)
        return []


# ── Razorpay endpoints ─────────────────────────────────────────────────────────


@router.get("/plans")
async def list_plans(request: Request) -> list[dict[str, Any]]:
    """Return available billing plans with Razorpay pricing info."""
    _require_tenant(request)
    from app.core.config import get_settings

    settings = get_settings()

    return [
        {
            "plan_id": plan,
            "name": plan.title(),
            "prices": {
                "monthly_inr": price["monthly"] / 100,
                "annual_inr": price["annual"] / 100,
                "monthly_paise": price["monthly"],
                "annual_paise": price["annual"],
            },
            "limits": PLAN_FEATURES[plan],
            "razorpay_key_id": settings.razorpay_key_id,
        }
        for plan, price in PLAN_PRICES.items()
    ]


# ── Server-side order records ─────────────────────────────────────────────────
#
# The plan a payment buys is fixed HERE, when the order is created, and stored
# server-side (billing_orders, FORCE RLS). verify-payment and the webhook read it
# back — they never take the plan from the client body or from free-form notes.


@dataclass
class _Order:
    order_id: str
    tenant_id: str
    plan: str
    cycle: str
    amount: int
    currency: str
    is_mock: bool
    status: str = "created"  # created | paid
    payment_id: str | None = None


def _db_factory(request: Request) -> Any | None:
    return getattr(request.app.state, "db_session_factory", None)


def _mem_orders(request: Request) -> dict[str, _Order]:
    """Process-local order store — ONLY for builds with no database (tests/dev)."""
    store: dict[str, _Order] | None = getattr(request.app.state, "billing_orders_mem", None)
    if store is None:
        store = {}
        request.app.state.billing_orders_mem = store
    return store


async def _save_order(request: Request, order: _Order) -> None:
    db = _db_factory(request)
    if db is None:
        _mem_orders(request)[order.order_id] = order
        return
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    try:
        async with db() as session, session.begin(), sqlalchemy_rls_context(
            session, order.tenant_id
        ):
            await session.execute(
                text(
                    "INSERT INTO billing_orders "
                    "(order_id, tenant_id, plan, cycle, amount, currency, is_mock, status) "
                    "VALUES (:oid, :tid, :plan, :cycle, :amount, :currency, :mock, 'created')"
                ),
                {
                    "oid": order.order_id,
                    "tid": order.tenant_id,
                    "plan": order.plan,
                    "cycle": order.cycle,
                    "amount": order.amount,
                    "currency": order.currency,
                    "mock": order.is_mock,
                },
            )
    except Exception as exc:
        _log.error("billing_order_persist_failed order=%s: %s", order.order_id, exc)
        raise HTTPException(503, "Could not record the payment order; try again") from exc


async def _load_order(request: Request, tenant_id: str, order_id: str) -> _Order | None:
    db = _db_factory(request)
    if db is None:
        order = _mem_orders(request).get(order_id)
        return order if order is not None and order.tenant_id == tenant_id else None
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    try:
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
            row = (
                await session.execute(
                    text(
                        "SELECT order_id, tenant_id, plan, cycle, amount, currency, is_mock, "
                        "status, payment_id FROM billing_orders "
                        "WHERE order_id = :oid AND tenant_id = :tid"
                    ),
                    {"oid": order_id, "tid": tenant_id},
                )
            ).fetchone()
    except Exception as exc:
        _log.error("billing_order_load_failed order=%s: %s", order_id, exc)
        raise HTTPException(503, "Billing store unavailable; try again") from exc
    if row is None:
        return None
    return _Order(
        order_id=row[0],
        tenant_id=row[1],
        plan=row[2],
        cycle=row[3],
        amount=int(row[4]),
        currency=row[5],
        is_mock=bool(row[6]),
        status=row[7],
        payment_id=row[8],
    )


async def _mark_order_paid(request: Request, order: _Order, payment_id: str) -> None:
    db = _db_factory(request)
    if db is None:
        order.status = "paid"
        order.payment_id = payment_id
        return
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    try:
        async with db() as session, session.begin(), sqlalchemy_rls_context(
            session, order.tenant_id
        ):
            await session.execute(
                text(
                    "UPDATE billing_orders SET status = 'paid', payment_id = :pid, "
                    "paid_at = NOW() WHERE order_id = :oid AND tenant_id = :tid "
                    "AND status = 'created'"
                ),
                {"pid": payment_id, "oid": order.order_id, "tid": order.tenant_id},
            )
    except Exception as exc:
        # The plan change already committed (it is applied first and is
        # idempotent); a retry re-applies it and records the payment.
        _log.error("billing_order_mark_paid_failed order=%s: %s", order.order_id, exc)
        raise HTTPException(503, "Payment recorded partially; retry verification") from exc
    order.status = "paid"
    order.payment_id = payment_id


@router.post("/create-order")
async def create_razorpay_order(request: Request, body: CreateOrderRequest) -> dict[str, Any]:
    """Create a Razorpay order for plan upgrade and record it server-side."""
    tenant = _require_tenant(request)

    if body.plan not in PLAN_PRICES:
        raise HTTPException(400, f"Unknown plan: {body.plan}")

    amount = PLAN_PRICES[body.plan][body.cycle]
    rz = _get_razorpay()

    if rz is None:
        from app.core.config import get_settings

        # Development / demo mode: a mock order so the UI still functions. It is
        # still recorded, so a mock verification upgrades to exactly this plan.
        order = _Order(
            order_id=f"order_mock_{uuid.uuid4().hex}",
            tenant_id=tenant.tenant_id,
            plan=body.plan,
            cycle=body.cycle,
            amount=amount,
            currency=body.currency,
            is_mock=True,
        )
        await _save_order(request, order)
        return {
            "order_id": order.order_id,
            "amount": amount,
            "currency": body.currency,
            "plan": body.plan,
            "cycle": body.cycle,
            "razorpay_key_id": get_settings().razorpay_key_id,
            "is_mock": True,
            "message": (
                "Razorpay not configured. "
                "Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to enable payments."
            ),
        }

    try:
        from app.core.config import get_settings

        order_data: dict[str, Any] = {
            "amount": amount,
            "currency": body.currency,
            "receipt": f"{tenant.tenant_id[:16]}_{body.plan}_{body.cycle}",
            "notes": {
                "tenant_id": tenant.tenant_id,
                "plan": body.plan,
                "cycle": body.cycle,
            },
        }
        rz_order = rz.order.create(data=order_data)
    except Exception as exc:
        _log.error("Razorpay order creation failed: %s", exc)
        raise HTTPException(502, f"Payment service error: {exc}") from exc

    await _save_order(
        request,
        _Order(
            order_id=str(rz_order["id"]),
            tenant_id=tenant.tenant_id,
            plan=body.plan,
            cycle=body.cycle,
            amount=int(rz_order.get("amount", amount)),
            currency=str(rz_order.get("currency", body.currency)),
            is_mock=False,
        ),
    )
    return {
        "order_id": rz_order["id"],
        "amount": rz_order["amount"],
        "currency": rz_order["currency"],
        "plan": body.plan,
        "cycle": body.cycle,
        "razorpay_key_id": get_settings().razorpay_key_id,
        "is_mock": False,
    }


@router.post("/verify-payment")
async def verify_razorpay_payment(request: Request, body: VerifyPaymentRequest) -> dict[str, Any]:
    """Verify a Razorpay payment and upgrade the tenant to the ORDER's plan.

    The plan comes only from the server-side order record created by
    ``/billing/create-order`` for this tenant; ``body.plan``/``body.cycle`` are
    ignored. A failure to record the upgrade is an error, never a "success".
    """
    tenant = _require_tenant(request)
    rz = _get_razorpay()

    if rz is None:
        from app.core.config import get_settings

        settings = get_settings()
        if not settings.allow_mock_payments:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Payment service not configured. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET."
                ),
            )

    order = await _load_order(request, tenant.tenant_id, body.razorpay_order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown payment order")

    if rz is None:
        if not order.is_mock:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Order requires a real payment")
        _log.warning(
            "MOCK_PAYMENT_ACCEPTED: allow_mock_payments=True — NEVER USE IN PRODUCTION. "
            "tenant=%s order=%s plan=%s",
            tenant.tenant_id,
            order.order_id,
            order.plan,
        )
    else:
        if order.is_mock:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Mock orders cannot be paid")
        try:
            from app.core.config import get_settings

            generated_signature = hmac.new(
                get_settings().razorpay_key_secret.encode(),
                f"{body.razorpay_order_id}|{body.razorpay_payment_id}".encode(),
                hashlib.sha256,
            ).hexdigest()
        except Exception as exc:
            _log.error("Payment verification failed: %s", exc)
            raise HTTPException(502, f"Payment verification error: {exc}") from exc
        if not hmac.compare_digest(generated_signature, body.razorpay_signature):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid payment signature")

    try:
        return await _apply_paid_order(request, order, body.razorpay_payment_id)
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("Payment verification failed: %s", exc)
        raise HTTPException(502, f"Payment verification error: {exc}") from exc


async def _apply_paid_order(request: Request, order: _Order, payment_id: str) -> dict[str, Any]:
    """Apply a verified payment for *order* exactly once (idempotent on retries)."""
    if order.status == "paid":
        if order.payment_id and order.payment_id != payment_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Order already paid")
        return _upgrade_result(order, payment_id)
    # Plan first, then mark paid: if marking fails, a retry re-applies the (same,
    # idempotent) plan instead of short-circuiting on a "paid" order whose plan
    # change never landed.
    await _upgrade_tenant_plan(request, order.tenant_id, order.plan)
    await _mark_order_paid(request, order, payment_id)
    return _upgrade_result(order, payment_id)


def _upgrade_result(order: _Order, payment_id: str) -> dict[str, Any]:
    return {
        "status": "success",
        "plan": order.plan,
        "cycle": order.cycle,
        "payment_id": payment_id,
        "order_id": order.order_id,
        "message": f"Successfully upgraded to {order.plan.title()} plan!",
        "limits": PLAN_FEATURES.get(order.plan, {}),
    }


async def _upgrade_tenant_plan(request: Request, tenant_id: str, plan: str) -> None:
    """Durably set the tenant's plan. Raises HTTP 503 if it could not be recorded.

    Previously this called a non-existent ``TenantService.update_plan``, logged
    the AttributeError as a warning and returned "Successfully upgraded" — the
    customer paid and stayed on their old plan.
    """
    tenant_service = getattr(request.app.state, "tenant_service", None)
    update_plan = getattr(tenant_service, "update_plan", None)
    if update_plan is None:
        raise HTTPException(503, "Tenant service unavailable; plan not changed")
    try:
        await update_plan(tenant_id, plan)
    except Exception as exc:
        _log.error("tenant_plan_update_failed tenant=%s plan=%s: %s", tenant_id, plan, exc)
        raise HTTPException(
            503, "Payment verified but the plan change could not be recorded; retry"
        ) from exc


@router.post("/webhook")
async def razorpay_webhook(request: Request) -> dict[str, Any]:
    """Handle Razorpay webhook events.

    The plan is derived from the server-side order record (looked up by the
    payment's ``order_id``), never from ``notes``; ``notes.tenant_id`` (written
    by create-order) only selects the RLS scope, and the order row must belong
    to that tenant. A failure to apply the upgrade answers 5xx so Razorpay
    retries, instead of a 200 that silently drops a paid upgrade.
    """
    body = await request.body()

    webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    if not webhook_secret:
        raise HTTPException(
            status_code=503,
            detail="Billing webhook not configured. Set RAZORPAY_WEBHOOK_SECRET.",
        )

    sig = request.headers.get("x-razorpay-signature", "")
    expected = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook signature")

    try:
        event = json.loads(body)
    except Exception as exc:
        # Signed but unparseable: a 400, never a 200 that drops the event.
        _log.error("Webhook processing error: %s", exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed webhook payload") from exc
    event_type: str = event.get("event", "")

    if event_type in ("payment.captured", "order.paid"):
        payload = event.get("payload", {})
        payment = payload.get("payment", {}).get("entity", {}) or {}
        rz_order = payload.get("order", {}).get("entity", {}) or {}
        order_id = str(payment.get("order_id") or rz_order.get("id") or "")
        notes = payment.get("notes") or rz_order.get("notes") or {}
        tenant_id = str(notes.get("tenant_id") or "")
        payment_id = str(payment.get("id") or "")
        if event_type == "payment.captured":
            paid_amount = int(payment.get("amount") or 0)
        else:
            paid_amount = int(rz_order.get("amount_paid") or 0)
        if not (order_id and tenant_id):
            _log.warning("webhook_%s_missing_order_or_tenant", event_type)
            return {"status": "ignored", "event": event_type}

        order = await _load_order(request, tenant_id, order_id)
        if order is None or order.is_mock:
            _log.warning("webhook_unknown_order order=%s tenant=%s", order_id, tenant_id)
            return {"status": "ignored", "event": event_type}
        if paid_amount < order.amount:
            _log.error(
                "webhook_amount_mismatch order=%s paid=%s expected=%s",
                order_id,
                paid_amount,
                order.amount,
            )
            return {"status": "ignored", "event": event_type}
        await _apply_paid_order(request, order, payment_id or order.payment_id or order_id)
        _log.info("Plan upgraded via webhook: tenant=%s → %s", tenant_id, order.plan)

    elif event_type == "subscription.charged":
        # No subscription is ever created by this service, so there is no
        # server-side record to derive a plan from; notes alone are not trusted.
        _log.warning("webhook_subscription_charged_ignored: no server-side order record")

    return {"status": "ok", "event": event_type}


# ── Stripe (legacy checkout) webhook ──────────────────────────────────────────

_STRIPE_PAID_PLANS = frozenset({"starter", "professional", "enterprise"})


@router.post("/webhook/stripe")
async def stripe_webhook(request: Request) -> dict[str, Any]:
    """Apply Stripe Checkout subscription events to the tenant's plan.

    ``POST /billing/checkout`` created a Stripe subscription session, but no
    webhook ever consumed its result, so a paying customer was never moved to
    the plan they bought. This endpoint (public — under ``/billing/webhook`` in
    TenantMiddleware's bypass list; authenticated by the ``Stripe-Signature``
    header, verified against ``STRIPE_WEBHOOK_SECRET``):

    * ``checkout.session.completed`` (subscription mode, paid) → upgrade the
      tenant in ``client_reference_id`` to ``metadata.plan`` (both written
      server-side at session creation; they must agree);
    * ``customer.subscription.deleted`` → downgrade that tenant to ``free``.

    A plan change that cannot be recorded answers 503 so Stripe retries.
    """
    from app.triggers.webhooks.verifier import WebhookSignatureVerifier

    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(503, "Stripe webhook not configured. Set STRIPE_WEBHOOK_SECRET.")
    body = await request.body()
    header = request.headers.get("stripe-signature", "")
    if not WebhookSignatureVerifier().verify_stripe(body, header, secret):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid Stripe signature")
    try:
        event = json.loads(body)
        event_type = str(event.get("type", ""))
        obj = (event.get("data") or {}).get("object") or {}
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed Stripe payload") from exc

    if event_type == "checkout.session.completed":
        metadata = obj.get("metadata") or {}
        tenant_id = str(obj.get("client_reference_id") or "")
        plan = str(metadata.get("plan") or "").lower()
        if (
            obj.get("mode") != "subscription"
            or obj.get("payment_status") not in ("paid", "no_payment_required")
            or not tenant_id
            or str(metadata.get("tenant_id") or "") != tenant_id
            or plan not in _STRIPE_PAID_PLANS
        ):
            _log.warning("stripe_checkout_ignored event=%s", event.get("id"))
            return {"status": "ignored", "event": event_type}
        await _upgrade_tenant_plan(request, tenant_id, plan)
        _log.info("Plan upgraded via Stripe: tenant=%s → %s", tenant_id, plan)
        return {"status": "ok", "event": event_type, "plan": plan}

    if event_type == "customer.subscription.deleted":
        tenant_id = str((obj.get("metadata") or {}).get("tenant_id") or "")
        if not tenant_id:
            _log.warning("stripe_subscription_deleted_without_tenant id=%s", obj.get("id"))
            return {"status": "ignored", "event": event_type}
        await _upgrade_tenant_plan(request, tenant_id, "free")
        _log.info("Plan downgraded via Stripe cancellation: tenant=%s", tenant_id)
        return {"status": "ok", "event": event_type, "plan": "free"}

    return {"status": "ignored", "event": event_type}
