"""Comprehensive OAuth PKCE flow tests — covers all paths in app/mcp/oauth.py."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from app.mcp.oauth import OAuthFlowManager, OAuthState, OAuthToken, _OAUTH_STATE_TTL
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="oauth-comp-t1", plan=PlanTier.ENTERPRISE, api_key_id="key1")
TENANT2 = TenantContext(tenant_id="oauth-comp-t2", plan=PlanTier.FREE, api_key_id="key2")


# ── OAuthState ────────────────────────────────────────────────────────────────


def test_oauth_state_code_challenge_is_s256() -> None:
    """code_challenge must be URL-safe base64 of SHA-256(code_verifier)."""
    import base64
    import hashlib

    state = OAuthState(server_id="srv-x")
    digest = hashlib.sha256(state.code_verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert state.code_challenge == expected


def test_oauth_state_unique_verifiers_per_instance() -> None:
    s1 = OAuthState(server_id="s")
    s2 = OAuthState(server_id="s")
    assert s1.code_verifier != s2.code_verifier
    assert s1.code_challenge != s2.code_challenge


def test_oauth_state_alias_fields() -> None:
    state = OAuthState(
        server_id="s",
        state="my-state",
        tenant_id="t1",
        redirect_uri="http://localhost/cb",
        pkce_verifier="verifier-xyz",
    )
    assert state.state == "my-state"
    assert state.tenant_id == "t1"
    assert state.pkce_verifier == "verifier-xyz"


# ── OAuthToken ────────────────────────────────────────────────────────────────


def test_oauth_token_not_expired_fresh() -> None:
    tok = OAuthToken(access_token="abc", expires_in=3600)
    assert tok.is_expired() is False


def test_oauth_token_expired_old() -> None:
    tok = OAuthToken(access_token="abc", expires_in=60, obtained_at=time.time() - 200)
    assert tok.is_expired() is True


def test_oauth_token_expiry_boundary_60s_buffer() -> None:
    """Token is considered expired 60 seconds before actual expiry."""
    tok = OAuthToken(access_token="abc", expires_in=3600, obtained_at=time.time() - 3545)
    assert tok.is_expired() is True


def test_oauth_token_defaults() -> None:
    tok = OAuthToken(access_token="tok")
    assert tok.token_type == "Bearer"
    assert tok.refresh_token == ""
    assert tok.expires_in == 3600
    assert tok.scope == ""


# ── OAuthFlowManager.start_flow ───────────────────────────────────────────────


def test_start_flow_returns_all_required_keys() -> None:
    mgr = OAuthFlowManager()
    params = mgr.start_flow(server_id="my-srv", tenant_ctx=TENANT)
    assert set(params.keys()) == {"state", "code_challenge", "code_challenge_method", "server_id"}
    assert params["code_challenge_method"] == "S256"
    assert params["server_id"] == "my-srv"


def test_start_flow_pending_state_is_stored() -> None:
    mgr = OAuthFlowManager()
    params = mgr.start_flow(server_id="srv-a", tenant_ctx=TENANT)
    assert params["state"] in mgr._pending_flows


def test_start_flow_multiple_flows_independent() -> None:
    mgr = OAuthFlowManager()
    p1 = mgr.start_flow(server_id="srv1", tenant_ctx=TENANT)
    p2 = mgr.start_flow(server_id="srv2", tenant_ctx=TENANT)
    assert p1["state"] != p2["state"]
    assert len(mgr._pending_flows) == 2


# ── OAuthFlowManager.get_pending_flow ─────────────────────────────────────────


def test_get_pending_flow_returns_valid_state() -> None:
    mgr = OAuthFlowManager()
    params = mgr.start_flow(server_id="s", tenant_ctx=TENANT)
    flow = mgr.get_pending_flow(params["state"])
    assert flow is not None
    assert flow.server_id == "s"


def test_get_pending_flow_missing_returns_none() -> None:
    mgr = OAuthFlowManager()
    assert mgr.get_pending_flow("totally-random-state") is None


def test_get_pending_flow_expired_state_returns_none() -> None:
    mgr = OAuthFlowManager()
    params = mgr.start_flow(server_id="s", tenant_ctx=TENANT)
    state_token = params["state"]
    # Force expiry by manipulating created_at
    mgr._pending_flows[state_token].created_at = time.time() - _OAUTH_STATE_TTL - 1
    result = mgr.get_pending_flow(state_token)
    assert result is None
    assert state_token not in mgr._pending_flows  # cleaned up


def test_cleanup_expired_flows_removes_old_entries() -> None:
    mgr = OAuthFlowManager()
    mgr.start_flow(server_id="s1", tenant_ctx=TENANT)
    mgr.start_flow(server_id="s2", tenant_ctx=TENANT)
    # Force all flows to be expired
    for v in mgr._pending_flows.values():
        v.created_at = time.time() - _OAUTH_STATE_TTL - 10
    mgr._cleanup_expired_flows()
    assert len(mgr._pending_flows) == 0


# ── OAuthFlowManager.exchange_code ────────────────────────────────────────────


async def test_exchange_code_success_stores_token() -> None:
    mgr = OAuthFlowManager()
    params = mgr.start_flow(server_id="srv-store", tenant_ctx=TENANT)

    with respx.mock:
        respx.post("http://auth.test/token").mock(
            return_value=httpx.Response(200, json={
                "access_token": "tok-123",
                "token_type": "Bearer",
                "expires_in": 7200,
                "refresh_token": "ref-abc",
                "scope": "read write",
            })
        )
        token = await mgr.exchange_code(
            code="code-x",
            state=params["state"],
            token_url="http://auth.test/token",
            client_id="cid",
            redirect_uri="http://localhost/cb",
            tenant_ctx=TENANT,
        )

    assert token is not None
    assert token.access_token == "tok-123"
    assert token.refresh_token == "ref-abc"
    assert token.expires_in == 7200
    assert token.scope == "read write"
    # State consumed
    assert params["state"] not in mgr._pending_flows
    # Stored in internal map
    assert mgr.get_token(server_id="srv-store", tenant_ctx=TENANT) is not None


async def test_exchange_code_http_error_returns_none() -> None:
    mgr = OAuthFlowManager()
    params = mgr.start_flow(server_id="srv-err", tenant_ctx=TENANT)

    with respx.mock:
        respx.post("http://auth.test/token").mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        token = await mgr.exchange_code(
            code="bad-code",
            state=params["state"],
            token_url="http://auth.test/token",
            client_id="cid",
            redirect_uri="http://localhost/cb",
            tenant_ctx=TENANT,
        )
    assert token is None


async def test_exchange_code_connect_error_returns_none() -> None:
    mgr = OAuthFlowManager()
    params = mgr.start_flow(server_id="srv-conn", tenant_ctx=TENANT)

    with respx.mock:
        respx.post("http://auth.test/token").mock(side_effect=httpx.ConnectError("refused"))
        token = await mgr.exchange_code(
            code="code-y",
            state=params["state"],
            token_url="http://auth.test/token",
            client_id="cid",
            redirect_uri="http://localhost/cb",
            tenant_ctx=TENANT,
        )
    assert token is None


async def test_exchange_code_timeout_returns_none() -> None:
    mgr = OAuthFlowManager()
    params = mgr.start_flow(server_id="srv-to", tenant_ctx=TENANT)

    with respx.mock:
        respx.post("http://auth.test/token").mock(
            side_effect=httpx.TimeoutException("timeout")
        )
        token = await mgr.exchange_code(
            code="code-z",
            state=params["state"],
            token_url="http://auth.test/token",
            client_id="cid",
            redirect_uri="http://localhost/cb",
            tenant_ctx=TENANT,
        )
    assert token is None


async def test_exchange_code_empty_access_token_returns_none() -> None:
    mgr = OAuthFlowManager()
    params = mgr.start_flow(server_id="srv-empty", tenant_ctx=TENANT)

    with respx.mock:
        respx.post("http://auth.test/token").mock(
            return_value=httpx.Response(200, json={"access_token": "", "token_type": "Bearer"})
        )
        token = await mgr.exchange_code(
            code="code-e",
            state=params["state"],
            token_url="http://auth.test/token",
            client_id="cid",
            redirect_uri="http://localhost/cb",
            tenant_ctx=TENANT,
        )
    assert token is None


async def test_exchange_code_invalid_state_returns_none() -> None:
    mgr = OAuthFlowManager()
    token = await mgr.exchange_code(
        code="code",
        state="bad-state-xyz",
        token_url="http://auth.test/token",
        client_id="cid",
        redirect_uri="http://localhost/cb",
        tenant_ctx=TENANT,
    )
    assert token is None


# ── OAuthFlowManager.get_token ────────────────────────────────────────────────


def test_get_token_keyword_style() -> None:
    mgr = OAuthFlowManager()
    mgr._tokens[("oauth-comp-t1", "srv1")] = OAuthToken(access_token="kw-tok")
    tok = mgr.get_token(server_id="srv1", tenant_ctx=TENANT)
    assert tok is not None
    assert tok.access_token == "kw-tok"


def test_get_token_positional_style() -> None:
    mgr = OAuthFlowManager()
    mgr._tokens[("oauth-comp-t1", "srv2")] = OAuthToken(access_token="pos-tok")
    tok = mgr.get_token("oauth-comp-t1", "srv2")
    assert tok is not None
    assert tok.access_token == "pos-tok"


def test_get_token_missing_returns_none() -> None:
    mgr = OAuthFlowManager()
    assert mgr.get_token(server_id="nonexistent", tenant_ctx=TENANT) is None


def test_get_token_wrong_tenant_returns_none() -> None:
    mgr = OAuthFlowManager()
    mgr._tokens[("oauth-comp-t1", "srv1")] = OAuthToken(access_token="tok")
    assert mgr.get_token(server_id="srv1", tenant_ctx=TENANT2) is None


# ── OAuthFlowManager.refresh_token ────────────────────────────────────────────


async def test_refresh_token_success() -> None:
    mgr = OAuthFlowManager()
    mgr._tokens[("oauth-comp-t1", "srv-r")] = OAuthToken(
        access_token="old", refresh_token="ref-123", expires_in=60
    )

    with respx.mock:
        respx.post("http://auth.test/token").mock(
            return_value=httpx.Response(200, json={
                "access_token": "new-tok",
                "token_type": "Bearer",
                "expires_in": 3600,
            })
        )
        new_tok = await mgr.refresh_token(
            server_id="srv-r",
            token_url="http://auth.test/token",
            client_id="cid",
            tenant_ctx=TENANT,
        )

    assert new_tok is not None
    assert new_tok.access_token == "new-tok"


async def test_refresh_token_no_refresh_token_returns_none() -> None:
    mgr = OAuthFlowManager()
    mgr._tokens[("oauth-comp-t1", "srv-nr")] = OAuthToken(
        access_token="tok", refresh_token=""
    )
    result = await mgr.refresh_token(
        server_id="srv-nr", token_url="http://auth.test/token", tenant_ctx=TENANT
    )
    assert result is None


async def test_refresh_token_no_token_url_returns_none() -> None:
    mgr = OAuthFlowManager()
    mgr._tokens[("oauth-comp-t1", "srv-nu")] = OAuthToken(
        access_token="tok", refresh_token="ref"
    )
    result = await mgr.refresh_token(server_id="srv-nu", tenant_ctx=TENANT)
    assert result is None


async def test_refresh_token_no_existing_token_returns_none() -> None:
    mgr = OAuthFlowManager()
    result = await mgr.refresh_token(
        server_id="nonexistent",
        token_url="http://auth.test/token",
        tenant_ctx=TENANT,
    )
    assert result is None


async def test_refresh_token_from_auth_config() -> None:
    mgr = OAuthFlowManager()
    existing = OAuthToken(access_token="old", refresh_token="ref-99")
    with respx.mock:
        respx.post("http://auth.test/token").mock(
            return_value=httpx.Response(200, json={"access_token": "refreshed"})
        )
        new_tok = await mgr.refresh_token(
            server_id="srv-ac",
            tenant_id="oauth-comp-t1",
            token=existing,
            auth_config={"token_url": "http://auth.test/token", "client_id": "cid"},
        )
    assert new_tok is not None
    assert new_tok.access_token == "refreshed"


async def test_refresh_token_http_error_returns_none() -> None:
    mgr = OAuthFlowManager()
    mgr._tokens[("oauth-comp-t1", "srv-re")] = OAuthToken(
        access_token="tok", refresh_token="ref"
    )
    with respx.mock:
        respx.post("http://auth.test/token").mock(
            return_value=httpx.Response(401, json={"error": "invalid_token"})
        )
        result = await mgr.refresh_token(
            server_id="srv-re",
            token_url="http://auth.test/token",
            tenant_ctx=TENANT,
        )
    assert result is None


# ── OAuthFlowManager._persist_token_to_db ────────────────────────────────────


async def test_persist_token_no_factory_is_noop() -> None:
    """Should complete silently when no DB factory is set."""
    mgr = OAuthFlowManager()
    tok = OAuthToken(access_token="tok")
    # Should not raise
    await mgr._persist_token_to_db("t1", "srv", tok)


# ── OAuthFlowManager.load_tokens_from_db ──────────────────────────────────────


async def test_load_tokens_no_factory_returns_zero() -> None:
    mgr = OAuthFlowManager()
    count = await mgr.load_tokens_from_db()
    assert count == 0
