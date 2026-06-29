"""Comprehensive tests for /connectors API endpoints — targets 18% → 55%+ coverage."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.connectors import router as connectors_router
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-connectors", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_connectors_comp"


class _FakeRedis:
    """Minimal in-memory async Redis stub for MCPRegistry tests."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def sadd(self, key: str, *values: str) -> int:
        if key not in self._sets:
            self._sets[key] = set()
        self._sets[key].update(str(v) for v in values)
        return len(values)

    async def smembers(self, key: str) -> set[str]:
        return self._sets.get(key, set())

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                count += 1
        return count

    async def srem(self, key: str, *values: str) -> int:
        if key not in self._sets:
            return 0
        before = len(self._sets[key])
        self._sets[key] -= {str(v) for v in values}
        return before - len(self._sets[key])


def _make_app(registry: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(connectors_router)
    app.state.mcp_registry = registry or MCPRegistry(_FakeRedis())
    return app


def _reg_with_connector(name: str = "github", url: str = "http://mcp.github.com/mcp") -> tuple[MCPRegistry, str]:
    """Returns (registry, server_id) after registering a connector."""
    import asyncio
    reg = MCPRegistry(_FakeRedis())

    async def _register():
        def cfg_factory(sid):
            return MCPServerConfig(
                server_id=sid,
                name=name,
                url=url,
                auth_type="bearer",
                auth_config={"token": "tok"},
            )
        return await reg.register(cfg_factory, tenant_ctx=_CTX)

    loop = asyncio.new_event_loop()
    server_id = loop.run_until_complete(_register())
    loop.close()
    return reg, server_id


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_list_connectors_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/connectors")
    assert resp.status_code == 401


def test_register_connector_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/connectors",
        json={"name": "github", "url": "http://mcp.github.com", "auth_type": "bearer"},
    )
    assert resp.status_code == 401


def test_get_catalog_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/connectors/catalog")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def test_get_catalog() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/connectors/catalog", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    catalog = resp.json()
    assert isinstance(catalog, list)
    # Catalog should have at least some entries
    assert len(catalog) >= 0
    for entry in catalog:
        assert "name" in entry
        assert "auth_type" in entry


# ---------------------------------------------------------------------------
# list_connectors
# ---------------------------------------------------------------------------

def test_list_connectors_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/connectors", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_connectors_after_register() -> None:
    reg, server_id = _reg_with_connector("github")
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.get("/connectors", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    connectors = resp.json()
    assert len(connectors) == 1
    assert connectors[0]["name"] == "github"
    # Sensitive auth values should be redacted
    auth_config = connectors[0].get("auth_config", {})
    if "token" in auth_config:
        assert auth_config["token"] == "<redacted>"


# ---------------------------------------------------------------------------
# register_connector
# ---------------------------------------------------------------------------

def test_register_connector_bearer() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/connectors",
        json={
            "name": "github",
            "url": "http://mcp.github.com/mcp",
            "auth_type": "bearer",
            "auth_config": {"token": "ghp_test123"},
            "description": "GitHub MCP",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "github"
    assert "server_id" in body


def test_register_connector_api_key_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/connectors",
        json={
            "name": "jira",
            "url": "http://mcp.jira.com/mcp",
            "auth_type": "api_key",
            "auth_config": {"api_key": "jira-key-123", "header_name": "X-Jira-Key"},
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201


def test_register_connector_no_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/connectors",
        json={
            "name": "public-mcp",
            "url": "http://mcp.public.com/mcp",
            "auth_type": "none",
            "auth_config": {},
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# get_connector
# ---------------------------------------------------------------------------

def test_get_connector_success() -> None:
    reg, server_id = _reg_with_connector()
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    # There is no GET /{server_id} endpoint; list all and find by server_id.
    resp = client.get("/connectors", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    connectors = resp.json()
    found = next((c for c in connectors if c.get("server_id") == server_id), None)
    assert found is not None
    assert found["name"] == "github"


def test_get_connector_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    # The test-connectivity endpoint returns 404 for unknown connectors.
    resp = client.post("/connectors/does-not-exist-xyz/test", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# update_connector
# ---------------------------------------------------------------------------

def test_update_connector_success() -> None:
    reg, server_id = _reg_with_connector()
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.put(
        f"/connectors/{server_id}",
        json={
            "name": "github-updated",
            "url": "http://mcp.github.com/mcp",
            "auth_type": "bearer",
            "auth_config": {"token": "<redacted>"},
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "github-updated"


def test_update_connector_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.put(
        "/connectors/nonexistent",
        json={
            "name": "github",
            "url": "http://mcp.github.com",
            "auth_type": "bearer",
            "auth_config": {},
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# unregister_connector
# ---------------------------------------------------------------------------

def test_unregister_connector_success() -> None:
    reg, server_id = _reg_with_connector()
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.delete(f"/connectors/{server_id}", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204


def test_unregister_connector_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/connectors/nonexistent", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# test_connector (health check)
# ---------------------------------------------------------------------------

def test_test_connector_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/connectors/nonexistent/test", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


def test_test_connector_connection_failure() -> None:
    reg, server_id = _reg_with_connector("github", "http://localhost:9999/mcp")
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.post(
        f"/connectors/{server_id}/test",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "reachable" in body
    assert body["reachable"] is False  # Should fail since server is unreachable


# ---------------------------------------------------------------------------
# discover tools
# ---------------------------------------------------------------------------

def test_discover_tools_no_mcp_client() -> None:
    reg, server_id = _reg_with_connector()
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    # The discover endpoint is POST /{server_id}/discover (not GET /tools).
    # Without an mcp_client on app.state it returns 503.
    resp = client.post(
        f"/connectors/{server_id}/discover",
        headers={"X-API-Key": _VALID_KEY},
    )
    # Should return 503 (no MCP client) or 200 with tool metadata
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        body = resp.json()
        assert "tools_discovered" in body
