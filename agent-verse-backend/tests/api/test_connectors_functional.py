"""Functional/contract coverage for app/api/connectors.py.

Focus areas (per coverage gap analysis — file was at 67-72% before this file):
  - Direct REST connector tests (_test_github/_test_jira/_test_slack/_test_stripe/
    _test_gitlab): success, auth failure (401/403), generic HTTP error, timeout,
    and network-connect-error paths — these are the *real* contract the "test
    connector" button relies on, bypassing the generic HTTP fallback.
  - OAuth popup flow (start_oauth_popup / complete_oauth_popup): not-configured
    connector, unknown connector, configured-without-secret ("pending_oauth"),
    configured-with-secret ("pending_token_exchange"), and reused/invalid state.
  - Tenant scoping: a connector registered by tenant A must be invisible (404)
    to tenant B for get/update/delete/test.
  - Malformed connector config on create → 422 validation error.
  - SSRF guard at registration time rejects private/loopback URLs → 400.
  - get_connector_usage in-memory fallback path.
"""
from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import connectors as connectors_module
from app.api.connectors import router as connectors_router
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX_A = TenantContext(tenant_id="tid-func-a", plan=PlanTier.PROFESSIONAL, api_key_id="ka")
_CTX_B = TenantContext(tenant_id="tid-func-b", plan=PlanTier.PROFESSIONAL, api_key_id="kb")
_KEY_A = "av_func_tenant_a"
_KEY_B = "av_func_tenant_b"


