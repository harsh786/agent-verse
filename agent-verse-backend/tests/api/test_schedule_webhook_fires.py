"""Regression: ``POST /webhooks/{token}`` must actually fire the webhook trigger.

Old bug: the endpoint looked the token up in a process-local ``{token:
schedule_id}`` dict and returned ``{"status": "ok"}`` WITHOUT dispatching
anything — every inbound webhook was silently dropped while the caller was told
it succeeded. The dict was also global across tenants and empty after a
restart. It now resolves the caller's own webhook trigger from the
ScheduleStore and runs it through the real ``TriggerDispatcher`` (the same path
as ``POST /triggers/webhooks/webhook/{token}``).
"""

from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.schedules import router as schedules_router
from app.api.schedules import webhooks_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from app.triggers.store import ScheduleStore

_CTX_A = TenantContext(tenant_id="tenant-a", plan=PlanTier.PROFESSIONAL, api_key_id="ka")
_CTX_B = TenantContext(tenant_id="tenant-b", plan=PlanTier.PROFESSIONAL, api_key_id="kb")
_KEYS = {"key-a": _CTX_A, "key-b": _CTX_B}


def _app(dispatcher: Any | None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _KEYS.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(schedules_router)
    app.include_router(webhooks_router)
    app.state.schedule_store = ScheduleStore()
    if dispatcher is not None:
        app.state.trigger_dispatcher = dispatcher
    return app


def _dispatcher() -> AsyncMock:
    d = AsyncMock()
    d.dispatch.return_value = SimpleNamespace(
        goal_id="goal-123", goal_created=True, skip_reason=None, fired_at="now"
    )
    return d


def _create_webhook(client: TestClient, key: str = "key-a") -> dict[str, Any]:
    r = client.post(
        "/schedules",
        json={"trigger_type": "webhook", "name": "hook", "goal_template": "Handle {{x}}"},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 201
    return r.json()  # type: ignore[no-any-return]


def test_webhook_token_dispatches_the_trigger() -> None:
    dispatcher = _dispatcher()
    client = TestClient(_app(dispatcher))
    rec = _create_webhook(client)
    token = rec["spec"]["webhook_token"]

    r = client.post(f"/webhooks/{token}", json={"x": 1}, headers={"X-API-Key": "key-a"})

    assert r.status_code == 202
    body = r.json()
    assert body["schedule_id"] == rec["schedule_id"]
    assert body["goal_id"] == "goal-123"
    assert body["goal_created"] is True
    dispatcher.dispatch.assert_awaited_once()
    spec, payload, tenant = dispatcher.dispatch.await_args.args
    assert spec.webhook_token == token
    assert payload == {"x": 1}
    assert tenant.tenant_id == "tenant-a"


def test_other_tenants_token_is_not_found() -> None:
    dispatcher = _dispatcher()
    client = TestClient(_app(dispatcher))
    token = _create_webhook(client, "key-a")["spec"]["webhook_token"]
    r = client.post(f"/webhooks/{token}", json={}, headers={"X-API-Key": "key-b"})
    assert r.status_code == 404
    dispatcher.dispatch.assert_not_awaited()


def test_token_resolves_without_any_process_local_token_map() -> None:
    # The token is resolved from the schedule record (the schedules row when
    # DB-backed — cross-replica coverage lives in test_schedules_cross_replica),
    # never from an app.state map that another replica would not have.
    dispatcher = _dispatcher()
    app = _app(dispatcher)
    client = TestClient(app)
    token = _create_webhook(client)["spec"]["webhook_token"]
    assert not hasattr(app.state, "_webhook_tokens")
    r = client.post(f"/webhooks/{token}", json={}, headers={"X-API-Key": "key-a"})
    assert r.status_code == 202
    dispatcher.dispatch.assert_awaited_once()


def test_no_dispatcher_is_503_not_fake_success() -> None:
    client = TestClient(_app(None))
    token = _create_webhook(client)["spec"]["webhook_token"]
    r = client.post(f"/webhooks/{token}", json={}, headers={"X-API-Key": "key-a"})
    assert r.status_code == 503


def test_paused_trigger_is_409_and_not_dispatched() -> None:
    dispatcher = _dispatcher()
    client = TestClient(_app(dispatcher))
    rec = _create_webhook(client)
    client.post(f"/schedules/{rec['schedule_id']}/pause", headers={"X-API-Key": "key-a"})
    r = client.post(
        f"/webhooks/{rec['spec']['webhook_token']}", json={}, headers={"X-API-Key": "key-a"}
    )
    assert r.status_code == 409
    dispatcher.dispatch.assert_not_awaited()


def test_signing_secret_is_enforced() -> None:
    dispatcher = _dispatcher()
    app = _app(dispatcher)
    client = TestClient(app)
    rec = _create_webhook(client)
    stored = app.state.schedule_store.get(rec["schedule_id"], tenant_ctx=_CTX_A)
    stored["spec"].webhook_signature_secret = "whsec"
    token = rec["spec"]["webhook_token"]
    body = b'{"x": 2}'

    bad = client.post(
        f"/webhooks/{token}",
        content=body,
        headers={"X-API-Key": "key-a", "Content-Type": "application/json"},
    )
    assert bad.status_code == 401
    dispatcher.dispatch.assert_not_awaited()

    sig = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    good = client.post(
        f"/webhooks/{token}",
        content=body,
        headers={
            "X-API-Key": "key-a",
            "Content-Type": "application/json",
            "X-Signature": f"sha256={sig}",
        },
    )
    assert good.status_code == 202
    dispatcher.dispatch.assert_awaited_once()
