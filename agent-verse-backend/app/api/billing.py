"""Billing & subscription management API — Razorpay + legacy Stripe support."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.tenancy.context import TenantContext

router = APIRouter(prefix="/billing", tags=["billing"])
_log = logging.getLogger(__name__)

# ── Plan definitions ───────────────────────────────────────────────────────────

PLAN_PRICES = {
    "starter":      {"monthly": 2900,  "annual": 27840},   # INR paise (₹29 / ₹278.40)
    "professional": {"monthly": 9900,  "annual": 95040},   # INR paise (₹99 / ₹950.40)
    "enterprise":   {"monthly": 49900, "annual": 479040},  # INR paise (₹499 / ₹4790.40)
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
        import razorpay  # noqa: PLC0415
        from app.core.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        if not settings.razorpay_key_secret:
            return None
        return razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    except Exception:
        return None


def _inr_to_usd(amount_inr: float) -> float:
    """Convert INR to approximate USD using the configurable exchange rate."""
    from app.core.config import get_settings  # noqa: PLC0415

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
    plan: str
    cycle: str


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

    from app.core.config import get_settings  # noqa: PLC0415

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

    from app.core.config import get_settings  # noqa: PLC0415

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
    from app.core.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    if not settings.stripe_api_key:
        raise HTTPException(
            503,
            "Stripe billing not configured. Use /billing/create-order for Razorpay.",
        )
    try:
        import stripe  # noqa: PLC0415

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


@router.get("/invoices")
async def list_invoices(request: Request) -> list[dict[str, Any]]:
    """Return billing invoice history from Razorpay or empty list."""
    tenant = _require_tenant(request)

    rz = _get_razorpay()
    if rz is None:
        from app.core.config import get_settings  # noqa: PLC0415

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
        payments = rz.payment.all({
            "count": 20,
            "notes[tenant_id]": tenant.tenant_id,
        })

        invoices = []
        for payment in (payments.get("items", []) if isinstance(payments, dict) else []):
            notes = payment.get("notes", {})
            if notes.get("tenant_id") != tenant.tenant_id:
                continue  # Extra safety check

            amount_inr = float(payment.get("amount", 0)) / 100  # paise to rupees
            amount_usd = _inr_to_usd(amount_inr)

            import datetime as _dt
            invoices.append({
                "id": payment.get("id", ""),
                "date": _dt.datetime.fromtimestamp(
                    payment.get("created_at", 0),
                    tz=_dt.timezone.utc,
                ).isoformat(),
                "amount_usd": round(amount_usd, 2),
                "amount_inr": round(amount_inr, 2),
                "status": (
                    "paid" if payment.get("status") == "captured"
                    else payment.get("status", "unknown")
                ),
                "plan": notes.get("plan", "unknown"),
                "cycle": notes.get("cycle", "monthly"),
                "pdf_url": None,  # Razorpay doesn't provide PDF invoices via API directly
                "payment_id": payment.get("id"),
            })

        return invoices
    except Exception as exc:
        _log.error("Failed to fetch Razorpay invoices: %s", exc)
        return []


# ── Razorpay endpoints ─────────────────────────────────────────────────────────


@router.get("/plans")
async def list_plans(request: Request) -> list[dict[str, Any]]:
    """Return available billing plans with Razorpay pricing info."""
    _require_tenant(request)
    from app.core.config import get_settings  # noqa: PLC0415

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


@router.post("/create-order")
async def create_razorpay_order(
    request: Request, body: CreateOrderRequest
) -> dict[str, Any]:
    """Create a Razorpay order for plan upgrade."""
    tenant = _require_tenant(request)

    if body.plan not in PLAN_PRICES:
        raise HTTPException(400, f"Unknown plan: {body.plan}")

    amount = PLAN_PRICES[body.plan][body.cycle]
    rz = _get_razorpay()

    if rz is None:
        from app.core.config import get_settings  # noqa: PLC0415

        # Development / demo mode: return a mock order so the UI still functions
        return {
            "order_id": f"order_{tenant.tenant_id[:8]}_mock",
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
        from app.core.config import get_settings  # noqa: PLC0415

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
        order = rz.order.create(data=order_data)
        return {
            "order_id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "plan": body.plan,
            "cycle": body.cycle,
            "razorpay_key_id": get_settings().razorpay_key_id,
            "is_mock": False,
        }
    except Exception as exc:
        _log.error("Razorpay order creation failed: %s", exc)
        raise HTTPException(502, f"Payment service error: {exc}")


@router.post("/verify-payment")
async def verify_razorpay_payment(
    request: Request, body: VerifyPaymentRequest
) -> dict[str, Any]:
    """Verify Razorpay payment signature and upgrade the tenant plan."""
    tenant = _require_tenant(request)
    rz = _get_razorpay()

    if rz is None:
        from app.core.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        if not settings.allow_mock_payments:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Payment service not configured. "
                    "Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET."
                ),
            )
        _log.warning(
            "MOCK_PAYMENT_ACCEPTED: allow_mock_payments=True — NEVER USE IN PRODUCTION. "
            "tenant=%s plan=%s",
            tenant.tenant_id,
            body.plan,
        )
        return await _upgrade_tenant_plan(
            request, tenant, body.plan, body.cycle, body.razorpay_payment_id
        )

    try:
        from app.core.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        generated_signature = hmac.new(
            settings.razorpay_key_secret.encode(),
            f"{body.razorpay_order_id}|{body.razorpay_payment_id}".encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(generated_signature, body.razorpay_signature):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid payment signature")

        return await _upgrade_tenant_plan(
            request, tenant, body.plan, body.cycle, body.razorpay_payment_id
        )
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("Payment verification failed: %s", exc)
        raise HTTPException(502, f"Payment verification error: {exc}")


async def _upgrade_tenant_plan(
    request: Request,
    tenant: TenantContext,
    plan: str,
    cycle: str,
    payment_id: str,
) -> dict[str, Any]:
    """Upgrade tenant plan after successful payment verification."""
    tenant_service = getattr(request.app.state, "tenant_service", None)
    if tenant_service is not None:
        try:
            await tenant_service.update_plan(tenant.tenant_id, plan)
        except Exception as exc:
            _log.warning("Could not update tenant plan in DB: %s", exc)

    return {
        "status": "success",
        "plan": plan,
        "cycle": cycle,
        "payment_id": payment_id,
        "message": f"Successfully upgraded to {plan.title()} plan!",
        "limits": PLAN_FEATURES.get(plan, {}),
    }


@router.post("/webhook")
async def razorpay_webhook(request: Request) -> dict[str, Any]:
    """Handle Razorpay webhook events."""
    body = await request.body()

    webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    if not webhook_secret:
        raise HTTPException(
            status_code=503,
            detail="Billing webhook not configured. Set RAZORPAY_WEBHOOK_SECRET.",
        )

    sig = request.headers.get("x-razorpay-signature", "")
    expected = hmac.new(
        webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook signature")

    try:
        event = json.loads(body)
        event_type: str = event.get("event", "")

        if event_type == "payment.captured":
            payment = event.get("payload", {}).get("payment", {}).get("entity", {})
            notes = payment.get("notes", {})
            tenant_id = notes.get("tenant_id")
            plan = notes.get("plan", "professional")
            cycle = notes.get("cycle", "monthly")
            payment_id = payment.get("id", "")
            if tenant_id and plan:
                _log.info(
                    "Payment captured: tenant=%s plan=%s payment=%s",
                    tenant_id,
                    plan,
                    payment_id,
                )
                try:
                    from app.tenancy.context import PlanTier, TenantContext  # noqa: PLC0415

                    tenant_ctx = TenantContext(
                        tenant_id=tenant_id,
                        plan=PlanTier.FREE,
                        api_key_id="webhook",
                    )
                    await _upgrade_tenant_plan(request, tenant_ctx, plan, cycle, payment_id)
                    _log.info("Plan upgraded via webhook: tenant=%s → %s", tenant_id, plan)
                except Exception as exc:
                    _log.error("Webhook plan upgrade failed: %s", exc)

        elif event_type in ("subscription.charged", "order.paid"):
            entity_key = "subscription" if event_type == "subscription.charged" else "order"
            entity = (
                event.get("payload", {}).get(entity_key, {}).get("entity", {})
            )
            notes = entity.get("notes", {})
            tenant_id = notes.get("tenant_id")
            plan = notes.get("plan")
            if tenant_id and plan:
                try:
                    from app.tenancy.context import PlanTier, TenantContext  # noqa: PLC0415

                    tenant_ctx = TenantContext(
                        tenant_id=tenant_id,
                        plan=PlanTier.FREE,
                        api_key_id="webhook",
                    )
                    await _upgrade_tenant_plan(
                        request, tenant_ctx, plan, "monthly", entity.get("id", "")
                    )
                except Exception as exc:
                    _log.error("Subscription webhook upgrade failed: %s", exc)

        return {"status": "ok", "event": event_type}
    except Exception as exc:
        _log.error("Webhook processing error: %s", exc)
        return {"status": "error", "message": str(exc)}
