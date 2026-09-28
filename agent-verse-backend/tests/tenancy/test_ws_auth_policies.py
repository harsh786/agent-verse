"""Regression tests: WebSocket auth applies the HTTP pipeline's tenant policies.

1. ``?api_key=`` was accepted on the org MCP and civilization sockets.
2. No scope / role, IP allowlist or MFA enforcement on any WebSocket.
3. ``resolve_ws_tenant`` swallowed resolver exceptions silently.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth import scope_enforcement
from app.auth.ip_allowlist import IPAllowlistCache, IPAllowlistUnavailableError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.ws_auth import resolve_ws_tenant

_KEYS = {
    "k-admin": TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="a", roles=("admin",)),
    "k-viewer": TenantContext(
        tenant_id="t1", plan=PlanTier.FREE, api_key_id="v", roles=("viewer",)
    ),
    "k-scoped": TenantContext(
        tenant_id="t1",
        plan=PlanTier.FREE,
        api_key_id="s",
        roles=("operator",),
        scopes=("goals:read",),
    ),
}


def _client(resolver: Any = None) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _KEYS.get(key)

    app.state._tenant_key_resolver = resolver or _resolve

    @app.websocket("/read")
    async def read_ws(ws: WebSocket) -> None:
        if await resolve_ws_tenant(ws, required_scope="agents:read") is None:
            await ws.close(code=4401)
            return
        await ws.accept()
        await ws.send_text("ok")
        await ws.close()

    @app.websocket("/write")
    async def write_ws(ws: WebSocket) -> None:
        if await resolve_ws_tenant(ws, write=True) is None:
            await ws.close(code=4401)
            return
        await ws.accept()
        await ws.send_text("ok")
        await ws.close()

    return TestClient(app, raise_server_exceptions=False)


def _refused(client: TestClient, path: str, **kw: Any) -> bool:
    try:
        with client.websocket_connect(path, **kw) as ws:
            ws.receive_text()
        return False
    except WebSocketDisconnect as exc:
        return exc.code == 4401


@pytest.fixture(autouse=True)
def _clear() -> Any:
    scope_enforcement._local_ip_allowlist_cache.clear()
    yield
    scope_enforcement._local_ip_allowlist_cache.clear()


def test_query_string_key_is_refused_header_key_works() -> None:
    client = _client()
    assert _refused(client, "/read?api_key=k-admin")
    assert not _refused(client, "/read", headers={"X-API-Key": "k-admin"})


def test_scoped_key_needs_the_scope_and_viewer_cannot_use_write_socket() -> None:
    client = _client()
    assert _refused(client, "/read", headers={"X-API-Key": "k-scoped"})  # lacks agents:read
    assert _refused(client, "/write", headers={"X-API-Key": "k-scoped"})  # unregistered
    assert _refused(client, "/write", headers={"X-API-Key": "k-viewer"})
    assert not _refused(client, "/read", headers={"X-API-Key": "k-viewer"})
    assert not _refused(client, "/write", headers={"X-API-Key": "k-admin"})


def test_ip_allowlist_applies_and_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(IPAllowlistCache, "get_cidrs", AsyncMock(return_value=["203.0.113.0/24"]))
    assert _refused(client, "/read", headers={"X-API-Key": "k-admin"})
    monkeypatch.setattr(
        IPAllowlistCache, "get_cidrs", AsyncMock(side_effect=IPAllowlistUnavailableError("x"))
    )
    assert _refused(client, "/read", headers={"X-API-Key": "k-admin"})


def test_mfa_is_enforced_on_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.mfa import _mfa_db_store
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "mfa_enforcement_enabled", True)
    monkeypatch.setattr(_mfa_db_store, "get", AsyncMock(return_value={"enabled": True}))
    assert _refused(_client(), "/read", headers={"X-API-Key": "k-admin"})


def test_resolver_error_refuses_the_socket() -> None:
    client = _client(resolver=AsyncMock(side_effect=RuntimeError("db down")))
    assert _refused(client, "/read", headers={"X-API-Key": "k-admin"})