class _FakeRedis:
    """Minimal in-memory async Redis substitute shared by MCPRegistry tests."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def sadd(self, key: str, *values: str) -> int:
        self._sets.setdefault(key, set()).update(str(v) for v in values)
        return len(values)

    async def smembers(self, key: str) -> set[str]:
        return self._sets.get(key, set())

    async def delete(self, *keys: str) -> int:
        return sum(1 for k in keys if self._store.pop(k, None) is not None)

    async def srem(self, key: str, *values: str) -> int:
        before = len(self._sets.get(key, set()))
        self._sets.get(key, set()).difference_update(str(v) for v in values)
        return before - len(self._sets.get(key, set()))


def _make_registry() -> MCPRegistry:
    return MCPRegistry(_FakeRedis())


def _make_app(
    registry: MCPRegistry | None = None,
    *,
    settings: Any = None,
    goal_service: Any = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        if key == _KEY_A:
            return _CTX_A
        if key == _KEY_B:
            return _CTX_B
        return None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(connectors_router)
    app.state.mcp_registry = registry if registry is not None else _make_registry()
    if settings is not None:
        app.state.settings = settings
    if goal_service is not None:
        app.state.goal_service = goal_service
    return app


class _AsyncClientStub:
    """Configurable stand-in for httpx.AsyncClient used by the direct REST tests."""

    calls: list[dict[str, Any]] = []

    def __init__(self, get_response: Any = None, post_response: Any = None, raise_exc: Exception | None = None):
        self._get_response = get_response
        self._post_response = post_response
        self._raise_exc = raise_exc

    async def __aenter__(self) -> _AsyncClientStub:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, url: str, *, headers: dict | None = None, **kwargs: Any) -> Any:
        if self._raise_exc is not None:
            raise self._raise_exc
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        return self._get_response

    async def post(self, url: str, *, headers: dict | None = None, **kwargs: Any) -> Any:
        if self._raise_exc is not None:
            raise self._raise_exc
        self.calls.append({"method": "POST", "url": url, "headers": headers})
        return self._post_response


class _FakeHTTPResponse:
    def __init__(self, status_code: int, json_body: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._json = json_body or {}
        self.headers = headers or {}

    def json(self) -> dict:
        return self._json


def _register(client: TestClient, api_key: str, **overrides: Any) -> dict:
    payload = {
        "name": "connector",
        "url": "https://api.github.com/mcp",
        "auth_type": "bearer",
        "auth_config": {},
        "description": "",
    }
    payload.update(overrides)
    resp = client.post("/connectors", json=payload, headers={"X-API-Key": api_key})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Direct REST connector tests — GitHub
# ---------------------------------------------------------------------------


def test_test_connector_github_success_reports_authenticated_user(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(
        client, _KEY_A, name="github", auth_config={"token": "ghp_valid"}
    )

    stub = _AsyncClientStub(
        get_response=_FakeHTTPResponse(
            200,
            {"login": "octocat", "name": "The Octocat"},
            headers={"X-OAuth-Scopes": "repo, read:org"},
        )
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "passed"
    assert body["reachable"] is True
    assert "octocat" in body["detail"]
    assert "scopes: repo, read:org" in body["detail"]


def test_test_connector_github_invalid_token_returns_401_error(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="github", auth_config={"token": "ghp_bad"})

    stub = _AsyncClientStub(get_response=_FakeHTTPResponse(401))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["reachable"] is False
    assert "401" in body["error"] or "Invalid token" in body["error"]


def test_test_connector_github_insufficient_scope_returns_403_error(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="github", auth_config={"token": "ghp_scoped"})

    stub = _AsyncClientStub(get_response=_FakeHTTPResponse(403))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert "scope" in body["error"].lower() or "403" in body["error"]


def test_test_connector_github_network_error_reports_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="github", auth_config={"token": "ghp_x"})

    stub = _AsyncClientStub(raise_exc=httpx.ConnectError("dns failure"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert body["reachable"] is False
    assert "api.github.com" in body["error"]


def test_test_connector_github_generic_exception_is_captured(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="github", auth_config={"token": "ghp_x"})

    stub = _AsyncClientStub(raise_exc=RuntimeError("boom"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert "boom" in body["error"]


def test_test_connector_github_unexpected_status_reports_http_code(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="github", auth_config={"token": "ghp_x"})

    stub = _AsyncClientStub(get_response=_FakeHTTPResponse(503))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert "503" in body["error"]


# ---------------------------------------------------------------------------
# Direct REST connector tests — Jira
# ---------------------------------------------------------------------------


def test_test_connector_jira_success(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(
        client,
        _KEY_A,
        name="jira",
        url="https://api.atlassian.com",
        auth_config={"email": "user@acme.com", "api_token": "tok"},
    )

    stub = _AsyncClientStub(get_response=_FakeHTTPResponse(200, {"displayName": "Jane Doe"}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "passed"
    assert "Jane Doe" in body["detail"]


def test_test_connector_jira_no_base_url_fails_fast() -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="jira", url="", auth_config={"api_token": "tok"})

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert "Jira base URL not configured" in body["error"]


def test_test_connector_jira_invalid_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(
        client,
        _KEY_A,
        name="jira",
        url="https://api.atlassian.com",
        auth_config={"email": "user@acme.com", "api_token": "bad"},
    )

    stub = _AsyncClientStub(get_response=_FakeHTTPResponse(401))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert "Invalid credentials" in body["error"]


def test_test_connector_jira_timeout_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(
        client,
        _KEY_A,
        name="jira",
        url="https://api.atlassian.com",
        auth_config={"email": "u@acme.com", "api_token": "tok"},
    )

    stub = _AsyncClientStub(raise_exc=httpx.TimeoutException("timed out"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert body["reachable"] is False
    assert "timed out" in body["error"]


# ---------------------------------------------------------------------------
# Direct REST connector tests — Slack
# ---------------------------------------------------------------------------


def test_test_connector_slack_success(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="slack", auth_config={"token": "xoxb-1"})

    stub = _AsyncClientStub(
        post_response=_FakeHTTPResponse(200, {"ok": True, "user": "bot", "team": "Acme"})
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "passed"
    assert "Acme" in body["detail"]


def test_test_connector_slack_ok_false_reports_slack_error(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="slack", auth_config={"token": "xoxb-bad"})

    stub = _AsyncClientStub(
        post_response=_FakeHTTPResponse(200, {"ok": False, "error": "invalid_auth"})
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error"] == "invalid_auth"


def test_test_connector_slack_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="slack", auth_config={"token": "xoxb-1"})

    stub = _AsyncClientStub(post_response=_FakeHTTPResponse(500))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert "500" in body["error"]


def test_test_connector_slack_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="slack", auth_config={"token": "xoxb-1"})

    stub = _AsyncClientStub(raise_exc=RuntimeError("slack down"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert "slack down" in body["error"]


# ---------------------------------------------------------------------------
# Direct REST connector tests — Stripe
# ---------------------------------------------------------------------------


def test_test_connector_stripe_success(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="stripe", auth_config={"api_key": "sk_live_1"})

    stub = _AsyncClientStub(get_response=_FakeHTTPResponse(200, {"id": "acct_123"}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "passed"
    assert "acct_123" in body["detail"]


def test_test_connector_stripe_invalid_key(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="stripe", auth_config={"api_key": "sk_bad"})

    stub = _AsyncClientStub(get_response=_FakeHTTPResponse(401))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert "Invalid Stripe API key" in body["error"]


def test_test_connector_stripe_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="stripe", auth_config={"api_key": "sk_1"})

    stub = _AsyncClientStub(get_response=_FakeHTTPResponse(502))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert "502" in body["error"]


def test_test_connector_stripe_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="stripe", auth_config={"api_key": "sk_1"})

    stub = _AsyncClientStub(raise_exc=RuntimeError("stripe unreachable"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert "stripe unreachable" in body["error"]


# ---------------------------------------------------------------------------
# Direct REST connector tests — GitLab
# ---------------------------------------------------------------------------


def test_test_connector_gitlab_success(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="gitlab", auth_config={"token": "glpat-1"})

    stub = _AsyncClientStub(get_response=_FakeHTTPResponse(200, {"username": "glabuser"}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "passed"
    assert "glabuser" in body["detail"]


def test_test_connector_gitlab_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="gitlab", auth_config={"token": "bad"})

    stub = _AsyncClientStub(get_response=_FakeHTTPResponse(401))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert "Invalid GitLab token" in body["error"]


def test_test_connector_gitlab_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="gitlab", auth_config={"token": "tok"})

    stub = _AsyncClientStub(get_response=_FakeHTTPResponse(500))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert "500" in body["error"]


def test_test_connector_gitlab_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="gitlab", auth_config={"token": "tok"})

    stub = _AsyncClientStub(raise_exc=RuntimeError("gitlab unreachable"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "failed"
    assert "gitlab unreachable" in body["error"]


# ---------------------------------------------------------------------------
# test_connector — mcp_client.call_tool fallback path (_CONNECTOR_TEST_TOOLS)
# ---------------------------------------------------------------------------


def test_test_connector_uses_mcp_tool_fallback_for_linear_success() -> None:
    """'linear' has no direct REST test fn, so it goes through mcp_client.call_tool."""
    registry = _make_registry()
    app = _make_app(registry)
    mock_result = MagicMock(success=True, error=None)
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(return_value=mock_result)
    app.state.mcp_client = mock_client
    client = TestClient(app, raise_server_exceptions=False)

    created = _register(client, _KEY_A, name="linear", auth_config={"api_key": "lin_1"})
    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "passed"
    mock_client.call_tool.assert_awaited_once()
    _, kwargs = mock_client.call_tool.call_args
    assert kwargs["tool_name"] == "linear_list_issues"


def test_test_connector_mcp_tool_fallback_reports_tool_failure() -> None:
    registry = _make_registry()
    app = _make_app(registry)
    mock_result = MagicMock(success=False, error="tool execution failed")
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(return_value=mock_result)
    app.state.mcp_client = mock_client
    client = TestClient(app, raise_server_exceptions=False)

    created = _register(client, _KEY_A, name="linear", auth_config={"api_key": "lin_1"})
    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})

    body = resp.json()
    assert body["status"] == "failed"
    assert body["error"] == "tool execution failed"


def test_test_connector_mcp_tool_fallback_exception_is_caught() -> None:
    registry = _make_registry()
    app = _make_app(registry)
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(side_effect=RuntimeError("mcp call exploded"))
    app.state.mcp_client = mock_client
    client = TestClient(app, raise_server_exceptions=False)

    created = _register(client, _KEY_A, name="linear", auth_config={"api_key": "lin_1"})
    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})

    body = resp.json()
    assert body["status"] == "failed"
    assert "mcp call exploded" in body["error"]


def test_test_connector_builtin_url_not_tested() -> None:
    """A builtin:// connector with no matching test fn short-circuits to not_tested."""
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="some-unmapped-builtin", url="builtin://")

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    body = resp.json()
    assert body["status"] == "not_tested"
    assert body["reachable"] is True


