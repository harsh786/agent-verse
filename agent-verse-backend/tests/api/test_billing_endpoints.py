"""Endpoint-level coverage for app/api/billing.py — Razorpay + legacy Stripe flows.

Complements test_billing_real.py (source-text assertions) and
test_gst_billing_behavioral.py (GST invoice model) by exercising the actual
FastAPI routes: usage/subscription/upgrade/checkout/invoices/plans,
create-order/verify-payment and the Razorpay webhook, across both the
"not configured" (mock/demo) and "configured" (mocked SDK) branches.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.billing import router as billing_router
from app.tenancy.context import PlanTier, TenantContext

TENANT_ID = "tenant-billing-1"


def _make_app(*, with_tenant: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(billing_router)

    if with_tenant:

        @app.middleware("http")
        async def _inject_tenant(request: Any, call_next: Any) -> Any:
            request.state.tenant = TenantContext(
                tenant_id=TENANT_ID, plan=PlanTier.STARTER, api_key_id="key-1"
            )
            return await call_next(request)

    return app


@pytest.fixture
def app() -> FastAPI:
    return _make_app()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Any:
    """get_settings() is process-wide @lru_cache'd; env var changes in one
    test are otherwise invisible to it, and a cached Settings from this test
    would leak into unrelated later tests."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _clear_billing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "STRIPE_API_KEY",
        "RAZORPAY_KEY_SECRET",
        "RAZORPAY_KEY_ID",
        "RAZORPAY_WEBHOOK_SECRET",
        "ALLOW_MOCK_PAYMENTS",
        "ENVIRONMENT",
        "STRIPE_PRICE_STARTER",
        "STRIPE_PRICE_PROFESSIONAL",
        "STRIPE_PRICE_ENTERPRISE",
    ):
        monkeypatch.delenv(var, raising=False)


# ── GET /billing/usage ──────────────────────────────────────────────────────


def test_get_usage_requires_auth() -> None:
    bare = TestClient(_make_app(with_tenant=False))
    resp = bare.get("/billing/usage")
    assert resp.status_code == 401


def test_get_usage_no_service_default(client: TestClient) -> None:
    resp = client.get("/billing/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tenant_id"] == TENANT_ID
    assert data["total_cost_usd"] == 0.0


def test_get_usage_with_service(app: FastAPI, client: TestClient) -> None:
    usage_svc = MagicMock()
    usage_svc.get_usage_summary = AsyncMock(return_value={"tenant_id": TENANT_ID, "usage": {"x": 1}})
    app.state.usage_service = usage_svc
    resp = client.get("/billing/usage")
    assert resp.status_code == 200
    assert resp.json()["usage"] == {"x": 1}
    usage_svc.get_usage_summary.assert_awaited_once_with(TENANT_ID)


# ── GET /billing/subscription ───────────────────────────────────────────────


