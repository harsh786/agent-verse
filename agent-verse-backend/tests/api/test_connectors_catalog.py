"""Tests for catalog endpoint and auto-wiring."""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from app.api.connectors import router as connectors_router
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

_TENANT = TenantContext(tenant_id="cat-t1", plan=PlanTier.PROFESSIONAL, api_key_id="k")

class FakeRedis:
    def __init__(self): self._d={}; self._s={}
    async def get(self,k): return self._d.get(k)
    async def set(self,k,v,ex=None): self._d[k]=v
    async def delete(self,k):
        e=k in self._d; self._d.pop(k,None); return int(e)
    async def sadd(self,k,v): self._s.setdefault(k,set()).add(v)
    async def srem(self,k,v): self._s.get(k,set()).discard(v)
    async def smembers(self,k): return self._s.get(k,set())

def _make_app():
    app = FastAPI()
    registry = MCPRegistry(redis=FakeRedis())
    async def resolve(request, call_next):
        request.state.tenant = _TENANT
        return await call_next(request)
    app.add_middleware(BaseHTTPMiddleware, dispatch=resolve)
    app.include_router(connectors_router)
    app.state.mcp_registry = registry
    app.state.connector_secret_store = {}
    app.state.connector_secret_store_is_production_safe = False
    return app, registry

def test_catalog_returns_list():
    app, _ = _make_app()
    resp = TestClient(app).get("/connectors/catalog")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list) and len(data) > 5

def test_catalog_jira_has_required_fields():
    app, _ = _make_app()
    data = TestClient(app).get("/connectors/catalog").json()
    jira = next((e for e in data if e["name"] == "jira"), None)
    assert jira is not None
    for key in ("description", "auth_type", "category", "auth_fields", "has_builtin", "is_configured"):
        assert key in jira, f"jira entry missing {key}"
    assert isinstance(jira["auth_fields"], list)

def test_catalog_jira_auth_fields_include_url_email_token():
    app, _ = _make_app()
    data = TestClient(app).get("/connectors/catalog").json()
    jira = next(e for e in data if e["name"] == "jira")
    keys = [f["key"] for f in jira["auth_fields"]]
    assert "url" in keys
    assert "username" in keys
    assert "password" in keys

def test_catalog_is_configured_false_initially():
    app, _ = _make_app()
    data = TestClient(app).get("/connectors/catalog").json()
    jira = next(e for e in data if e["name"] == "jira")
    assert jira["is_configured"] is False

def test_register_jira_sets_is_configured_true():
    app, _ = _make_app()
    client = TestClient(app)
    client.post("/connectors", json={
        "name": "jira", "url": "https://co.atlassian.net",
        "auth_type": "basic",
        "auth_config": {"username": "u@co.com", "password": "TOKEN"},
    })
    data = client.get("/connectors/catalog").json()
    jira = next(e for e in data if e["name"] == "jira")
    assert jira["is_configured"] is True

def test_register_jira_auto_wires_builtin_handler():
    app, registry = _make_app()
    client = TestClient(app)
    resp = client.post("/connectors", json={
        "name": "jira", "url": "https://co.atlassian.net",
        "auth_type": "basic",
        "auth_config": {"username": "u@co.com", "password": "TOKEN"},
    })
    assert resp.status_code == 201
    server_id = resp.json()["server_id"]
    # The registered connector should have a builtin handler attached
    import asyncio
    cfg = asyncio.run(
        registry.get(server_id, tenant_ctx=_TENANT)
    )
    # Handler is re-attached via MCPRegistry.register_builtin_handler
    from app.mcp.registry import _BUILTIN_HANDLER_REGISTRY
    assert server_id in _BUILTIN_HANDLER_REGISTRY, (
        f"Expected builtin handler registered for {server_id}; got keys: {list(_BUILTIN_HANDLER_REGISTRY)}"
    )


@pytest.mark.asyncio
async def test_connector_test_endpoint_returns_passed_for_jira_with_valid_credentials():
    """Test endpoint uses connector credentials and returns passed on success."""
    import httpx
    import respx

    app, registry = _make_app()
    client = TestClient(app)

    # Register jira connector
    resp = client.post("/connectors", json={
        "name": "jira", "url": "https://testco.atlassian.net",
        "auth_type": "basic",
        "auth_config": {"username": "t@co.com", "password": "TOKEN"},
    })
    assert resp.status_code == 201
    server_id = resp.json()["server_id"]

    # Wire MCPClient into app state
    from app.mcp.client import MCPClient
    mcp_client = MCPClient(registry=registry)
    app.state.mcp_client = mcp_client

    with respx.mock:
        respx.post("https://testco.atlassian.net/rest/api/3/search/jql").mock(
            return_value=httpx.Response(200, json={"issues": []})
        )
        test_resp = client.post(f"/connectors/{server_id}/test")

    assert test_resp.status_code == 200
    body = test_resp.json()
    assert body["reachable"] is True
    assert body["status"] == "passed"
    assert "latency_ms" in body


@pytest.mark.asyncio
async def test_connector_test_endpoint_returns_failed_on_auth_error():
    """Test endpoint returns failed when Jira returns 401."""
    import httpx
    import respx

    app, registry = _make_app()
    client = TestClient(app)

    resp = client.post("/connectors", json={
        "name": "jira", "url": "https://testco.atlassian.net",
        "auth_type": "basic",
        "auth_config": {"username": "wrong@co.com", "password": "BAD-TOKEN"},
    })
    server_id = resp.json()["server_id"]

    from app.mcp.client import MCPClient
    app.state.mcp_client = MCPClient(registry=registry)

    with respx.mock:
        respx.post("https://testco.atlassian.net/rest/api/3/search/jql").mock(
            return_value=httpx.Response(401, json={"errorMessages": ["Unauthorized"]})
        )
        test_resp = client.post(f"/connectors/{server_id}/test")

    assert test_resp.status_code == 200
    body = test_resp.json()
    assert body["reachable"] is False
    assert body["status"] == "failed"
    assert "error" in body


def test_connector_test_endpoint_unknown_type_does_generic_check():
    """Unknown connector type uses generic GET fallback."""
    app, registry = _make_app()
    client = TestClient(app)

    resp = client.post("/connectors", json={
        "name": "unknown-service",
        "url": "http://localhost:9999/api",
        "auth_type": "bearer",
        "auth_config": {"token": "t"},
    })
    server_id = resp.json()["server_id"]

    # Without a real server running, this should fail gracefully
    test_resp = client.post(f"/connectors/{server_id}/test")
    assert test_resp.status_code == 200
    body = test_resp.json()
    assert body["server_id"] == server_id
    # Either passed (unlikely) or failed gracefully
    assert body["status"] in ("passed", "failed", "not_tested")