def test_test_connector_resolves_vault_secret_before_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stored vault refs must be resolved to plaintext before the direct REST test runs."""
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="github", auth_config={"token": "ghp_resolve_me"})

    seen_tokens: list[str] = []

    class _Stub(_AsyncClientStub):
        async def get(self, url: str, *, headers: dict | None = None, **kwargs: Any) -> Any:
            seen_tokens.append((headers or {}).get("Authorization", ""))
            return await super().get(url, headers=headers, **kwargs)

    stub = _Stub(get_response=_FakeHTTPResponse(200, {"login": "octocat"}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: stub)

    resp = client.post(f"/connectors/{created['server_id']}/test", headers={"X-API-Key": _KEY_A})
    assert resp.status_code == 200
    assert seen_tokens == ["Bearer ghp_resolve_me"]


# ---------------------------------------------------------------------------
# OAuth popup flow — /connectors/oauth/start (POST) and /oauth/callback (POST)
# ---------------------------------------------------------------------------


class _Settings:
    def __init__(self, **kwargs: Any) -> None:
        self.frontend_url = "https://app.example.com"
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_start_oauth_popup_unconfigured_connector_returns_400() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/connectors/oauth/start",
        json={"connector_name": "github"},
        headers={"X-API-Key": _KEY_A},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["type"] == "oauth-not-configured"
    assert "GITHUB_CLIENT_ID" in detail["detail"]


def test_start_oauth_popup_unknown_connector_returns_400() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/connectors/oauth/start",
        json={"connector_name": "totally-unknown-service"},
        headers={"X-API-Key": _KEY_A},
    )
    assert resp.status_code == 400


def test_start_oauth_popup_configured_returns_auth_url_and_state() -> None:
    settings = _Settings(GITHUB_CLIENT_ID="gh-client-id")
    client = TestClient(_make_app(settings=settings), raise_server_exceptions=False)
    resp = client.post(
        "/connectors/oauth/start",
        json={"connector_name": "github"},
        headers={"X-API-Key": _KEY_A},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "github.com/login/oauth/authorize" in body["auth_url"]
    assert "gh-client-id" in body["auth_url"]
    assert body["state"]


def test_complete_oauth_popup_invalid_state_returns_400() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/connectors/oauth/callback",
        json={"code": "somecode", "state": "never-issued", "connector_name": "github"},
        headers={"X-API-Key": _KEY_A},
    )
    assert resp.status_code == 400
    assert "Invalid or expired" in resp.json()["detail"]


def test_complete_oauth_popup_state_from_other_tenant_rejected() -> None:
    """A state token minted for tenant A must not be redeemable by tenant B."""
    settings = _Settings(GITHUB_CLIENT_ID="gh-client-id")
    client = TestClient(_make_app(settings=settings), raise_server_exceptions=False)

    started = client.post(
        "/connectors/oauth/start",
        json={"connector_name": "github"},
        headers={"X-API-Key": _KEY_A},
    )
    state = started.json()["state"]

    resp = client.post(
        "/connectors/oauth/callback",
        json={"code": "somecode", "state": state, "connector_name": "github"},
        headers={"X-API-Key": _KEY_B},
    )
    assert resp.status_code == 400
    assert "Invalid or expired" in resp.json()["detail"]


def test_complete_oauth_popup_expired_state_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _Settings(GITHUB_CLIENT_ID="gh-client-id")
    client = TestClient(_make_app(settings=settings), raise_server_exceptions=False)

    started = client.post(
        "/connectors/oauth/start",
        json={"connector_name": "github"},
        headers={"X-API-Key": _KEY_A},
    )
    state = started.json()["state"]

    # Advance the clock past the 10-minute state TTL before completing.
    real_time = time.time
    monkeypatch.setattr(connectors_module.time, "time", lambda: real_time() + 700)

    resp = client.post(
        "/connectors/oauth/callback",
        json={"code": "somecode", "state": state, "connector_name": "github"},
        headers={"X-API-Key": _KEY_A},
    )
    assert resp.status_code == 400
    assert "Invalid or expired" in resp.json()["detail"]


def test_complete_oauth_popup_without_secret_registers_pending_placeholder() -> None:
    settings = _Settings(GITHUB_CLIENT_ID="gh-client-id")
    registry = _make_registry()
    app = _make_app(registry, settings=settings)
    app.state.mcp_registry = registry
    client = TestClient(app, raise_server_exceptions=False)

    started = client.post(
        "/connectors/oauth/start",
        json={"connector_name": "github"},
        headers={"X-API-Key": _KEY_A},
    )
    state = started.json()["state"]

    resp = client.post(
        "/connectors/oauth/callback",
        json={"code": "auth-code-123", "state": state, "connector_name": "github"},
        headers={"X-API-Key": _KEY_A},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "connected"
    assert "github" in body["name"]

    # The connector was registered with a clearly-marked pending status, never
    # a fake working token.
    servers = await_list_servers(registry)
    assert any(s.auth_config.get("status") == "pending_oauth" for s in servers)


def test_complete_oauth_popup_with_secret_marks_pending_token_exchange() -> None:
    settings = _Settings(GITHUB_CLIENT_ID="gh-client-id", GITHUB_CLIENT_SECRET="gh-secret")
    registry = _make_registry()
    app = _make_app(registry, settings=settings)
    client = TestClient(app, raise_server_exceptions=False)

    started = client.post(
        "/connectors/oauth/start",
        json={"connector_name": "github"},
        headers={"X-API-Key": _KEY_A},
    )
    state = started.json()["state"]

    resp = client.post(
        "/connectors/oauth/callback",
        json={"code": "auth-code-456", "state": state, "connector_name": "github"},
        headers={"X-API-Key": _KEY_A},
    )
    assert resp.status_code == 200
    servers = await_list_servers(registry)
    assert any(s.auth_config.get("status") == "pending_token_exchange" for s in servers)


def await_list_servers(registry: MCPRegistry) -> list[MCPServerConfig]:
    import asyncio

    return asyncio.run(registry.list_servers(tenant_ctx=_CTX_A))


# ---------------------------------------------------------------------------
# Tenant scoping — a connector registered by tenant A is invisible to tenant B
# ---------------------------------------------------------------------------


def test_cross_tenant_get_via_update_returns_404() -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="jira")
    server_id = created["server_id"]

    resp = client.put(
        f"/connectors/{server_id}",
        json={"name": "jira", "url": "https://x.example.com/mcp", "auth_type": "bearer"},
        headers={"X-API-Key": _KEY_B},
    )
    assert resp.status_code == 404


def test_cross_tenant_delete_returns_404() -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="jira")
    server_id = created["server_id"]

    resp = client.delete(f"/connectors/{server_id}", headers={"X-API-Key": _KEY_B})
    assert resp.status_code == 404

    # Tenant A can still see and delete its own connector.
    still_there = client.get("/connectors", headers={"X-API-Key": _KEY_A})
    assert any(c["server_id"] == server_id for c in still_there.json())
    own_delete = client.delete(f"/connectors/{server_id}", headers={"X-API-Key": _KEY_A})
    assert own_delete.status_code == 204


def test_cross_tenant_test_connector_returns_404() -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="jira")
    server_id = created["server_id"]

    resp = client.post(f"/connectors/{server_id}/test", headers={"X-API-Key": _KEY_B})
    assert resp.status_code == 404


def test_cross_tenant_list_connectors_isolated() -> None:
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    _register(client, _KEY_A, name="jira")
    _register(client, _KEY_B, name="slack")

    list_a = client.get("/connectors", headers={"X-API-Key": _KEY_A}).json()
    list_b = client.get("/connectors", headers={"X-API-Key": _KEY_B}).json()
    assert {c["name"] for c in list_a} == {"jira"}
    assert {c["name"] for c in list_b} == {"slack"}


# ---------------------------------------------------------------------------
# Malformed config on create → 422
# ---------------------------------------------------------------------------


def test_register_connector_missing_required_field_returns_422() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/connectors",
        # Missing required "name" and "auth_type"
        json={"url": "https://api.example.com/mcp"},
        headers={"X-API-Key": _KEY_A},
    )
    assert resp.status_code == 422


def test_register_connector_wrong_type_for_priority_returns_422() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/connectors",
        json={
            "name": "x",
            "url": "https://api.example.com/mcp",
            "auth_type": "bearer",
            "priority": "not-a-number",
        },
        headers={"X-API-Key": _KEY_A},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# SSRF guard at registration time
# ---------------------------------------------------------------------------


def test_register_connector_rejects_loopback_url() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/connectors",
        json={
            "name": "internal",
            "url": "http://127.0.0.1:8080/mcp",
            "auth_type": "bearer",
        },
        headers={"X-API-Key": _KEY_A},
    )
    assert resp.status_code == 400
    assert "SSRF" in resp.json()["detail"]


def test_register_connector_rejects_cloud_metadata_url() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/connectors",
        json={
            "name": "metadata",
            "url": "http://169.254.169.254/latest/meta-data/",
            "auth_type": "bearer",
        },
        headers={"X-API-Key": _KEY_A},
    )
    assert resp.status_code == 400
    assert "SSRF" in resp.json()["detail"]


def test_register_connector_builtin_marker_url_bypasses_ssrf_guard() -> None:
    """The 'builtin://' dispatch marker isn't a real network URL, so it skips SSRF checks."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/connectors",
        json={"name": "internal-tool", "url": "builtin://", "auth_type": "none"},
        headers={"X-API-Key": _KEY_A},
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# get_connector_usage — in-memory fallback (no DB session factory on goal_svc)
# ---------------------------------------------------------------------------


