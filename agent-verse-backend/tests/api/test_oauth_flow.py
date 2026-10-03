"""Tests for real OAuth callback implementation."""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx as _httpx
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.connectors import router as connectors_router
from app.mcp.oauth import OAuthFlowManager
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="oauth-t1", plan=PlanTier.PROFESSIONAL, api_key_id="ok1")
_VALID_KEY = "av_test_oauthkey"


def _make_app(registry, oauth_mgr=None):
    app = FastAPI()

    async def resolve(k):
        return _CTX if k == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(connectors_router)
    app.state.mcp_registry = registry
    app.state.oauth_manager = oauth_mgr or OAuthFlowManager()
    return app


def test_oauth_callback_with_no_state_returns_pending_or_error():
    """Missing/empty state param returns pending_config (no token_url configured)."""
    reg = AsyncMock()
    reg.get.return_value = None  # No connector config → token_url will be empty
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.get(
        "/connectors/oauth/callback",
        params={"code": "abc", "state": "", "server_id": "srv1"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    data = resp.json()
    # No state → pending_config because state is empty (also no token_url)
    assert data.get("status") in {"pending_config", "error"}


def test_oauth_callback_no_token_url_returns_pending_config():
    """When server config has no token_url, endpoint returns pending_config."""
    from app.mcp.registry import MCPServerConfig

    cfg = MCPServerConfig(
        name="myapp",
        url="http://mcp-server",
        auth_type="oauth_ac",
        auth_config={"authorize_url": "https://example.com/auth"},
        # No token_url → cannot exchange code
    )
    reg = AsyncMock()
    reg.get.return_value = cfg
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.get(
        "/connectors/oauth/callback",
        params={"code": "abc123", "state": "xyz456", "server_id": "srv1"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending_config"
    assert data["received_code"] is True
    assert data["received_state"] is True


def test_oauth_callback_invalid_state_returns_error():
    """A state that doesn't match any pending flow returns error."""
    from app.mcp.registry import MCPServerConfig

    cfg = MCPServerConfig(
        name="github",
        url="http://gh-mcp",
        auth_type="oauth_ac",
        auth_config={
            "token_url": "https://github.com/login/oauth/access_token",
            "client_id": "client123",
        },
    )
    reg = AsyncMock()
    reg.get.return_value = cfg
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.get(
        "/connectors/oauth/callback",
        params={
            "code": "realcode",
            "state": "stale-or-unknown-state",
            "server_id": "srv1",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    # OAUTH-05: an unknown/expired state is a 400 with a stable code.
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "oauth_invalid_state"
    assert "state" in detail["message"].lower()


def test_oauth_callback_valid_flow_returns_connected():
    """A matching pending flow returns connected with token metadata."""
    from app.mcp.registry import MCPServerConfig

    cfg = MCPServerConfig(
        name="github",
        url="http://gh-mcp",
        auth_type="oauth_ac",
        auth_config={
            "token_url": "https://github.com/login/oauth/access_token",
            "client_id": "client123",
        },
    )
    reg = AsyncMock()
    reg.get.return_value = cfg
    reg.unregister.return_value = True
    reg.register.return_value = "new-srv-id"

    mgr = OAuthFlowManager()
    # Start a flow so the state token is registered
    pkce = mgr.start_flow(server_id="srv1", tenant_ctx=_CTX)
    state_token = pkce["state"]

    with respx.mock:
        respx.post("https://github.com/login/oauth/access_token").mock(
            return_value=_httpx.Response(200, json={
                "access_token": "gha_test_token_xyz",
                "token_type": "bearer",
                "expires_in": 3600,
            })
        )
        client = TestClient(_make_app(reg, oauth_mgr=mgr), raise_server_exceptions=False)
        resp = client.get(
            "/connectors/oauth/callback",
            params={"code": "mycode", "state": state_token, "server_id": "srv1"},
            headers={"X-API-Key": _VALID_KEY},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "connected"
    assert "token_type" in data


def test_oauth_start_returns_pkce_params():
    """OAuth start returns state and code_challenge embedded in auth_url."""
    from app.mcp.registry import MCPServerConfig

    cfg = MCPServerConfig(
        name="github",
        url="http://gh-mcp",
        auth_type="oauth_ac",
        auth_config={
            "authorize_url": "https://github.com/login/oauth/authorize",
            "client_id": "client123",
        },
    )
    reg = AsyncMock()
    reg.get.return_value = cfg
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.get(
        "/connectors/oauth/start",
        params={"server_id": "srv1"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "auth_url" in data
    assert "state" in data
    assert "github.com" in data["auth_url"]
    # PKCE params should be in the URL
    assert "code_challenge" in data["auth_url"]
    assert "redirect_uri" in data


def test_oauth_start_non_oauth_auth_type_returns_400():
    """Connectors that are not OAuth type return 400."""
    from app.mcp.registry import MCPServerConfig

    cfg = MCPServerConfig(name="gh", url="http://github-mcp", auth_type="bearer")
    reg = AsyncMock()
    reg.get.return_value = cfg
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.get(
        "/connectors/oauth/start",
        params={"server_id": "srv1"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 400


def test_scope_extraction_from_step():
    """_extract_scope_value correctly extracts repo names and JIRA project keys."""
    from app.agent.nodes._helpers import _extract_scope_value

    assert _extract_scope_value("push to acme/my-repo") == "acme/my-repo"
    assert _extract_scope_value("create PR on org/backend-service") == "org/backend-service"
    result_jira = _extract_scope_value("resolve PROJ-123 ticket")
    assert result_jira is not None
    assert result_jira == "PROJ"
    assert _extract_scope_value("run tests") is None
    assert _extract_scope_value("deploy the application") is None


def _oauth_cfg(**extra_auth):
    from app.mcp.registry import MCPServerConfig

    return MCPServerConfig(
        name="github",
        url="http://gh-mcp",
        auth_type="oauth_ac",
        auth_config={
            "authorize_url": "https://github.com/login/oauth/authorize",
            "token_url": "https://github.com/login/oauth/access_token",
            "client_id": "client123",
            **extra_auth,
        },
    )


def test_oauth_token_exchange_reuses_the_authorize_redirect_uri():
    """Regression: /oauth/start sent redirect_uri={api}/connectors/oauth/callback?server_id=…
    to the provider, but /oauth/callback exchanged the code with a DIFFERENT value
    ({frontend_url}/connectors/oauth/callback, no query). RFC 6749 §4.1.3 requires the
    two to be identical, so every real provider answered invalid_grant."""
    from urllib.parse import parse_qs, urlparse

    reg = AsyncMock()
    reg.get.return_value = _oauth_cfg()
    reg.unregister.return_value = True
    reg.register.return_value = "srv1"
    app = _make_app(reg)
    app.state.settings = type("S", (), {"frontend_url": "http://frontend.example"})()
    client = TestClient(app, raise_server_exceptions=False)

    start = client.get(
        "/connectors/oauth/start",
        params={"server_id": "srv1"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert start.status_code == 200
    authorize_qs = parse_qs(urlparse(start.json()["auth_url"]).query)
    authorize_redirect = authorize_qs["redirect_uri"][0]
    assert start.json()["redirect_uri"] == authorize_redirect

    with respx.mock:
        route = respx.post("https://github.com/login/oauth/access_token").mock(
            return_value=_httpx.Response(200, json={"access_token": "gha_tok"})
        )
        cb = client.get(
            "/connectors/oauth/callback",
            params={"code": "c0de", "state": authorize_qs["state"][0], "server_id": "srv1"},
            headers={"X-API-Key": _VALID_KEY},
        )
    assert cb.status_code == 200
    assert cb.json()["status"] == "connected"
    sent = parse_qs(route.calls.last.request.content.decode())
    assert sent["redirect_uri"] == [authorize_redirect]


def test_oauth_callback_ignores_caller_supplied_redirect_uri():
    """A redirect_uri registered in auth_config is what the provider sees, and the
    stored value wins over a ?redirect_uri= supplied on the callback request."""
    from urllib.parse import parse_qs, urlparse

    reg = AsyncMock()
    reg.get.return_value = _oauth_cfg(redirect_uri="https://app.example/oauth/cb")
    reg.register.return_value = "srv1"
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    start = client.get(
        "/connectors/oauth/start",
        params={"server_id": "srv1"},
        headers={"X-API-Key": _VALID_KEY},
    )
    qs = parse_qs(urlparse(start.json()["auth_url"]).query)
    assert qs["redirect_uri"] == ["https://app.example/oauth/cb"]

    with respx.mock:
        route = respx.post("https://github.com/login/oauth/access_token").mock(
            return_value=_httpx.Response(200, json={"access_token": "t"})
        )
        client.get(
            "/connectors/oauth/callback",
            params={
                "code": "c",
                "state": qs["state"][0],
                "server_id": "srv1",
                "redirect_uri": "https://evil.example/steal",
            },
            headers={"X-API-Key": _VALID_KEY},
        )
    sent = parse_qs(route.calls.last.request.content.decode())
    assert sent["redirect_uri"] == ["https://app.example/oauth/cb"]
