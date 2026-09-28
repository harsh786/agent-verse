"""Regression: the voice WebSocket must not take the API key from the URL.

Old bugs (``WS /v1/voice/stream/{org_id}``):
* the key was read from ``?api_key=`` — so it landed in reverse-proxy / load
  balancer access logs and browser history;
* with no ``_tenant_key_resolver`` wired, ``_ws_auth`` returned the raw key AS
  the tenant id ("dev fallback"), so any string authenticated as a tenant.

Now the key is accepted only from a header or the ``av.v1.<b64url(key)>``
``Sec-WebSocket-Protocol`` entry (``app.tenancy.ws_auth.resolve_ws_tenant``),
and the selected subprotocol is echoed so browsers complete the handshake.
"""

from __future__ import annotations

import base64
import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.voice.router import router as voice_router

voice_router_module = importlib.import_module("app.voice.router")

KEY = "av_voice_ws_test_key"  # test fixture, not a real credential


def _protocol(key: str) -> str:
    return "av.v1." + base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")


def _app(resolver: Any | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(voice_router)

    async def _resolve(key: str) -> Any:
        return SimpleNamespace(tenant_id="tenant-ws") if key == KEY else None

    app.state._tenant_key_resolver = resolver or _resolve
    return app


def _session_mock() -> MagicMock:
    sess = MagicMock()
    sess.run = AsyncMock()
    return sess


def test_query_string_api_key_is_rejected() -> None:
    client = TestClient(_app())
    with (
        patch("app.voice.router.VoiceStreamingSession", return_value=_session_mock()) as cls,
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect(f"/v1/voice/stream/org-1?api_key={KEY}") as ws,
    ):
        ws.receive_text()
    assert exc.value.code == 4001
    cls.assert_not_called()


def test_subprotocol_key_authenticates_and_is_echoed() -> None:
    client = TestClient(_app())
    proto = _protocol(KEY)
    sess = _session_mock()
    with (
        patch("app.voice.router._get_persona", AsyncMock(return_value=(None, None))),
        patch("app.voice.router.VoiceStreamingSession", return_value=sess) as cls,
        client.websocket_connect("/v1/voice/stream/org-1", subprotocols=[proto]) as ws,
    ):
        assert ws.accepted_subprotocol == proto
    _, kwargs = cls.call_args
    assert kwargs["tenant_id"] == "tenant-ws"


def test_header_key_authenticates() -> None:
    client = TestClient(_app())
    with (
        patch("app.voice.router._get_persona", AsyncMock(return_value=(None, None))),
        patch("app.voice.router.VoiceStreamingSession", return_value=_session_mock()) as cls,
        client.websocket_connect("/v1/voice/stream/org-1", headers={"X-API-Key": KEY}),
    ):
        pass
    _, kwargs = cls.call_args
    assert kwargs["tenant_id"] == "tenant-ws"


async def test_ws_auth_no_resolver_does_not_treat_key_as_tenant() -> None:
    ws = MagicMock()
    ws.headers = {"x-api-key": "anything"}
    ws.app.state = SimpleNamespace()  # no resolver, no tenant_service
    assert await voice_router_module._ws_auth(ws) is None