def test_get_subscription(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_x")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "rzp_secret")
    resp = client.get("/billing/subscription")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tenant_id"] == TENANT_ID
    assert data["plan"] == PlanTier.STARTER.value
    assert data["stripe_configured"] is True
    assert data["razorpay_configured"] is True


def test_get_subscription_requires_auth() -> None:
    bare = TestClient(_make_app(with_tenant=False))
    resp = bare.get("/billing/subscription")
    assert resp.status_code == 401


# ── POST /billing/upgrade ───────────────────────────────────────────────────


def test_upgrade_invalid_plan(client: TestClient) -> None:
    resp = client.post("/billing/upgrade", json={"plan": "bogus"})
    assert resp.status_code == 400


def test_upgrade_no_stripe_returns_pending(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    resp = client.post("/billing/upgrade", json={"plan": "starter"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"
    assert "Razorpay" in data["message"]


def test_upgrade_delegates_to_checkout(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_x")
    monkeypatch.setenv("STRIPE_PRICE_STARTER", "price_123")

    mock_stripe = MagicMock()
    mock_stripe.checkout.Session.create.return_value = MagicMock(
        url="https://stripe.example/session", id="cs_test_1"
    )
    monkeypatch.setitem(sys.modules, "stripe", mock_stripe)

    resp = client.post("/billing/upgrade", json={"plan": "starter"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["checkout_url"] == "https://stripe.example/session"
    assert data["session_id"] == "cs_test_1"


# ── POST /billing/checkout ──────────────────────────────────────────────────


def test_checkout_requires_auth() -> None:
    bare = TestClient(_make_app(with_tenant=False))
    resp = bare.post("/billing/checkout", json={"plan": "starter"})
    assert resp.status_code == 401


def test_checkout_stripe_not_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    resp = client.post("/billing/checkout", json={"plan": "starter"})
    assert resp.status_code == 503


def test_checkout_stripe_package_not_installed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STRIPE_API_KEY set but the `stripe` package genuinely isn't installed."""
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_x")
    monkeypatch.delitem(sys.modules, "stripe", raising=False)
    resp = client.post("/billing/checkout", json={"plan": "starter"})
    assert resp.status_code == 503
    assert "stripe package not installed" in resp.json()["detail"]


def test_checkout_unknown_plan_price_not_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_x")
    mock_stripe = MagicMock()
    monkeypatch.setitem(sys.modules, "stripe", mock_stripe)
    resp = client.post("/billing/checkout", json={"plan": "unknownplan"})
    assert resp.status_code == 400


def test_checkout_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_x")
    monkeypatch.setenv("STRIPE_PRICE_PROFESSIONAL", "price_pro")
    mock_stripe = MagicMock()
    mock_stripe.checkout.Session.create.return_value = MagicMock(
        url="https://stripe.example/pro", id="cs_pro_1"
    )
    monkeypatch.setitem(sys.modules, "stripe", mock_stripe)

    resp = client.post("/billing/checkout", json={"plan": "professional"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["checkout_url"] == "https://stripe.example/pro"
    assert data["session_id"] == "cs_pro_1"
    mock_stripe.checkout.Session.create.assert_called_once()
    _, kwargs = mock_stripe.checkout.Session.create.call_args
    assert kwargs["client_reference_id"] == TENANT_ID


def test_checkout_general_exception(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_x")
    monkeypatch.setenv("STRIPE_PRICE_STARTER", "price_starter")
    mock_stripe = MagicMock()
    mock_stripe.checkout.Session.create.side_effect = RuntimeError("stripe down")
    monkeypatch.setitem(sys.modules, "stripe", mock_stripe)

    resp = client.post("/billing/checkout", json={"plan": "starter"})
    assert resp.status_code == 500
    assert "stripe down" in resp.json()["detail"]


# ── GET /billing/invoices ───────────────────────────────────────────────────


def test_invoices_requires_auth() -> None:
    bare = TestClient(_make_app(with_tenant=False))
    resp = bare.get("/billing/invoices")
    assert resp.status_code == 401


def test_invoices_no_razorpay_dev_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")
    resp = client.get("/billing/invoices")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert all(item["is_demo"] for item in data)


def test_invoices_no_razorpay_production_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    resp = client.get("/billing/invoices")
    assert resp.status_code == 200
    assert resp.json() == []


def test_invoices_razorpay_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    mock_rz = MagicMock()
    mock_rz.payment.all.return_value = {
        "items": [
            {
                "id": "pay_1",
                "amount": 9900,
                "created_at": 1_700_000_000,
                "status": "captured",
                "notes": {"tenant_id": TENANT_ID, "plan": "professional", "cycle": "monthly"},
            },
            {
                "id": "pay_other_tenant",
                "amount": 100,
                "created_at": 1_700_000_000,
                "status": "captured",
                "notes": {"tenant_id": "someone-else"},
            },
        ]
    }
    monkeypatch.setattr("app.api.billing._get_razorpay", lambda: mock_rz)
    resp = client.get("/billing/invoices")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "pay_1"
    assert data[0]["status"] == "paid"


def test_invoices_razorpay_exception(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    mock_rz = MagicMock()
    mock_rz.payment.all.side_effect = RuntimeError("razorpay unreachable")
    monkeypatch.setattr("app.api.billing._get_razorpay", lambda: mock_rz)
    resp = client.get("/billing/invoices")
    assert resp.status_code == 200
    assert resp.json() == []


# ── GET /billing/plans ───────────────────────────────────────────────────────


def test_list_plans(client: TestClient) -> None:
    resp = client.get("/billing/plans")
    assert resp.status_code == 200
    data = resp.json()
    plan_ids = {p["plan_id"] for p in data}
    assert plan_ids == {"starter", "professional", "enterprise"}
    starter = next(p for p in data if p["plan_id"] == "starter")
    assert starter["prices"]["monthly_inr"] == 29.0
    assert starter["limits"]["agents"] == 5


def test_list_plans_requires_auth() -> None:
    bare = TestClient(_make_app(with_tenant=False))
    resp = bare.get("/billing/plans")
    assert resp.status_code == 401


# ── POST /billing/create-order ──────────────────────────────────────────────


def test_create_order_rejects_invalid_plan_at_validation(client: TestClient) -> None:
    """The pydantic pattern already blocks anything outside the three plans."""
    resp = client.post(
        "/billing/create-order", json={"plan": "bogus", "cycle": "monthly"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_order_unknown_plan_defensive_branch() -> None:
    """Directly exercise the defensive `plan not in PLAN_PRICES` 400 branch,
    which is unreachable via HTTP because the pydantic field pattern already
    restricts `plan` to the same three values."""
    from app.api.billing import CreateOrderRequest, create_razorpay_order

    request = MagicMock()
    request.state.tenant = TenantContext(
        tenant_id=TENANT_ID, plan=PlanTier.STARTER, api_key_id="k1"
    )
    body = CreateOrderRequest.model_construct(plan="bogus", cycle="monthly", currency="INR")
    with pytest.raises(Exception) as exc_info:
        await create_razorpay_order(request, body)
    assert "Unknown plan" in str(exc_info.value)


def test_create_order_mock_when_no_razorpay(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    resp = client.post(
        "/billing/create-order", json={"plan": "starter", "cycle": "monthly"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_mock"] is True
    assert data["amount"] == 2900


def test_create_order_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_billing_env(monkeypatch)
    mock_rz = MagicMock()
    mock_rz.order.create.return_value = {"id": "order_abc", "amount": 9900, "currency": "INR"}
    monkeypatch.setattr("app.api.billing._get_razorpay", lambda: mock_rz)
    resp = client.post(
        "/billing/create-order", json={"plan": "professional", "cycle": "monthly"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["order_id"] == "order_abc"
    assert data["is_mock"] is False


def test_create_order_exception(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_billing_env(monkeypatch)
    mock_rz = MagicMock()
    mock_rz.order.create.side_effect = RuntimeError("razorpay error")
    monkeypatch.setattr("app.api.billing._get_razorpay", lambda: mock_rz)
    resp = client.post(
        "/billing/create-order", json={"plan": "starter", "cycle": "monthly"}
    )
    assert resp.status_code == 502


# ── POST /billing/verify-payment ────────────────────────────────────────────


def _verify_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "razorpay_order_id": "order_1",
        "razorpay_payment_id": "pay_1",
        "razorpay_signature": "sig_1",
        "plan": "professional",
        "cycle": "monthly",
    }
    body.update(overrides)
    return body


def test_verify_payment_no_razorpay_mock_disallowed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    resp = client.post("/billing/verify-payment", json=_verify_body())
    assert resp.status_code == 503


def test_verify_payment_no_razorpay_mock_allowed(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("ALLOW_MOCK_PAYMENTS", "true")
    tenant_svc = MagicMock()
    tenant_svc.update_plan = AsyncMock()
    app.state.tenant_service = tenant_svc
    resp = client.post("/billing/verify-payment", json=_verify_body())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["plan"] == "professional"
    tenant_svc.update_plan.assert_awaited_once_with(TENANT_ID, "professional")


def test_verify_payment_invalid_signature(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "topsecret")
    mock_rz = MagicMock()
    monkeypatch.setattr("app.api.billing._get_razorpay", lambda: mock_rz)
    resp = client.post("/billing/verify-payment", json=_verify_body(razorpay_signature="wrong"))
    assert resp.status_code == 400


def test_verify_payment_valid_signature(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "topsecret")
    mock_rz = MagicMock()
    monkeypatch.setattr("app.api.billing._get_razorpay", lambda: mock_rz)

    sig = hmac.new(
        b"topsecret", b"order_1|pay_1", hashlib.sha256
    ).hexdigest()
    tenant_svc = MagicMock()
    tenant_svc.update_plan = AsyncMock()
    app.state.tenant_service = tenant_svc

    resp = client.post(
        "/billing/verify-payment", json=_verify_body(razorpay_signature=sig)
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["payment_id"] == "pay_1"


def test_verify_payment_tenant_service_update_failure_is_swallowed(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_upgrade_tenant_plan logs and swallows a tenant_service.update_plan failure
    rather than failing the whole request."""
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("ALLOW_MOCK_PAYMENTS", "true")
    tenant_svc = MagicMock()
    tenant_svc.update_plan = AsyncMock(side_effect=RuntimeError("db unreachable"))
    app.state.tenant_service = tenant_svc

    resp = client.post("/billing/verify-payment", json=_verify_body())
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


def test_verify_payment_unexpected_exception(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error raised *after* signature verification (inside the try block)
    must surface as a 502, not bubble up unhandled."""
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "topsecret")
    mock_rz = MagicMock()
    monkeypatch.setattr("app.api.billing._get_razorpay", lambda: mock_rz)

    sig = hmac.new(b"topsecret", b"order_1|pay_1", hashlib.sha256).hexdigest()
    monkeypatch.setattr(
        "app.api.billing._upgrade_tenant_plan",
        AsyncMock(side_effect=RuntimeError("db exploded")),
    )
    resp = client.post(
        "/billing/verify-payment", json=_verify_body(razorpay_signature=sig)
    )
    assert resp.status_code == 502


# ── POST /billing/webhook ────────────────────────────────────────────────────


def test_webhook_no_secret_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_billing_env(monkeypatch)
    resp = client.post("/billing/webhook", json={"event": "payment.captured"})
    assert resp.status_code == 503


def test_webhook_invalid_signature(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsecret")
    resp = client.post(
        "/billing/webhook",
        json={"event": "payment.captured"},
        headers={"x-razorpay-signature": "bogus"},
    )
    assert resp.status_code == 400


def _signed_webhook(client: TestClient, secret: str, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/billing/webhook",
        content=body,
        headers={"x-razorpay-signature": sig, "content-type": "application/json"},
    )


def test_get_razorpay_client_construction_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If razorpay.Client(...) itself raises, _get_razorpay must swallow it and
    return None rather than propagate (all callers treat None as "not configured")."""
    from app.api.billing import _get_razorpay

    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "topsecret")

    class _BoomClient:
        def __init__(self, *a: Any, **kw: Any) -> None:
            raise RuntimeError("bad credentials")

    mock_razorpay_module = MagicMock()
    mock_razorpay_module.Client = _BoomClient
    monkeypatch.setitem(sys.modules, "razorpay", mock_razorpay_module)

    assert _get_razorpay() is None


def test_webhook_payment_captured_upgrade_failure_is_logged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure inside the payment.captured upgrade path must not break the
    webhook response — Razorpay must still get a 200 acknowledgement."""
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsecret")
    monkeypatch.setattr(
        "app.api.billing._upgrade_tenant_plan",
        AsyncMock(side_effect=RuntimeError("upgrade failed")),
    )
    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_fail_1",
                    "notes": {"tenant_id": "wh-tenant-fail", "plan": "starter"},
                }
            }
        },
    }
    resp = _signed_webhook(client, "whsecret", payload)
    assert resp.status_code == 200
    assert resp.json()["event"] == "payment.captured"


def test_webhook_subscription_charged_upgrade_failure_is_logged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsecret")
    monkeypatch.setattr(
        "app.api.billing._upgrade_tenant_plan",
        AsyncMock(side_effect=RuntimeError("upgrade failed")),
    )
    payload = {
        "event": "subscription.charged",
        "payload": {
            "subscription": {
                "entity": {
                    "id": "sub_fail_1",
                    "notes": {"tenant_id": "wh-tenant-fail-2", "plan": "enterprise"},
                }
            }
        },
    }
    resp = _signed_webhook(client, "whsecret", payload)
    assert resp.status_code == 200


def test_webhook_payment_captured(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsecret")
    tenant_svc = MagicMock()
    tenant_svc.update_plan = AsyncMock()
    app.state.tenant_service = tenant_svc

    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_wh_1",
                    "notes": {"tenant_id": "wh-tenant", "plan": "starter", "cycle": "monthly"},
                }
            }
        },
    }
    resp = _signed_webhook(client, "whsecret", payload)
    assert resp.status_code == 200
    assert resp.json()["event"] == "payment.captured"
    tenant_svc.update_plan.assert_awaited_once_with("wh-tenant", "starter")


def test_webhook_subscription_charged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsecret")
    payload = {
        "event": "subscription.charged",
        "payload": {
            "subscription": {
                "entity": {
                    "id": "sub_1",
                    "notes": {"tenant_id": "wh-tenant-2", "plan": "enterprise"},
                }
            }
        },
    }
    resp = _signed_webhook(client, "whsecret", payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_webhook_order_paid(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsecret")
    payload = {
        "event": "order.paid",
        "payload": {
            "order": {
                "entity": {
                    "id": "order_9",
                    "notes": {"tenant_id": "wh-tenant-3", "plan": "starter"},
                }
            }
        },
    }
    resp = _signed_webhook(client, "whsecret", payload)
    assert resp.status_code == 200


def test_webhook_unknown_event(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsecret")
    resp = _signed_webhook(client, "whsecret", {"event": "something.else"})
    assert resp.status_code == 200
    assert resp.json()["event"] == "something.else"


def test_webhook_processing_error_returns_error_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A body that isn't valid JSON after signature check triggers the except branch."""
    _clear_billing_env(monkeypatch)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsecret")
    body = b"not-json"
    sig = hmac.new(b"whsecret", body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/billing/webhook",
        content=body,
        headers={"x-razorpay-signature": sig, "content-type": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "error"
