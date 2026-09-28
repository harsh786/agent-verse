"""Regression: typed webhooks acknowledged deliveries that fired nothing, and
Stripe deliveries could never pass signature verification.

* dispatch errors were suppressed and the route still answered ``accepted`` —
  the platform never redelivered;
* the route verified every type with the generic HMAC-of-body check, which a
  Stripe ``t=,v1=`` signature (over ``"<t>.<body>"``) can never satisfy.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.triggers import router as triggers_router
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore

TOKEN = "tokS_" + "s" * 40
SECRET = "whsec_" + "x" * 24


class _Dispatcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.fired = 0

    async def dispatch(self, spec: Any, payload: dict[str, Any], tenant_ctx: Any) -> None:
        if self.fail:
            raise RuntimeError("queue down")
        self.fired += 1


def _client(dispatcher: _Dispatcher, *, secret: str = "") -> TestClient:
    store = ScheduleStore()
    spec = TriggerSpec(
        trigger_type=TriggerType.STRIPE_WEBHOOK,
        webhook_token=TOKEN,
        webhook_signature_secret=secret,
    )
    store.create(
        goal_id="",
        spec=spec,
        tenant_ctx=TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k"),
        goal_template="handle stripe",
    )
    app = FastAPI()
    app.include_router(triggers_router)
    app.state.schedule_store = store
    app.state.trigger_dispatcher = dispatcher
    return TestClient(app, raise_server_exceptions=False)


def _stripe_headers(body: bytes, secret: str) -> dict[str, str]:
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {"content-type": "application/json", "stripe-signature": f"t={ts},v1={sig}"}


def test_genuine_stripe_signature_is_accepted_and_fires() -> None:
    disp = _Dispatcher()
    body = b'{"type": "invoice.paid", "id": "evt_1"}'
    r = _client(disp, secret=SECRET).post(
        f"/triggers/webhooks/stripe/{TOKEN}", content=body, headers=_stripe_headers(body, SECRET)
    )
    assert r.status_code == 200, r.text
    assert r.json()["dispatched"] == 1
    assert disp.fired == 1


def test_bad_stripe_signature_is_401() -> None:
    body = b'{"type": "invoice.paid"}'
    r = _client(_Dispatcher(), secret=SECRET).post(
        f"/triggers/webhooks/stripe/{TOKEN}", content=body, headers=_stripe_headers(body, "nope")
    )
    assert r.status_code == 401


def test_dispatch_failure_is_503_so_the_sender_retries() -> None:
    r = _client(_Dispatcher(fail=True)).post(
        f"/triggers/webhooks/stripe/{TOKEN}", json={"type": "invoice.paid"}
    )
    assert r.status_code == 503
