"""Regression: the coordination group-chat socket uses the shared WebSocket authenticator.

``app.api.coordination_group_chat`` used to authenticate with its own helper that
only resolved the API key. HTTP middleware never runs for WebSocket scopes, so
that helper skipped every per-tenant policy ``TenantMiddleware`` /
``ScopeEnforcementMiddleware`` apply over HTTP: the tenant IP allowlist, an API
key's explicit scopes / roles, and MFA. It must delegate to
``app.tenancy.ws_auth.resolve_ws_tenant`` and refuse like the other sockets
(close code 4401), while still applying its own origin and session checks.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.coordination_group_chat import router
from app.auth import scope_enforcement
from app.auth.ip_allowlist import IPAllowlistCache, IPAllowlistUnavailableError
from app.coordination.transcript.repository import InMemoryTranscriptRepository
from app.coordination.transcript.service import TranscriptService
from app.tenancy.context import PlanTier, TenantContext

_PATH = "/api/v1/coordination/sessions/session/group-chat/ws"
_ORIGIN = "https://app.example"

_KEYS = {
    "k-admin": TenantContext(
        tenant_id="tenant", plan=PlanTier.PROFESSIONAL, api_key_id="a", roles=("admin",)
    ),
    "k-operator": TenantContext(
        tenant_id="tenant", plan=PlanTier.PROFESSIONAL, api_key_id="o", roles=("operator",)
    ),
    "k-viewer": TenantContext(
        tenant_id="tenant", plan=PlanTier.PROFESSIONAL, api_key_id="v", roles=("viewer",)
    ),
    # A key minted with explicit scopes that do not cover this (unregistered,
    # write-capable) socket.
    "k-scoped": TenantContext(
        tenant_id="tenant",
        plan=PlanTier.PROFESSIONAL,
        api_key_id="s",
        roles=("operator",),
        scopes=("goals:read",),
    ),
}


async def _authorized(tenant_id: str, session_id: str) -> bool:
    return tenant_id == "tenant" and session_id == "session"


def _client() -> TestClient:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return _KEYS.get(key)

    app.include_router(router)
    app.state._tenant_key_resolver = resolve
    app.state.transcript_service = TranscriptService(InMemoryTranscriptRepository())
    app.state.settings = SimpleNamespace(cors_origins=(_ORIGIN,))
    app.state.coordination_session_authorizer = _authorized
    return TestClient(app, raise_server_exceptions=False)


def _close_code(client: TestClient, key: str, path: str = _PATH, **headers: str) -> int | None:
    """Return the close code if the socket is refused, or None if it connects."""
    try:
        with client.websocket_connect(
            path, headers={"X-API-Key": key, "Origin": _ORIGIN, **headers}
        ) as socket:
            assert socket.receive_json()["type"] == "replay_complete"
            socket.send_json({"type": "close"})
        return None
    except WebSocketDisconnect as exc:
        return exc.code


@pytest.fixture(autouse=True)
def _clear_ip_cache() -> Any:
    scope_enforcement._local_ip_allowlist_cache.clear()
    yield
    scope_enforcement._local_ip_allowlist_cache.clear()


@pytest.mark.parametrize("key", ["k-admin", "k-operator"])
def test_valid_write_capable_key_still_connects(key: str) -> None:
    assert _close_code(_client(), key) is None


def test_invalid_key_is_refused_with_shared_close_code() -> None:
    assert _close_code(_client(), "nope") == 4401


def test_ip_allowlist_excluding_client_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(IPAllowlistCache, "get_cidrs", AsyncMock(return_value=["203.0.113.0/24"]))
    assert _close_code(_client(), "k-admin") == 4401


def test_ip_allowlist_store_outage_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        IPAllowlistCache, "get_cidrs", AsyncMock(side_effect=IPAllowlistUnavailableError("x"))
    )
    assert _close_code(_client(), "k-admin") == 4401


def test_key_with_explicit_scopes_not_covering_socket_is_refused() -> None:
    assert _close_code(_client(), "k-scoped") == 4401


def test_viewer_key_cannot_use_write_socket() -> None:
    assert _close_code(_client(), "k-viewer") == 4401


def test_mfa_required_principal_without_mfa_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.mfa import _mfa_db_store
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "mfa_enforcement_enabled", True)
    monkeypatch.setattr(_mfa_db_store, "get", AsyncMock(return_value={"enabled": True}))
    assert _close_code(_client(), "k-admin") == 4401


def test_api_key_in_query_string_is_not_accepted() -> None:
    client = _client()
    try:
        with client.websocket_connect(f"{_PATH}?api_key=k-admin", headers={"Origin": _ORIGIN}):
            pass
        code = None
    except WebSocketDisconnect as exc:
        code = exc.code
    assert code == 4401


def test_session_authorization_still_applies_after_authentication() -> None:
    other = "/api/v1/coordination/sessions/other/group-chat/ws"
    assert _close_code(_client(), "k-admin", path=other) == 1008


def test_origin_check_still_applies_after_authentication() -> None:
    assert _close_code(_client(), "k-admin", Origin="https://evil.example") == 1008


def test_browser_subprotocol_key_connects_and_is_echoed() -> None:
    token = base64.urlsafe_b64encode(b"k-admin").decode().rstrip("=")
    with _client().websocket_connect(
        _PATH, headers={"Origin": _ORIGIN}, subprotocols=[f"av.v1.{token}"]
    ) as socket:
        assert socket.accepted_subprotocol == f"av.v1.{token}"
        assert socket.receive_json()["type"] == "replay_complete"
        socket.send_json({"type": "close"})
