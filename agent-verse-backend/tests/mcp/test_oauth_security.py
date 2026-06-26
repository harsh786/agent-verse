"""Tests that OAuth security fixes work correctly."""
from __future__ import annotations

import pytest
import respx
import httpx

from app.mcp.oauth import OAuthFlowManager, OAuthToken
from app.tenancy.context import TenantContext, PlanTier

T = TenantContext(tenant_id="oauth-sec-t1", plan=PlanTier.PROFESSIONAL, api_key_id="os1")


async def test_exchange_code_returns_none_on_http_error():
    """HTTP 400/401 from OAuth server returns None, not a mock token."""
    manager = OAuthFlowManager()
    params = manager.start_flow(server_id="srv1", tenant_ctx=T)
    state = params["state"]

    with respx.mock:
        respx.post("https://auth.example.com/token").mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        token = await manager.exchange_code(
            code="bad_code", state=state,
            token_url="https://auth.example.com/token",
            client_id="client123", redirect_uri="http://localhost/cb",
            tenant_ctx=T,
        )
    assert token is None  # Must be None, NOT a mock token


async def test_exchange_code_returns_none_on_connection_error():
    """Unreachable OAuth server returns None."""
    manager = OAuthFlowManager()
    params = manager.start_flow(server_id="srv2", tenant_ctx=T)
    state = params["state"]

    with respx.mock:
        respx.post("https://auth.example.com/token").mock(
            side_effect=httpx.ConnectError("refused")
        )
        token = await manager.exchange_code(
            code="code", state=state,
            token_url="https://auth.example.com/token",
            client_id="cid", redirect_uri="http://localhost/cb",
            tenant_ctx=T,
        )
    assert token is None


async def test_exchange_code_returns_none_on_empty_access_token():
    """OAuth server returns 200 but no access_token — should return None."""
    manager = OAuthFlowManager()
    params = manager.start_flow(server_id="srv3", tenant_ctx=T)
    state = params["state"]

    with respx.mock:
        respx.post("https://auth.example.com/token").mock(
            return_value=httpx.Response(200, json={"token_type": "Bearer"})
        )
        token = await manager.exchange_code(
            code="code", state=state,
            token_url="https://auth.example.com/token",
            client_id="cid", redirect_uri="http://localhost/cb",
            tenant_ctx=T,
        )
    assert token is None


async def test_exchange_code_returns_none_on_invalid_state():
    """Expired or forged state parameter returns None."""
    manager = OAuthFlowManager()
    token = await manager.exchange_code(
        code="code", state="invalid-state-xyz",
        token_url="https://auth.example.com/token",
        client_id="cid", redirect_uri="http://localhost/cb",
        tenant_ctx=T,
    )
    assert token is None


async def test_exchange_code_success_stores_token():
    """Successful exchange stores the real token."""
    manager = OAuthFlowManager()
    params = manager.start_flow(server_id="srv4", tenant_ctx=T)
    state = params["state"]

    with respx.mock:
        respx.post("https://auth.example.com/token").mock(
            return_value=httpx.Response(200, json={
                "access_token": "real_access_token_123",
                "token_type": "Bearer",
                "refresh_token": "real_refresh_456",
                "expires_in": 3600,
                "scope": "read write",
            })
        )
        token = await manager.exchange_code(
            code="real_code", state=state,
            token_url="https://auth.example.com/token",
            client_id="cid", redirect_uri="http://localhost/cb",
            tenant_ctx=T,
        )

    assert token is not None
    assert token.access_token == "real_access_token_123"
    assert token.refresh_token == "real_refresh_456"

    # Token should be stored
    stored = manager.get_token(server_id="srv4", tenant_ctx=T)
    assert stored is not None
    assert stored.access_token == "real_access_token_123"