def test_get_connector_usage_in_memory_fallback_filters_by_connector() -> None:
    goal_svc = MagicMock()
    goal_svc._db = None
    goal_svc.list_goals = AsyncMock(
        return_value={
            "goals": [
                {
                    "execution_context": {"connector_ids": ["conn-1"]},
                    "status": "complete",
                },
                {
                    "execution_context": {"connector_ids": ["conn-2"]},
                    "status": "failed",
                },
            ]
        }
    )
    client = TestClient(_make_app(goal_service=goal_svc), raise_server_exceptions=False)

    resp = client.get("/connectors/conn-1/usage", headers={"X-API-Key": _KEY_A})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["success_rate"] == 100.0
    assert body["connector_id"] == "conn-1"


def test_get_connector_usage_without_goal_service_returns_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/connectors/conn-x/usage", headers={"X-API-Key": _KEY_A})
    assert resp.status_code == 200
    body = resp.json()
    assert body["goals"] == []
    assert body["total"] == 0
    assert body["success_rate"] is None


def test_get_connector_usage_db_path_returns_goals_and_success_rate() -> None:
    """When goal_svc has a real _db session factory, usage queries the goals table
    directly (lines 1450-1499) rather than the in-memory fallback."""
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    goal_rows = [("goal-1", "Sync issues", "complete", now, 0.42)]
    count_row = (3, 2)

    class _Result:
        def __init__(self, rows: Any) -> None:
            self._rows = rows

        def fetchall(self) -> Any:
            return self._rows

        def fetchone(self) -> Any:
            return self._rows

    class _UsageSession:
        def __init__(self) -> None:
            self._call = 0

        async def execute(self, *args: Any, **kwargs: Any) -> _Result:
            self._call += 1
            if self._call == 1:
                return _Result(None)  # SET config statement
            if self._call == 2:
                return _Result(goal_rows)
            return _Result(count_row)

        async def __aenter__(self) -> _UsageSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

    @asynccontextmanager
    async def _db_factory() -> Any:
        yield _UsageSession()

    goal_svc = MagicMock()
    goal_svc._db = _db_factory
    client = TestClient(_make_app(goal_service=goal_svc), raise_server_exceptions=False)

    resp = client.get("/connectors/conn-1/usage", headers={"X-API-Key": _KEY_A})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["success_rate"] == pytest.approx(66.7, abs=0.1)
    assert body["goals"] == [
        {
            "id": "goal-1",
            "goal": "Sync issues",
            "status": "complete",
            "created_at": now.isoformat(),
            "cost_usd": 0.42,
        }
    ]


