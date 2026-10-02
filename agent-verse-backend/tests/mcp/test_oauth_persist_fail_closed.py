"""OAUTH-01: a token that could not be persisted is never reported "connected".

The oauth_tokens write failure was logged and swallowed; the callback then said
"connected" although the worker and every other replica would never find the
token.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.mcp.oauth import OAuthFlowManager, OAuthTokenPersistError
from app.mcp.registry import MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext
from tests.api.test_connectors_comprehensive2 import _CTX, _VALID_KEY, _make_app, _make_registry

T = TenantContext(tenant_id="oauth-persist-t", plan=PlanTier.FREE, api_key_id="k")


class _BrokenDb:
    async def __aenter__(self) -> Any:
        raise ConnectionError("db down")

    async def __aexit__(self, *a: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_exchange_raises_when_the_durable_write_fails() -> None:
    mgr = OAuthFlowManager()
    mgr._db_session_factory = _BrokenDb
    state = mgr.start_flow(server_id="srv", tenant_ctx=T)["state"]
    with respx.mock:
        respx.post("https://auth.example.com/token").mock(
            return_value=httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        )
        with pytest.raises(OAuthTokenPersistError):
            await mgr.exchange_code(
                code="c",
                state=state,
                token_url="https://auth.example.com/token",
                client_id="cid",
                redirect_uri="https://app/cb",
                tenant_ctx=T,
            )
    # Not served from this process either: nobody else could see it.
    assert mgr._tokens == {}


@pytest.mark.asyncio
async def test_without_a_db_nothing_to_persist_is_not_an_error() -> None:
    mgr = OAuthFlowManager()
    state = mgr.start_flow(server_id="srv", tenant_ctx=T)["state"]
    with respx.mock:
        respx.post("https://auth.example.com/token").mock(
            return_value=httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        )
        token = await mgr.exchange_code(
            code="c",
            state=state,
            token_url="https://auth.example.com/token",
            client_id="cid",
            redirect_uri="https://app/cb",
            tenant_ctx=T,
        )
    assert token is not None and token.access_token == "at"


def test_callback_is_503_when_the_token_could_not_be_persisted() -> None:
    import asyncio

    reg = _make_registry()
    sid = asyncio.run(
        reg.register(
            MCPServerConfig(
                name="oauth thing",
                url="https://api.example.com",
                auth_type="oauth_ac",
                auth_config={"token_url": "https://auth.example.com/token", "client_id": "c"},
            ),
            tenant_ctx=_CTX,
        )
    )
    app = _make_app(reg)
    manager = AsyncMock()
    manager.exchange_code.side_effect = OAuthTokenPersistError("oauth_tokens write failed")
    app.state.oauth_manager = manager
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get(
        "/connectors/oauth/callback",
        params={"code": "c", "state": "s", "server_id": sid},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 503
    assert "connected" not in resp.text
    assert "retry" in resp.text.lower()
