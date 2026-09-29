"""POST /rpa/sessions/{id}/takeover must really reach a human, or say it cannot.

Regression: the endpoint only set a Redis key (``rpa_human_needed:{id}``) that
nothing ever read — and skipped even that without Redis — yet always answered
"Human operator has been notified", with a ``live_url`` pointing at a route the
frontend does not have. It now raises a tenant-scoped HITL approval (listed in
the Approvals inbox; the gateway alerts the tenant's notification channels),
returns 503 when that cannot be delivered, and only reports what happened.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.rpa import router as rpa_router
from app.governance.hitl import HITLGateway
from app.rpa.session import RPASessionStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-takeover", plan=PlanTier.PROFESSIONAL, api_key_id="kid-t")
_KEY = "av_test_rpa_takeover"


def _app(*, hitl: Any = None) -> tuple[FastAPI, RPASessionStore]:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(rpa_router)
    store = RPASessionStore()
    app.state.rpa_session_store = store
    if hitl is not None:
        app.state.hitl_gateway = hitl
    app.state.settings = MagicMock(frontend_url="https://app.example")
    return app, store


def _post(app: FastAPI, session_id: str, reason: str = "captcha wall") -> Any:
    client = TestClient(app, raise_server_exceptions=False)
    return client.post(
        f"/rpa/sessions/{session_id}/takeover",
        json={"reason": reason},
        headers={"X-API-Key": _KEY},
    )


def _new_session(app: FastAPI, store: RPASessionStore) -> str:
    import asyncio

    return asyncio.run(store.create(tenant_id=_CTX.tenant_id)).session_id


def test_takeover_without_a_hitl_channel_is_503_not_a_fake_notification() -> None:
    app, store = _app()
    sid = _new_session(app, store)

    resp = _post(app, sid)

    assert resp.status_code == 503
    assert "notified" not in resp.text.lower()


def test_takeover_raises_a_tenant_scoped_hitl_approval() -> None:
    gateway = HITLGateway()
    app, store = _app(hitl=gateway)
    sid = _new_session(app, store)

    resp = _post(app, sid, reason="captcha wall")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "awaiting_human"
    req_id = body["approval_request_id"]
    pending = gateway.list_pending(tenant_ctx=_CTX)
    assert [str(r.request_id) for r in pending] == [req_id]
    req = pending[0]
    assert sid in req.goal_id
    assert "captcha wall" in req.action
    other = TenantContext(tenant_id="someone-else", plan=PlanTier.PROFESSIONAL, api_key_id="x")
    assert gateway.list_pending(tenant_ctx=other) == []
    # Truthful: no claim that a person was notified; points at the inbox.
    assert "has been notified" not in body["message"]
    assert req_id in body["message"]
    assert body["approvals_url"] == "https://app.example/approvals"


def test_takeover_live_url_targets_an_existing_frontend_route() -> None:
    gateway = HITLGateway()
    app, store = _app(hitl=gateway)
    sid = _new_session(app, store)

    body = _post(app, sid).json()

    assert body["live_url"] == "https://app.example/rpa/live"
    app_tsx = (
        pathlib.Path(__file__).resolve().parents[3]
        / "agent-verse-frontend"
        / "src"
        / "app"
        / "App.tsx"
    )
    if not app_tsx.exists():
        pytest.skip("frontend sources not present")
    routes = set(re.findall(r'path="([^"]+)"', app_tsx.read_text()))
    for url in (body["live_url"], body["approvals_url"]):
        assert url.removeprefix("https://app.example/") in routes, url


def test_takeover_reports_notification_channels_only_when_there_are_some() -> None:
    gateway = HITLGateway()
    notifier = MagicMock()
    notifier.get_channels = MagicMock(return_value=[object(), object()])
    notifier.notify_approval_required = AsyncMock()
    gateway._notification_service = notifier
    app, store = _app(hitl=gateway)
    sid = _new_session(app, store)

    body = _post(app, sid).json()

    notifier.get_channels.assert_called_with(_CTX.tenant_id)
    assert "2 notification channel" in body["message"]
    assert body["notification_channels"] == 2


def test_takeover_is_503_when_the_approval_cannot_be_persisted() -> None:
    gateway = HITLGateway(db_session_factory=MagicMock(side_effect=RuntimeError("db down")))
    app, store = _app(hitl=gateway)
    sid = _new_session(app, store)

    resp = _post(app, sid)

    assert resp.status_code == 503
    # No half-created approval left behind on this replica.
    assert gateway.list_pending(tenant_ctx=_CTX) == []


def test_takeover_unknown_session_is_404() -> None:
    app, _store = _app(hitl=HITLGateway())
    assert _post(app, "nope").status_code == 404