def test_get_connector_usage_db_error_falls_back_to_empty_result() -> None:
    """A DB error during usage lookup is swallowed and returns the empty shape,
    not a 500 (lines 1510-1511)."""
    from contextlib import asynccontextmanager

    class _BrokenSession:
        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("db exploded")

        async def __aenter__(self) -> _BrokenSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

    @asynccontextmanager
    async def _db_factory() -> Any:
        yield _BrokenSession()

    goal_svc = MagicMock()
    goal_svc._db = _db_factory
    client = TestClient(_make_app(goal_service=goal_svc), raise_server_exceptions=False)

    resp = client.get("/connectors/conn-1/usage", headers={"X-API-Key": _KEY_A})
    assert resp.status_code == 200
    body = resp.json()
    assert body["goals"] == []
    assert body["total"] == 0
    assert body["success_rate"] is None


# ---------------------------------------------------------------------------
# _build_auth_headers / _resolve_auth_value / _secret_resolver — direct unit
# tests for the auth-header-building helpers (lines 154-206).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_auth_headers_bearer() -> None:
    from app.mcp.registry import AuthType, MCPServerConfig

    cfg = MCPServerConfig(
        name="x", url="https://x.example.com", auth_type=AuthType.BEARER,
        auth_config={"token": "tok-123"},
    )
    headers = await connectors_module._build_auth_headers(cfg)
    assert headers == {"Authorization": "Bearer tok-123"}


