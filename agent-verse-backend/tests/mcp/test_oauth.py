"""Tests for OAuthFlowManager."""
from __future__ import annotations

import pytest
import respx
import httpx

from app.mcp.oauth import OAuthFlowManager, OAuthToken
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(
    tenant_id="oauth-test",
    plan=PlanTier.PROFESSIONAL,
    api_key_id="oauth-key-1",
)


def test_start_flow_returns_pkce_params() -> None:
    manager = OAuthFlowManager()
    params = manager.start_flow(server_id="srv-1", tenant_ctx=TENANT)

    assert "state" in params
    assert "code_challenge" in params
    assert params["code_challenge_method"] == "S256"
    assert params["server_id"] == "srv-1"


def test_start_flow_creates_pending_state() -> None:
    manager = OAuthFlowManager()
    params = manager.start_flow(server_id="srv-1", tenant_ctx=TENANT)

    assert params["state"] in manager._pending_flows


def test_start_flow_each_call_has_unique_state() -> None:
    manager = OAuthFlowManager()
    p1 = manager.start_flow(server_id="srv-1", tenant_ctx=TENANT)
    p2 = manager.start_flow(server_id="srv-1", tenant_ctx=TENANT)

    assert p1["state"] != p2["state"]
    assert p1["code_challenge"] != p2["code_challenge"]


@pytest.mark.asyncio
async def test_exchange_code_cleans_up_state() -> None:
    manager = OAuthFlowManager()
    params = manager.start_flow(server_id="srv-1", tenant_ctx=TENANT)
    state = params["state"]

    with respx.mock:
        respx.post("http://auth.example.com/token").mock(
            return_value=httpx.Response(200, json={
                "access_token": "test_token_123",
                "token_type": "Bearer",
                "expires_in": 3600,
            })
        )
        token = await manager.exchange_code(
            code="auth-code-123",
            state=state,
            token_url="http://auth.example.com/token",
            client_id="client-id",
            redirect_uri="http://localhost/callback",
            tenant_ctx=TENANT,
        )

    assert token is not None
    assert token.access_token == "test_token_123"
    assert state not in manager._pending_flows  # cleaned up


@pytest.mark.asyncio
async def test_exchange_code_invalid_state_returns_none() -> None:
    manager = OAuthFlowManager()

    token = await manager.exchange_code(
        code="any",
        state="invalid-state",
        token_url="http://auth.example.com/token",
        client_id="cid",
        redirect_uri="http://localhost/cb",
        tenant_ctx=TENANT,
    )

    assert token is None


@pytest.mark.asyncio
async def test_exchange_code_stores_token_for_tenant() -> None:
    manager = OAuthFlowManager()
    params = manager.start_flow(server_id="srv-2", tenant_ctx=TENANT)

    with respx.mock:
        respx.post("http://auth.example.com/token").mock(
            return_value=httpx.Response(200, json={
                "access_token": "stored_token_abc",
                "token_type": "Bearer",
                "expires_in": 3600,
            })
        )
        await manager.exchange_code(
            code="code-abc",
            state=params["state"],
            token_url="http://auth.example.com/token",
            client_id="cid",
            redirect_uri="http://localhost/cb",
            tenant_ctx=TENANT,
        )

    token = manager.get_token(server_id="srv-2", tenant_ctx=TENANT)
    assert token is not None
    assert token.access_token == "stored_token_abc"


def test_get_token_after_exchange() -> None:
    manager = OAuthFlowManager()
    # Manually store a token
    manager._tokens[("oauth-test", "srv-1")] = OAuthToken(access_token="tok_123")

    token = manager.get_token(server_id="srv-1", tenant_ctx=TENANT)

    assert token is not None
    assert token.access_token == "tok_123"


def test_get_token_wrong_tenant_returns_none() -> None:
    manager = OAuthFlowManager()
    manager._tokens[("oauth-test", "srv-1")] = OAuthToken(access_token="tok_123")

    other_tenant = TenantContext(
        tenant_id="other-tenant",
        plan=PlanTier.FREE,
        api_key_id="k2",
    )
    token = manager.get_token(server_id="srv-1", tenant_ctx=other_tenant)

    assert token is None


def test_oauth_token_expiry() -> None:
    import time

    token = OAuthToken(
        access_token="tok",
        expires_in=60,
        obtained_at=time.time() - 120,  # obtained 2 minutes ago
    )
    assert token.is_expired() is True


def test_oauth_token_not_expired() -> None:
    token = OAuthToken(access_token="tok", expires_in=3600)
    assert token.is_expired() is False
