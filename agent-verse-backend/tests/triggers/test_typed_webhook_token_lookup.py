"""Typed webhooks are deliverable by the third party: tenant comes from the token.

Regression: ``POST /triggers/webhooks/{type}/{token}`` required an AgentVerse API
key (``request.state.tenant``) — which GitHub / Stripe / Jira / PagerDuty can
never send — and the route was not even in TenantMiddleware's bypass list, so
genuine deliveries always got 401. The tenant is now resolved from the webhook
token itself (constant-time compare; DB lookup via the maintenance session,
because the caller has no tenant yet), and everything after that runs as the
resolved tenant. The signature check per trigger is unchanged.

Because the token is now a pre-auth credential it must be unguessable: a short
token never authenticates, and push-webhook triggers created without one get a
server-generated 256-bit token.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.triggers import router as triggers_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore

TOKEN_A = "tokA_" + "a" * 40
TOKEN_B = "tokB_" + "b" * 40


class _Dispatcher:
    def __init__(self) -> None:
        self.fired: list[tuple[str, str]] = []

    async def dispatch(
        self, spec: Any, payload: dict[str, Any], tenant_ctx: Any, **kw: Any
    ) -> None:
        self.fired.append((tenant_ctx.tenant_id, spec.webhook_token))


def _ctx(tid: str) -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.FREE, api_key_id="k")


def _store_with(*triggers: tuple[str, str, str, str]) -> ScheduleStore:
    """(tenant, trigger_type, token, secret) → a real in-memory ScheduleStore."""
    store = ScheduleStore()
    for tenant, ttype, token, secret in triggers:
        spec = TriggerSpec(
            trigger_type=TriggerType(ttype), webhook_token=token, webhook_signature_secret=secret
        )
        store.create(goal_id="", spec=spec, tenant_ctx=_ctx(tenant), goal_template="run")
    return store


async def _reject_all(_key: str) -> TenantContext | None:
    return None


def _client(store: Any, dispatcher: _Dispatcher, *, real_middleware: bool = True) -> TestClient:
    app = FastAPI()
    if real_middleware:
        # The production middleware — proves the route is reachable without a key.
        app.add_middleware(TenantMiddleware, key_resolver=_reject_all)
    app.include_router(triggers_router)
    app.state.schedule_store = store
    app.state.trigger_dispatcher = dispatcher
    return TestClient(app, raise_server_exceptions=False)


def test_third_party_delivery_without_api_key_fires_token_owner() -> None:
    store = _store_with(("t1", "webhook", TOKEN_A, ""), ("t2", "webhook", TOKEN_B, ""))
    disp = _Dispatcher()
    r = _client(store, disp).post(f"/triggers/webhooks/custom/{TOKEN_B}", json={"x": 1})
    assert r.status_code == 200, r.text
    assert disp.fired == [("t2", TOKEN_B)]


def test_signature_still_required_for_signed_trigger() -> None:
    secret = "s3cret"
    store = _store_with(("t1", "github_webhook", TOKEN_A, secret))
    disp = _Dispatcher()
    client = _client(store, disp)
    body = json.dumps({"ref": "main"}).encode()
    assert client.post(f"/triggers/webhooks/github/{TOKEN_A}", content=body).status_code == 401
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    ok = client.post(
        f"/triggers/webhooks/github/{TOKEN_A}",
        content=body,
        headers={"x-hub-signature-256": sig},
    )
    assert ok.status_code == 200, ok.text
    assert disp.fired == [("t1", TOKEN_A)]


def test_unknown_token_is_404_and_short_token_never_authenticates() -> None:
    store = _store_with(("t1", "webhook", "short", ""))
    disp = _Dispatcher()
    client = _client(store, disp)
    assert client.post("/triggers/webhooks/custom/" + "z" * 45, json={}).status_code == 404
    assert client.post("/triggers/webhooks/custom/short", json={}).status_code == 404
    assert disp.fired == []


def test_api_key_tenant_and_header_do_not_override_token_tenant() -> None:
    store = _store_with(("victim", "webhook", TOKEN_A, ""))
    disp = _Dispatcher()
    app = FastAPI()

    @app.middleware("http")
    async def _auth(request: Request, call_next: Any) -> Any:
        request.state.tenant = SimpleNamespace(tenant_id="attacker", plan="free")
        return await call_next(request)

    app.include_router(triggers_router)
    app.state.schedule_store = store
    app.state.trigger_dispatcher = disp
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(
        "/triggers/webhooks/custom/" + "q" * 45, json={}, headers={"X-Tenant-ID": "victim"}
    )
    assert r.status_code == 404
    assert disp.fired == []
    # With the real token it fires as the token's owner, never as the caller.
    assert client.post(f"/triggers/webhooks/custom/{TOKEN_A}", json={}).status_code == 200
    assert disp.fired == [("victim", TOKEN_A)]


def test_token_registered_by_two_tenants_fires_nothing() -> None:
    store = _store_with(("t1", "webhook", TOKEN_A, ""), ("t2", "webhook", TOKEN_A, ""))
    disp = _Dispatcher()
    r = _client(store, disp).post(f"/triggers/webhooks/custom/{TOKEN_A}", json={})
    assert r.status_code == 404
    assert disp.fired == []


# ── DB lookup (other replica / after restart) ───────────────────────────


def _fake_system_db(row: tuple[str, str] | None) -> tuple[Any, list[str]]:
    executed: list[str] = []
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    async def _execute(stmt: Any, params: Any = None) -> Any:
        executed.append(str(stmt))
        result = MagicMock()
        result.fetchone = MagicMock(return_value=row)
        return result

    session.execute = AsyncMock(side_effect=_execute)
    return (lambda: session), executed


@pytest.mark.asyncio
async def test_db_lookup_uses_system_session_and_constant_time_confirm() -> None:
    store = ScheduleStore()
    db, executed = _fake_system_db(("tenant-db", TOKEN_A))
    assert await store.find_tenant_by_webhook_token(TOKEN_A, system_db=db) == "tenant-db"
    assert any("row_security = off" in s for s in executed), executed


@pytest.mark.asyncio
async def test_db_lookup_rejects_row_whose_token_does_not_match() -> None:
    store = ScheduleStore()
    db, _ = _fake_system_db(("tenant-db", TOKEN_B))
    assert await store.find_tenant_by_webhook_token(TOKEN_A, system_db=db) is None


# ── Token issuance on create ────────────────────────────────────────────


def _crud_client(store: ScheduleStore) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _auth(request: Request, call_next: Any) -> Any:
        request.state.tenant = _ctx("t1")
        return await call_next(request)

    app.include_router(triggers_router)
    app.state.schedule_store = store
    return TestClient(app, raise_server_exceptions=False)


def test_webhook_trigger_without_token_gets_strong_generated_token() -> None:
    store = ScheduleStore()
    r = _crud_client(store).post(
        "/triggers", json={"spec": {"trigger_type": "webhook"}, "goal_template": "run"}
    )
    assert r.status_code == 201, r.text
    token = r.json()["spec"]["webhook_token"]
    assert len(token) >= 32


def test_webhook_trigger_with_weak_token_is_rejected() -> None:
    r = _crud_client(ScheduleStore()).post(
        "/triggers",
        json={"spec": {"trigger_type": "webhook", "webhook_token": "abc"}, "goal_template": "x"},
    )
    assert r.status_code == 422


def test_previous_secret_verifies_only_during_rotation_grace() -> None:
    """rotate-secret promises a dual-secret grace period; only the new secret
    used to be checked, breaking in-flight deliveries signed with the old one."""
    import time

    store = _store_with(("t1", "github_webhook", TOKEN_A, "new-secret"))
    (rec,) = store._data.values()
    rec["previous_webhook_secret"] = "old-secret"
    rec["secret_grace_until"] = time.time() + 300
    client = _client(store, _Dispatcher())
    body = json.dumps({"ref": "main"}).encode()

    def _post(secret: str) -> int:
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return client.post(
            f"/triggers/webhooks/github/{TOKEN_A}",
            content=body,
            headers={"x-hub-signature-256": sig},
        ).status_code

    assert _post("old-secret") == 200
    assert _post("new-secret") == 200
    assert _post("wrong") == 401
    rec["secret_grace_until"] = time.time() - 1  # grace over
    assert _post("old-secret") == 401
