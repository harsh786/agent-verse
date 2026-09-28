"""Regression: paid plan upgrades must be durable and derived from the order.

Bugs fixed:
* ``_upgrade_tenant_plan`` called ``TenantService.update_plan`` — which did not
  exist. The AttributeError was swallowed and the API answered "Successfully
  upgraded" while the tenant's plan never changed.
* ``/billing/verify-payment`` upgraded to ``body.plan`` — whatever the client
  sent — so paying for Starter could claim Enterprise.
* The webhook trusted ``notes.plan`` and 200-acked upgrade failures (no retry).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.billing import router as billing_router
from app.services.tenant_service import TenantService
from app.tenancy.context import PlanTier, TenantContext


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self.store[key] = value

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            n += 1 if self.store.pop(k, None) is not None else 0
        return n


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.core.config import get_settings

    for var in ("RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET", "ALLOW_MOCK_PAYMENTS"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _app(tenant_svc: Any, tenant_id: str) -> FastAPI:
    app = FastAPI()
    app.include_router(billing_router)
    app.state.tenant_service = tenant_svc

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k1"
        )
        return await call_next(request)

    return app


# ── TenantService.update_plan ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_plan_exists_and_changes_resolved_plan() -> None:
    svc = TenantService()
    created = await svc.create_tenant("Acme", "acme@example.com")
    tid, raw = created["tenant_id"], created["api_key"]
    redis = _FakeRedis()
    svc.set_redis(redis)
    before = await svc.resolve_api_key(raw)
    assert before is not None and before.plan == PlanTier.FREE
    assert any(k.startswith("api_key:") for k in redis.store)  # cached with old plan

    await svc.update_plan(tid, "professional")

    # The shared cache entry carrying the old plan is gone...
    assert not any(k.startswith("api_key:") for k in redis.store)
    # ...so the next resolution (any replica) sees the new plan.
    after = await svc.resolve_api_key(raw)
    assert after is not None and after.plan == PlanTier.PROFESSIONAL
    assert (await svc.get_tenant(tid))["plan"] == "professional"


@pytest.mark.asyncio
async def test_update_plan_rejects_unknown_tenant_and_plan() -> None:
    from app.core.errors import NotFoundError

    svc = TenantService()
    with pytest.raises(NotFoundError):
        await svc.update_plan("nope", "starter")
    created = await svc.create_tenant("Acme", "acme2@example.com")
    with pytest.raises(ValueError):
        await svc.update_plan(created["tenant_id"], "platinum")


# ── verify-payment derives the plan from the server-side order ───────────────


def test_verify_payment_uses_order_plan_not_client_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOW_MOCK_PAYMENTS", "true")
    svc = MagicMock()
    svc.update_plan = AsyncMock()
    client = TestClient(_app(svc, "t-1"))

    order = client.post("/billing/create-order", json={"plan": "starter", "cycle": "monthly"})
    assert order.status_code == 200
    resp = client.post(
        "/billing/verify-payment",
        json={
            "razorpay_order_id": order.json()["order_id"],
            "razorpay_payment_id": "pay_1",
            "razorpay_signature": "x",
            "plan": "enterprise",  # attacker-controlled — must be ignored
            "cycle": "annual",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["plan"] == "starter"
    svc.update_plan.assert_awaited_once_with("t-1", "starter")


def test_verify_payment_unknown_order_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_MOCK_PAYMENTS", "true")
    svc = MagicMock()
    svc.update_plan = AsyncMock()
    client = TestClient(_app(svc, "t-1"))
    resp = client.post(
        "/billing/verify-payment",
        json={
            "razorpay_order_id": "order_forged",
            "razorpay_payment_id": "pay_1",
            "razorpay_signature": "x",
            "plan": "enterprise",
            "cycle": "monthly",
        },
    )
    assert resp.status_code == 404
    svc.update_plan.assert_not_awaited()


def test_verify_payment_other_tenants_order_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOW_MOCK_PAYMENTS", "true")
    svc = MagicMock()
    svc.update_plan = AsyncMock()
    app_a = _app(svc, "t-a")
    order_id = TestClient(app_a).post(
        "/billing/create-order", json={"plan": "enterprise", "cycle": "monthly"}
    ).json()["order_id"]
    # Same process store, different tenant.
    app_b = _app(svc, "t-b")
    app_b.state.billing_orders_mem = app_a.state.billing_orders_mem
    resp = TestClient(app_b).post(
        "/billing/verify-payment",
        json={"razorpay_order_id": order_id, "razorpay_payment_id": "p", "razorpay_signature": "x"},
    )
    assert resp.status_code == 404
    svc.update_plan.assert_not_awaited()


def test_verify_payment_plan_update_failure_is_an_error_not_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOW_MOCK_PAYMENTS", "true")
    svc = MagicMock()
    svc.update_plan = AsyncMock(side_effect=RuntimeError("db unreachable"))
    client = TestClient(_app(svc, "t-1"))
    order_id = client.post(
        "/billing/create-order", json={"plan": "professional", "cycle": "monthly"}
    ).json()["order_id"]
    resp = client.post(
        "/billing/verify-payment",
        json={"razorpay_order_id": order_id, "razorpay_payment_id": "p", "razorpay_signature": "x"},
    )
    assert resp.status_code == 503
    assert "success" not in resp.text.lower()


def test_verify_payment_without_update_plan_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The original defect: a tenant service lacking update_plan → fake success."""
    monkeypatch.setenv("ALLOW_MOCK_PAYMENTS", "true")
    client = TestClient(_app(object(), "t-1"))
    order_id = client.post(
        "/billing/create-order", json={"plan": "starter", "cycle": "monthly"}
    ).json()["order_id"]
    resp = client.post(
        "/billing/verify-payment",
        json={"razorpay_order_id": order_id, "razorpay_payment_id": "p", "razorpay_signature": "x"},
    )
    assert resp.status_code == 503
    assert hasattr(TenantService, "update_plan")


