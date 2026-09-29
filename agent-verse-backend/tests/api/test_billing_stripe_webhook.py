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


# ── Past-due / failed-payment lifecycle ───────────────────────────────────────
# Regression: only checkout.session.completed and customer.subscription.deleted
# were handled, so a subscription whose renewal payment failed (past_due /
# unpaid) kept its paid plan indefinitely. Both events are now resolved against
# the LIVE subscription (Stripe does not guarantee event order, so a stale
# "active" event must not undo a downgrade).


def _live(status: str, plan: str = "professional", tenant: str = "tenant-1") -> dict[str, Any]:
    return {"id": "sub_1", "status": status, "metadata": {"tenant_id": tenant, "plan": plan}}


def _updated(status: str) -> dict[str, Any]:
    return {"type": "customer.subscription.updated", "data": {"object": _live(status)}}


def _invoice_failed(**over: Any) -> dict[str, Any]:
    obj = {"id": "in_1", "subscription": "sub_1", **over}
    return {"type": "invoice.payment_failed", "data": {"object": obj}}


@pytest.fixture
def live_sub(monkeypatch: pytest.MonkeyPatch) -> Any:
    import app.api.billing as billing

    state: dict[str, Any] = {"sub": _live("active"), "calls": []}

    async def _retrieve(sub_id: str) -> dict[str, Any]:
        state["calls"].append(sub_id)
        if isinstance(state["sub"], Exception):
            raise state["sub"]
        return dict(state["sub"])

    monkeypatch.setattr(billing, "_retrieve_subscription", _retrieve)
    return state


@pytest.mark.parametrize("status", ["past_due", "unpaid", "canceled", "incomplete_expired"])
def test_subscription_updated_to_a_delinquent_status_downgrades(live_sub: Any, status: str) -> None:
    live_sub["sub"] = _live(status)
    upd = AsyncMock()
    r = _post(_app(upd), _updated(status))
    assert r.status_code == 200, r.text
    assert r.json()["plan"] == "free"
    upd.assert_awaited_once_with("tenant-1", "free")
    assert live_sub["calls"] == ["sub_1"]


def test_invoice_payment_failed_downgrades_a_past_due_subscription(live_sub: Any) -> None:
    live_sub["sub"] = _live("past_due")
    upd = AsyncMock()
    r = _post(_app(upd), _invoice_failed())
    assert r.status_code == 200, r.text
    upd.assert_awaited_once_with("tenant-1", "free")


def test_invoice_payment_failed_new_api_shape(live_sub: Any) -> None:
    live_sub["sub"] = _live("unpaid")
    upd = AsyncMock()
    ev = _invoice_failed(
        subscription=None,
        parent={"subscription_details": {"subscription": "sub_1"}},
    )
    assert _post(_app(upd), ev).status_code == 200
    upd.assert_awaited_once_with("tenant-1", "free")


def test_recovered_subscription_restores_the_paid_plan(live_sub: Any) -> None:
    live_sub["sub"] = _live("active", plan="starter")
    upd = AsyncMock()
    r = _post(_app(upd), _updated("active"))
    assert r.status_code == 200
    upd.assert_awaited_once_with("tenant-1", "starter")


def test_stale_active_event_cannot_undo_a_downgrade(live_sub: Any) -> None:
    live_sub["sub"] = _live("past_due")
    upd = AsyncMock()
    _post(_app(upd), _updated("active"))
    upd.assert_awaited_once_with("tenant-1", "free")


def test_payment_failed_on_a_still_active_subscription_keeps_the_plan(live_sub: Any) -> None:
    live_sub["sub"] = _live("active")
    upd = AsyncMock()
    r = _post(_app(upd), _invoice_failed())
    assert r.status_code == 200
    upd.assert_awaited_once_with("tenant-1", "professional")


def test_subscription_without_tenant_metadata_is_ignored(live_sub: Any) -> None:
    live_sub["sub"] = {"id": "sub_1", "status": "past_due", "metadata": {}}
    upd = AsyncMock()
    r = _post(_app(upd), _updated("past_due"))
    assert r.json()["status"] == "ignored"
    upd.assert_not_awaited()


def test_invoice_without_a_subscription_is_ignored(live_sub: Any) -> None:
    upd = AsyncMock()
    r = _post(_app(upd), _invoice_failed(subscription=None))
    assert r.json()["status"] == "ignored"
    upd.assert_not_awaited()
    assert live_sub["calls"] == []


def test_unreachable_stripe_is_503_so_the_event_is_retried(live_sub: Any) -> None:
    live_sub["sub"] = RuntimeError("stripe down")
    upd = AsyncMock()
    r = _post(_app(upd), _updated("past_due"))
    assert r.status_code == 503
    upd.assert_not_awaited()


def test_unconfigured_stripe_api_is_503_not_a_silent_ignore() -> None:
    upd = AsyncMock()
    r = _post(_app(upd), _updated("past_due"))
    assert r.status_code == 503
    upd.assert_not_awaited()