@pytest.mark.asyncio
async def test_build_auth_headers_api_key_custom_header_name() -> None:
    from app.mcp.registry import AuthType, MCPServerConfig

    cfg = MCPServerConfig(
        name="x", url="https://x.example.com", auth_type=AuthType.API_KEY,
        auth_config={"header_name": "X-Custom-Key", "api_key": "key-abc"},
    )
    headers = await connectors_module._build_auth_headers(cfg)
    assert headers == {"X-Custom-Key": "key-abc"}


@pytest.mark.asyncio
async def test_build_auth_headers_basic() -> None:
    import base64

    from app.mcp.registry import AuthType, MCPServerConfig

    cfg = MCPServerConfig(
        name="x", url="https://x.example.com", auth_type=AuthType.BASIC,
        auth_config={"username": "alice", "password": "secret"},
    )
    headers = await connectors_module._build_auth_headers(cfg)
    expected = base64.b64encode(b"alice:secret").decode()
    assert headers == {"Authorization": f"Basic {expected}"}


@pytest.mark.asyncio
async def test_build_auth_headers_custom_header_multiple_keys() -> None:
    from app.mcp.registry import AuthType, MCPServerConfig

    cfg = MCPServerConfig(
        name="x", url="https://x.example.com", auth_type=AuthType.CUSTOM_HEADER,
        auth_config={"X-One": "a", "X-Two": "b"},
    )
    headers = await connectors_module._build_auth_headers(cfg)
    assert headers == {"X-One": "a", "X-Two": "b"}