def test_verify_payment_is_idempotent_and_end_to_end_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real TenantService: a verified order changes the plan the API key resolves to."""
    import asyncio

    monkeypatch.setenv("ALLOW_MOCK_PAYMENTS", "true")
    svc = TenantService()
    created = asyncio.run(svc.create_tenant("Acme", "acme3@example.com"))
    tid, raw = created["tenant_id"], created["api_key"]
    client = TestClient(_app(svc, tid))
    order_id = client.post(
        "/billing/create-order", json={"plan": "enterprise", "cycle": "annual"}
    ).json()["order_id"]
    body = {"razorpay_order_id": order_id, "razorpay_payment_id": "p1", "razorpay_signature": "x"}
    assert client.post("/billing/verify-payment", json=body).status_code == 200
    ctx = asyncio.run(svc.resolve_api_key(raw))
    assert ctx is not None and ctx.plan == PlanTier.ENTERPRISE
    # Retry of the same payment is fine; a different payment for a paid order is not.
    assert client.post("/billing/verify-payment", json=body).status_code == 200
    body["razorpay_payment_id"] = "p2"
    assert client.post("/billing/verify-payment", json=body).status_code == 409


def test_real_signature_flow_uses_stored_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "topsecret")
    rz = MagicMock()
    rz.order.create.return_value = {"id": "order_real_1", "amount": 2900, "currency": "INR"}
    monkeypatch.setattr("app.api.billing._get_razorpay", lambda: rz)
    svc = MagicMock()
    svc.update_plan = AsyncMock()
    client = TestClient(_app(svc, "t-1"))
    assert (
        client.post("/billing/create-order", json={"plan": "starter", "cycle": "monthly"})
    ).status_code == 200
    sig = hmac.new(b"topsecret", b"order_real_1|pay_9", hashlib.sha256).hexdigest()
    resp = client.post(
        "/billing/verify-payment",
        json={
            "razorpay_order_id": "order_real_1",
            "razorpay_payment_id": "pay_9",
            "razorpay_signature": sig,
            "plan": "enterprise",
        },
    )
    assert resp.status_code == 200
    svc.update_plan.assert_awaited_once_with("t-1", "starter")


# ── webhook ──────────────────────────────────────────────────────────────────


def _post_webhook(client: TestClient, payload: dict[str, Any]) -> Any:
    raw = json.dumps(payload).encode()
    sig = hmac.new(b"whsecret", raw, hashlib.sha256).hexdigest()
    return client.post(
        "/billing/webhook",
        content=raw,
        headers={"x-razorpay-signature": sig, "content-type": "application/json"},
    )


def _captured(order_id: str, tenant_id: str, amount: int, plan: str) -> dict[str, Any]:
    return {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_wh",
                    "order_id": order_id,
                    "amount": amount,
                    "notes": {"tenant_id": tenant_id, "plan": plan},
                }
            }
        },
    }


def _real_order_app(monkeypatch: pytest.MonkeyPatch, svc: Any) -> TestClient:
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "topsecret")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsecret")
    rz = MagicMock()
    rz.order.create.return_value = {"id": "order_wh_1", "amount": 2900, "currency": "INR"}
    monkeypatch.setattr("app.api.billing._get_razorpay", lambda: rz)
    client = TestClient(_app(svc, "t-1"))
    client.post("/billing/create-order", json={"plan": "starter", "cycle": "monthly"})
    return client


def test_webhook_plan_comes_from_order_not_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = MagicMock()
    svc.update_plan = AsyncMock()
    client = _real_order_app(monkeypatch, svc)
    resp = _post_webhook(client, _captured("order_wh_1", "t-1", 2900, "enterprise"))
    assert resp.status_code == 200
    svc.update_plan.assert_awaited_once_with("t-1", "starter")


def test_webhook_unknown_order_or_short_payment_does_not_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = MagicMock()
    svc.update_plan = AsyncMock()
    client = _real_order_app(monkeypatch, svc)
    assert _post_webhook(client, _captured("order_x", "t-1", 2900, "enterprise")).status_code == 200
    assert _post_webhook(client, _captured("order_wh_1", "t-1", 100, "starter")).status_code == 200
    svc.update_plan.assert_not_awaited()


def test_webhook_upgrade_failure_is_5xx_so_provider_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = MagicMock()
    svc.update_plan = AsyncMock(side_effect=RuntimeError("db down"))
    client = _real_order_app(monkeypatch, svc)
    resp = _post_webhook(client, _captured("order_wh_1", "t-1", 2900, "starter"))
    assert resp.status_code == 503
