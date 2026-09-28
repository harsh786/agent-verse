"""Regression: Stripe checkout never activated the purchased plan.

``POST /billing/checkout`` created a Stripe subscription session but nothing
consumed its completion, so a paying customer stayed on their old plan.
``POST /billing/webhook/stripe`` now verifies the Stripe signature and applies
the plan change (503 when it cannot be recorded, so Stripe retries).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.billing import router as billing_router
from app.tenancy.middleware import TenantMiddleware

SECRET = "whsec_" + "k" * 24


async def _no_key(_k: str) -> None:
    return None


def _app(update_plan: AsyncMock) -> FastAPI:
    app = FastAPI()
    # The real middleware: proves the route is reachable without an API key.
    app.add_middleware(TenantMiddleware, key_resolver=_no_key)
    app.include_router(billing_router)
    app.state.tenant_service = type("TS", (), {"update_plan": update_plan})()
    return app


def _post(app: FastAPI, event: dict[str, Any], *, secret: str = SECRET) -> Any:
    body = json.dumps(event).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return TestClient(app, raise_server_exceptions=False).post(
        "/billing/webhook/stripe",
        content=body,
        headers={"content-type": "application/json", "stripe-signature": f"t={ts},v1={sig}"},
    )


def _completed(**over: Any) -> dict[str, Any]:
    obj = {
        "mode": "subscription",
        "payment_status": "paid",
        "client_reference_id": "tenant-1",
        "metadata": {"tenant_id": "tenant-1", "plan": "professional"},
        **over,
    }
    return {"id": "evt_1", "type": "checkout.session.completed", "data": {"object": obj}}


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)


def test_completed_checkout_upgrades_the_tenant() -> None:
    upd = AsyncMock()
    r = _post(_app(upd), _completed())
    assert r.status_code == 200, r.text
    upd.assert_awaited_once_with("tenant-1", "professional")


def test_bad_signature_is_400_and_changes_nothing() -> None:
    upd = AsyncMock()
    r = _post(_app(upd), _completed(), secret="attacker")
    assert r.status_code == 400
    upd.assert_not_awaited()


def test_unconfigured_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET")
    assert _post(_app(AsyncMock()), _completed()).status_code == 503


@pytest.mark.parametrize(
    "over",
    [
        {"payment_status": "unpaid"},
        {"mode": "payment"},
        {"metadata": {"tenant_id": "someone-else", "plan": "enterprise"}},
        {"metadata": {"tenant_id": "tenant-1", "plan": "free-forever"}},
    ],
)
def test_unpaid_or_inconsistent_sessions_change_nothing(over: dict[str, Any]) -> None:
    upd = AsyncMock()
    r = _post(_app(upd), _completed(**over))
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
    upd.assert_not_awaited()


def test_plan_write_failure_is_503_so_stripe_retries() -> None:
    r = _post(_app(AsyncMock(side_effect=RuntimeError("db down"))), _completed())
    assert r.status_code == 503


def test_subscription_deleted_downgrades_to_free() -> None:
    upd = AsyncMock()
    ev = {
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_1", "metadata": {"tenant_id": "tenant-1"}}},
    }
    assert _post(_app(upd), ev).status_code == 200
    upd.assert_awaited_once_with("tenant-1", "free")