@pytest.mark.asyncio
async def test_build_auth_headers_none_type_returns_empty() -> None:
    from app.mcp.registry import AuthType, MCPServerConfig

    cfg = MCPServerConfig(name="x", url="https://x.example.com", auth_type=AuthType.NONE)
    headers = await connectors_module._build_auth_headers(cfg)
    assert headers == {}


@pytest.mark.asyncio
async def test_resolve_auth_value_with_secret_ref_and_resolver() -> None:
    """A secret:// ref is resolved via the async resolver callback, not returned raw."""

    async def _resolver(ref: str) -> str:
        assert ref == "vault://connectors/srv-1/token"
        return "resolved-plaintext"

    value = await connectors_module._resolve_auth_value(
        "vault://connectors/srv-1/token", _resolver
    )
    assert value == "resolved-plaintext"


@pytest.mark.asyncio
async def test_resolve_auth_value_plain_string_passthrough() -> None:
    value = await connectors_module._resolve_auth_value("plain-value", None)
    assert value == "plain-value"


@pytest.mark.asyncio
async def test_secret_resolver_resolves_via_tenant_scoped_store() -> None:
    """_secret_resolver builds a resolver bound to the request's tenant + secret store."""
    request = MagicMock()
    request.state.tenant = _CTX_A
    request.app.state.connector_secret_store = {
        "vault://connectors/srv-1/token": "plaintext-token"
    }
    request.app.state.connector_secret_store_is_production_safe = False

    resolve = connectors_module._secret_resolver(request)
    result = await resolve("vault://connectors/srv-1/token")
    assert result == "plaintext-token"
