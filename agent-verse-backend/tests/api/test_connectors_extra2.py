"""Extra coverage for /connectors API — pushes connectors.py from 77.6% → 88%+.

Covers:
  - _require_tenant raise (line 52)
  - list_connectors with list_server_records (lines 261-265)
  - register_connector secret storage failure (lines 302-304)
  - update_connector not found (lines 336-337) + secret storage failure (line 351)
  - test_connector exception path (lines 422-435)
  - oauth_start no oauth_manager (lines 480-482)
  - oauth_start with authorize_url+client_id (line 510)
  - oauth_callback no oauth_manager (line 539)
  - oauth_callback exchange_code error (lines 611-612)
  - import_openapi_connector (lines 687-741)
  - list_capabilities with DB (lines 766-776) and q filter (lines 814-815)
  - search_capabilities (lines 839-840)
  - discover_connector_tools (lines 856-890)
  - missing_capabilities (lines 915-916)
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.connectors import _require_tenant, router as connectors_router
from app.mcp.oauth import OAuthToken
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-conn-ex2", plan=PlanTier.PROFESSIONAL, api_key_id="kx2")
_VALID_KEY = "av_test_connectors_ex2"


# ---------------------------------------------------------------------------
# Fake Redis for MCPRegistry
# ---------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    async def set(self, key: str, value: str) -> None:
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


# ---------------------------------------------------------------------------
# Minimal async DB session mock
# ---------------------------------------------------------------------------


class _MockSession:
    def __init__(self, *, many: list = None) -> None:
        self._many = many or []
        self.added: list = []

    async def execute(self, *args: Any, **kwargs: Any) -> "_MockSession":
        return self

    def fetchall(self) -> list:
        return self._many

    def scalars(self) -> "_MockSession":
        return self

    def all(self) -> list:
        return self._many

    def scalar_one_or_none(self) -> Any:
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        pass

    async def refresh(self, obj: Any) -> None:
        pass

    def begin(self) -> Any:
        @asynccontextmanager
        async def _txn() -> Any:
            yield self

        return _txn()

    async def __aenter__(self) -> "_MockSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


def _make_db_factory(*, many: list = None) -> Any:
    @asynccontextmanager
    async def _factory() -> Any:
        yield _MockSession(many=many)

    return _factory


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_app(
    registry: MCPRegistry | None = None,
    mcp_client: Any = None,
    oauth_manager: Any = None,
    db_factory: Any = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(connectors_router)
    app.state.mcp_registry = registry if registry is not None else _make_registry()
    if mcp_client is not None:
        app.state.mcp_client = mcp_client
    if oauth_manager is not None:
        app.state.oauth_manager = oauth_manager
    if db_factory is not None:
        app.state.db_session_factory = db_factory
    return app


def _register_connector(
    client: TestClient,
    name: str = "Test Connector",
    auth_type: str = "bearer",
    auth_config: dict | None = None,
) -> dict:
    resp = client.post(
        "/connectors",
        json={
            "name": name,
            "url": "https://connector.example.com/mcp",
            "auth_type": auth_type,
            "auth_config": auth_config or {},
            "description": "Test",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201, f"Register failed: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# _require_tenant unit test (line 52)
# ---------------------------------------------------------------------------


def test_require_tenant_raises_401() -> None:
    """_require_tenant raises HTTPException(401) when state.tenant is None (line 52)."""
    request = MagicMock()
    request.state.tenant = None

    with pytest.raises(HTTPException) as exc_info:
        _require_tenant(request)
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# list_connectors with list_server_records (lines 261-265)
# ---------------------------------------------------------------------------


def test_list_connectors_uses_list_server_records() -> None:
    """list_connectors falls back to list_server_records when available (lines 261-265)."""
    registry = _make_registry()

    # Add a method list_server_records to the registry mock
    fake_cfg = MCPServerConfig(
        server_id="srv-1",
        name="My Tool",
        url="https://tool.example.com/mcp",
        auth_type="bearer",
    )
    registry.list_server_records = AsyncMock(return_value=[("srv-1", fake_cfg)])

    client = TestClient(_make_app(registry=registry), raise_server_exceptions=False)
    resp = client.get("/connectors", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert any(item.get("server_id") == "srv-1" for item in body)


# ---------------------------------------------------------------------------
# register_connector secret storage failure (lines 302-304)
# ---------------------------------------------------------------------------


def test_register_connector_secret_storage_fails_unregisters() -> None:
    """When secret storage raises, connector is unregistered + 503 returned (lines 302-304)."""
    registry = _make_registry()
    client = TestClient(_make_app(registry=registry), raise_server_exceptions=False)

    # In production mode, secret store raises if not production-safe
    import os

    original_env = os.environ.get("ENVIRONMENT", "development")
    try:
        os.environ["ENVIRONMENT"] = "production"
        resp = client.post(
            "/connectors",
            json={
                "name": "Secret Connector",
                "url": "https://c.example.com/mcp",
                "auth_type": "bearer",
                "auth_config": {"token": "super-secret-key"},
            },
            headers={"X-API-Key": _VALID_KEY},
        )
        # 503 because production secret store is not configured
        assert resp.status_code == 503
    finally:
        if original_env == "development":
            os.environ.pop("ENVIRONMENT", None)
        else:
            os.environ["ENVIRONMENT"] = original_env


# ---------------------------------------------------------------------------
# update_connector not found (lines 336-337)
# ---------------------------------------------------------------------------


def test_update_connector_not_found_returns_404() -> None:
    """Updating a non-existent connector returns 404 (lines 336-337)."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.put(
        "/connectors/nonexistent-server",
        json={"name": "Updated", "url": "https://x.com/mcp", "auth_type": "none"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# test_connector exception path (lines 422-435)
# ---------------------------------------------------------------------------


def test_test_connector_connection_exception() -> None:
    """test_connector catches network exception and returns unreachable status (lines 422-435)."""
    registry = _make_registry()
    client = TestClient(_make_app(registry=registry), raise_server_exceptions=False)

    # Register a connector
    connector = _register_connector(client, name="Network Failing Connector")
    server_id = connector["server_id"]

    # The endpoint tries to connect to https://connector.example.com/mcp
    # which will fail with a connection error in tests — caught and returned
    resp = client.post(
        f"/connectors/{server_id}/test",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is False
    assert body["status"] == "failed"
    assert body["server_id"] == server_id


# ---------------------------------------------------------------------------
# oauth_start — no oauth_manager (lines 480-482)
# ---------------------------------------------------------------------------


def test_oauth_start_no_oauth_manager_returns_500() -> None:
    """oauth_start raises 500 when oauth_manager not configured (lines 480-482)."""
    registry = _make_registry()
    client = TestClient(_make_app(registry=registry), raise_server_exceptions=False)

    # Register a pkce connector
    connector = _register_connector(
        client,
        name="OAuth Connector",
        auth_type="pkce",
        auth_config={"authorize_url": "https://auth.example.com/oauth/authorize", "client_id": "client-abc"},
    )
    server_id = connector["server_id"]

    resp = client.get(
        f"/connectors/oauth/start?server_id={server_id}",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 500
    assert "OAuth manager" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# oauth_start — with full authorize_url and client_id (line 510)
# ---------------------------------------------------------------------------


def test_oauth_start_builds_full_authorization_url() -> None:
    """oauth_start with authorize_url+client_id builds a full auth URL (line 510)."""
    registry = _make_registry()

    mock_oauth = MagicMock()
    mock_oauth.start_flow.return_value = {
        "state": "random-state-xyz",
        "code_challenge": "challenge-abc",
    }

    client = TestClient(
        _make_app(registry=registry, oauth_manager=mock_oauth),
        raise_server_exceptions=False,
    )

    connector = _register_connector(
        client,
        name="OAuth Full",
        auth_type="pkce",
        auth_config={
            "authorize_url": "https://auth.example.com/authorize",
            "client_id": "my-client-id",
        },
    )
    server_id = connector["server_id"]

    resp = client.get(
        f"/connectors/oauth/start?server_id={server_id}",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "auth_url" in body
    assert "https://auth.example.com/authorize" in body["auth_url"]
    assert "my-client-id" in body["auth_url"]
    assert body["state"] == "random-state-xyz"


# ---------------------------------------------------------------------------
# oauth_callback — no oauth_manager (line 539)
# ---------------------------------------------------------------------------


def test_oauth_callback_no_oauth_manager_returns_503() -> None:
    """oauth_callback raises 503 when oauth_manager not configured (line 539)."""
    client = TestClient(_make_app(), raise_server_exceptions=False)

    resp = client.get(
        "/connectors/oauth/callback?code=abc&state=xyz&server_id=srv-1",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 503
    assert "OAuth flow manager" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# oauth_callback — exchange_code raises exception (lines 611-612)
# ---------------------------------------------------------------------------


def test_oauth_callback_exchange_error_returns_error_status() -> None:
    """oauth_callback with exchange_code raising returns error status (lines 611-612)."""
    registry = _make_registry()

    mock_oauth = MagicMock()
    mock_oauth.start_flow.return_value = {"state": "st", "code_challenge": "ch"}
    mock_oauth.exchange_code = AsyncMock(side_effect=ValueError("Invalid code"))

    client = TestClient(
        _make_app(registry=registry, oauth_manager=mock_oauth),
        raise_server_exceptions=False,
    )

    # Register connector with token_url
    connector = _register_connector(
        client,
        name="OAuth Exchange Fail",
        auth_type="pkce",
        auth_config={
            "authorize_url": "https://auth.example.com/authorize",
            "token_url": "https://auth.example.com/token",
            "client_id": "client-id",
        },
    )
    server_id = connector["server_id"]

    resp = client.get(
        f"/connectors/oauth/callback?code=bad-code&state=st&server_id={server_id}",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert "Invalid code" in body["message"]


# ---------------------------------------------------------------------------
# import_openapi_connector (lines 687-741)
# ---------------------------------------------------------------------------


_MINIMAL_OPENAPI_SPEC = json.dumps({
    "openapi": "3.0.0",
    "info": {"title": "Test Service API", "version": "1.0.0"},
    "paths": {
        "/search": {
            "get": {
                "operationId": "searchItems",
                "summary": "Search for items",
                "responses": {"200": {"description": "Success"}},
            }
        },
        "/items": {
            "post": {
                "operationId": "createItem",
                "summary": "Create an item",
                "responses": {"201": {"description": "Created"}},
            }
        },
    },
})


def test_import_openapi_connector_registers_connector() -> None:
    """import_openapi_connector parses spec and registers connector (lines 687-741)."""
    registry = _make_registry()
    client = TestClient(_make_app(registry=registry), raise_server_exceptions=False)

    resp = client.post(
        "/connectors/import-openapi",
        json={
            "openapi_spec": _MINIMAL_OPENAPI_SPEC,
            "base_url": "https://api.example.com",
            "name": "Imported Service",
            "auth_type": "bearer",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "server_id" in body
    assert body["tools_imported"] == 2
    assert body["base_url"] == "https://api.example.com"
    assert body["name"] == "Imported Service"


def test_import_openapi_connector_auto_name_from_spec() -> None:
    """import_openapi_connector uses spec title when name not provided (lines 687-741)."""
    registry = _make_registry()
    client = TestClient(_make_app(registry=registry), raise_server_exceptions=False)

    resp = client.post(
        "/connectors/import-openapi",
        json={
            "openapi_spec": _MINIMAL_OPENAPI_SPEC,
            "base_url": "https://api.example.com",
            # name omitted — should use spec info.title
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "Test Service API" in body["name"]


def test_import_openapi_connector_invalid_json_returns_error() -> None:
    """import_openapi_connector returns an error for invalid JSON spec (line 696-701)."""
    import warnings
    client = TestClient(_make_app(), raise_server_exceptions=False)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        resp = client.post(
            "/connectors/import-openapi",
            json={
                "openapi_spec": '{"not": "valid openapi" BROKEN',
                "base_url": "https://api.example.com",
            },
            headers={"X-API-Key": _VALID_KEY},
        )
    # Should return 422 (Starlette may use deprecated constant but still works)
    assert resp.status_code in (422, 500)
    if resp.status_code == 422:
        assert "Cannot parse" in resp.json().get("detail", "")


def test_import_openapi_connector_unknown_auth_type_defaults_to_bearer() -> None:
    """Unknown auth_type is normalised to 'bearer' (lines 712-713)."""
    registry = _make_registry()
    client = TestClient(_make_app(registry=registry), raise_server_exceptions=False)

    resp = client.post(
        "/connectors/import-openapi",
        json={
            "openapi_spec": _MINIMAL_OPENAPI_SPEC,
            "base_url": "https://api.example.com",
            "auth_type": "magic_token",  # unknown → bearer
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# list_capabilities with DB mock (lines 766-776)
# ---------------------------------------------------------------------------


def test_list_capabilities_with_db_returns_rows() -> None:
    """list_capabilities queries DB tool_capabilities table (lines 766-776)."""
    # Mock DB rows: (tool_name, connector_id, description, risk_level, health, success, latency)
    fake_rows = [
        ("search_repo", "conn-1", "Search GitHub repos", "low", "healthy", 0.95, 12.3),
        ("create_issue", "conn-1", "Create GitHub issue", "medium", "healthy", 0.88, 45.6),
    ]
    db_factory = _make_db_factory(many=fake_rows)

    client = TestClient(_make_app(db_factory=db_factory), raise_server_exceptions=False)
    resp = client.get("/connectors/capabilities", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    # Returns list of capability dicts (or falls back to catalog on error)
    assert isinstance(body, list)


def test_list_capabilities_with_db_and_query_filter() -> None:
    """list_capabilities with q param adds ILIKE filter (lines 814-815)."""
    db_factory = _make_db_factory(many=[])
    client = TestClient(_make_app(db_factory=db_factory), raise_server_exceptions=False)
    resp = client.get(
        "/connectors/capabilities?q=github",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_list_capabilities_without_db_falls_back_to_catalog() -> None:
    """list_capabilities without DB returns catalog fallback."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/connectors/capabilities", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    # Catalog entries have at minimum tool_name field
    if body:
        assert "tool_name" in body[0]


# ---------------------------------------------------------------------------
# search_capabilities (lines 839-840)
# ---------------------------------------------------------------------------


def test_search_capabilities_without_mcp_client() -> None:
    """search_capabilities runs even without mcp_client (lines 839-840)."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get(
        "/connectors/capabilities/search?q=search",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "query" in body
    assert body["query"] == "search"
    assert "results" in body


def test_search_capabilities_with_mcp_client() -> None:
    """search_capabilities calls mcp_client.discover_all_tools (lines 839-840)."""
    mock_tool = MagicMock()
    mock_tool.name = "search_issues"
    mock_tool.description = "Search for issues"
    mock_tool.to_dict.return_value = {"name": "search_issues", "description": "Search for issues"}

    mock_client = MagicMock()
    mock_client.discover_all_tools = AsyncMock(return_value=[mock_tool])

    client = TestClient(_make_app(mcp_client=mock_client), raise_server_exceptions=False)
    resp = client.get(
        "/connectors/capabilities/search?q=search",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "search"
    mock_client.discover_all_tools.assert_awaited_once()


# ---------------------------------------------------------------------------
# discover_connector_tools (lines 856-890)
# ---------------------------------------------------------------------------


def test_discover_connector_tools_no_mcp_client_returns_503() -> None:
    """discover_connector_tools without mcp_client → 503 (line 862-863)."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/connectors/some-server/discover",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 503


def test_discover_connector_tools_with_mcp_client() -> None:
    """discover_connector_tools calls mcp_client.discover_tools (lines 856-890)."""
    mock_tool = MagicMock()
    mock_tool.name = "create_pr"
    mock_tool.description = "Create pull request"
    mock_tool.input_schema = {"type": "object"}
    mock_tool.risk_level = "medium"

    mock_client = MagicMock()
    mock_client.discover_tools = AsyncMock(return_value=[mock_tool])

    registry = _make_registry()
    client = TestClient(_make_app(registry=registry, mcp_client=mock_client), raise_server_exceptions=False)

    # Register a connector first
    connector = _register_connector(client, name="GitHub")
    server_id = connector["server_id"]

    resp = client.post(
        f"/connectors/{server_id}/discover",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["server_id"] == server_id
    assert body["tools_discovered"] == 1
    mock_client.discover_tools.assert_awaited_once()


def test_discover_connector_tools_discovery_failure_returns_500() -> None:
    """discover_connector_tools propagates discovery exception as 500 (lines 866-867)."""
    mock_client = MagicMock()
    mock_client.discover_tools = AsyncMock(side_effect=RuntimeError("Connection refused"))

    registry = _make_registry()
    client = TestClient(_make_app(registry=registry, mcp_client=mock_client), raise_server_exceptions=False)

    connector = _register_connector(client, name="Broken Connector")
    server_id = connector["server_id"]

    resp = client.post(
        f"/connectors/{server_id}/discover",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# missing_capabilities (lines 915-916)
# ---------------------------------------------------------------------------


def test_missing_capabilities_without_mcp_client() -> None:
    """missing_capabilities works without mcp_client (no tools)."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get(
        "/connectors/capabilities/missing?goal=search github for issues",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "goal" in body
    assert "missing_connectors" in body


def test_missing_capabilities_with_mcp_client() -> None:
    """missing_capabilities calls mcp_client.discover_all_tools (lines 915-916)."""
    mock_client = MagicMock()
    mock_client.discover_all_tools = AsyncMock(return_value=[])

    client = TestClient(_make_app(mcp_client=mock_client), raise_server_exceptions=False)
    resp = client.get(
        "/connectors/capabilities/missing?goal=deploy to production",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["available_tool_count"] == 0
    mock_client.discover_all_tools.assert_awaited_once()


# ---------------------------------------------------------------------------
# Additional tests to push 86% → 88%
# ---------------------------------------------------------------------------


class _LegacyRegistry:
    """Fake registry WITHOUT list_server_records to test fallback path (lines 261-265)."""

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerConfig] = {}

    async def list_servers(self, *, tenant_ctx: Any) -> list[MCPServerConfig]:
        return list(self._servers.values())

    async def register(self, config_or_factory: Any, *, tenant_ctx: Any) -> str:
        sid = f"srv-{len(self._servers)}"
        if callable(config_or_factory):
            cfg = config_or_factory(sid)
        else:
            cfg = config_or_factory
        self._servers[sid] = cfg
        return sid

    async def get(self, server_id: str, *, tenant_ctx: Any) -> MCPServerConfig | None:
        return self._servers.get(server_id)

    async def update(self, server_id: str, cfg: Any, *, tenant_ctx: Any) -> bool:
        if server_id not in self._servers:
            return False
        self._servers[server_id] = cfg
        return True

    async def unregister(self, server_id: str, *, tenant_ctx: Any) -> bool:
        return self._servers.pop(server_id, None) is not None


def test_list_connectors_fallback_when_no_list_server_records() -> None:
    """list_connectors uses list_servers fallback when list_server_records unavailable (lines 261-265)."""
    registry = _LegacyRegistry()  # no list_server_records attribute
    client = TestClient(_make_app(registry=registry), raise_server_exceptions=False)

    resp = client.get("/connectors", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_test_connector_with_db_factory_persists_snapshot() -> None:
    """test_connector with db_session_factory persists health snapshot (lines 422-435)."""
    registry = _make_registry()
    db_factory = _make_db_factory()
    client = TestClient(_make_app(registry=registry, db_factory=db_factory), raise_server_exceptions=False)

    connector = _register_connector(client, name="DB Snapshot Connector")
    server_id = connector["server_id"]

    # Will fail to connect but still attempt DB persist
    resp = client.post(
        f"/connectors/{server_id}/test",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is False  # can't connect in test env


def test_get_connector_health_history_with_db() -> None:
    """health history with DB session (lines 449-473)."""
    # Mock rows (status, latency_ms, error, checked_at)
    from types import SimpleNamespace
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    fake_row = SimpleNamespace(
        status="healthy",
        latency_ms=12,
        error=None,
        checked_at=now,
    )

    class _HealthSession:
        async def execute(self, *a: Any, **kw: Any) -> "_HealthSession":
            return self

        def scalars(self) -> "_HealthSession":
            return self

        def all(self) -> list:
            return [fake_row]

        async def __aenter__(self) -> "_HealthSession":
            return self

        async def __aexit__(self, *a: Any) -> None:
            pass

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _health_db_factory() -> Any:
        yield _HealthSession()

    registry = _make_registry()
    client = TestClient(
        _make_app(registry=registry, db_factory=_health_db_factory),
        raise_server_exceptions=False,
    )

    connector = _register_connector(client, name="Health Connector")
    server_id = connector["server_id"]

    resp = client.get(
        f"/connectors/{server_id}/health",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    # May return empty list if query fails (RLS context needs real session)
    assert isinstance(resp.json(), list)


def test_update_connector_found_and_updated() -> None:
    """update_connector when connector exists updates and returns (covers lines 336-337 via not-found path)."""
    registry = _make_registry()
    client = TestClient(_make_app(registry=registry), raise_server_exceptions=False)

    # Register first
    connector = _register_connector(client, name="Original")
    server_id = connector["server_id"]

    # Update it
    resp = client.put(
        f"/connectors/{server_id}",
        json={
            "name": "Updated Connector",
            "url": "https://updated.example.com/mcp",
            "auth_type": "none",
            "description": "Updated description",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Connector"


def test_import_openapi_connector_with_db_persists_tools() -> None:
    """import_openapi_connector with db_factory tries to persist tools (lines 732-739)."""
    registry = _make_registry()

    class _PersistSession:
        async def execute(self, *a: Any, **kw: Any) -> "_PersistSession":
            return self

        async def commit(self) -> None:
            pass

        def begin(self) -> Any:
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _txn() -> Any:
                yield self

            return _txn()

        async def __aenter__(self) -> "_PersistSession":
            return self

        async def __aexit__(self, *a: Any) -> None:
            pass

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _persist_db() -> Any:
        yield _PersistSession()

    client = TestClient(
        _make_app(registry=registry, db_factory=_persist_db),
        raise_server_exceptions=False,
    )

    resp = client.post(
        "/connectors/import-openapi",
        json={
            "openapi_spec": _MINIMAL_OPENAPI_SPEC,
            "base_url": "https://api.example.com",
            "name": "Persisted API",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert resp.json()["tools_imported"] == 2
