"""GET /connectors/{id} and GET /connectors/{id}/tools.

The connector detail page called both, but the backend only had PUT/DELETE on
/connectors/{id} (so GET was a 405) and no /tools route at all — the page could
never render. These routes are tenant-scoped through the registry, 404 for a
connector the caller does not own, and fail loudly when live discovery fails
instead of returning an empty tool list.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.connectors import router as connectors_router
from app.mcp.client import ToolDefinition
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from tests.api.test_connectors_cross_tenant_extra import _FakeRedis

_CTX_A = TenantContext(tenant_id="tid-detail-a", plan=PlanTier.PROFESSIONAL, api_key_id="ka")
_CTX_B = TenantContext(tenant_id="tid-detail-b", plan=PlanTier.PROFESSIONAL, api_key_id="kb")
_A = {"X-API-Key": "av_detail_a"}
_B = {"X-API-Key": "av_detail_b"}


class _Client:
    def __init__(self, tools: list[ToolDefinition] | Exception) -> None:
        self._tools = tools
        self.calls: list[tuple[str, str]] = []

    async def discover_tools(
        self, *, server_id: str, tenant_ctx: TenantContext
    ) -> list[ToolDefinition]:
        self.calls.append((server_id, tenant_ctx.tenant_id))
        if isinstance(self._tools, Exception):
            raise self._tools
        return self._tools


def _app(registry: MCPRegistry, mcp_client: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return {"av_detail_a": _CTX_A, "av_detail_b": _CTX_B}.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(connectors_router)
    app.state.mcp_registry = registry
    if mcp_client is not None:
        app.state.mcp_client = mcp_client
    return app


def _register(registry: MCPRegistry, **auth: str) -> str:
    cfg = MCPServerConfig(
        name="jira",
        url="https://api.example.com/mcp",
        auth_type="bearer",
        auth_config=dict(auth),
    )
    return asyncio.run(registry.register(cfg, tenant_ctx=_CTX_A))


def test_get_connector_returns_the_masked_record() -> None:
    registry = MCPRegistry(_FakeRedis())
    sid = _register(registry, token="super-secret-token")
    resp = TestClient(_app(registry)).get(f"/connectors/{sid}", headers=_A)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["server_id"] == sid
    assert body["name"] == "jira"
    assert "super-secret-token" not in resp.text


def test_get_connector_is_404_for_unknown_and_other_tenants() -> None:
    registry = MCPRegistry(_FakeRedis())
    sid = _register(registry)
    client = TestClient(_app(registry))
    assert client.get(f"/connectors/{sid}", headers=_B).status_code == 404
    assert client.get("/connectors/does-not-exist", headers=_A).status_code == 404


def test_static_connector_routes_are_not_shadowed_by_get_by_id() -> None:
    registry = MCPRegistry(_FakeRedis())
    client = TestClient(_app(registry), raise_server_exceptions=False)
    # /catalog and /capabilities are literal routes; a GET /{server_id} must not
    # capture them (it would 404 "connector not found").
    assert client.get("/connectors/catalog", headers=_A).status_code == 200
    assert client.get("/connectors/capabilities", headers=_A).status_code != 404


def test_list_tools_returns_live_discovered_tools() -> None:
    registry = MCPRegistry(_FakeRedis())
    sid = _register(registry)
    mcp = _Client([ToolDefinition(name="search_issues", description="Search", input_schema={})])
    resp = TestClient(_app(registry, mcp)).get(f"/connectors/{sid}/tools", headers=_A)
    assert resp.status_code == 200, resp.text
    assert resp.json() == [{"name": "search_issues", "description": "Search", "input_schema": {}}]
    assert mcp.calls == [(sid, _CTX_A.tenant_id)]


def test_list_tools_404_for_other_tenant_without_touching_mcp() -> None:
    registry = MCPRegistry(_FakeRedis())
    sid = _register(registry)
    mcp = _Client([])
    resp = TestClient(_app(registry, mcp)).get(f"/connectors/{sid}/tools", headers=_B)
    assert resp.status_code == 404
    assert mcp.calls == []


def test_list_tools_discovery_failure_is_502_not_empty() -> None:
    registry = MCPRegistry(_FakeRedis())
    sid = _register(registry)
    mcp = _Client(RuntimeError("upstream down"))
    resp = TestClient(_app(registry, mcp), raise_server_exceptions=False).get(
        f"/connectors/{sid}/tools", headers=_A
    )
    assert resp.status_code == 502


def test_list_tools_503_without_mcp_client() -> None:
    registry = MCPRegistry(_FakeRedis())
    sid = _register(registry)
    resp = TestClient(_app(registry), raise_server_exceptions=False).get(
        f"/connectors/{sid}/tools", headers=_A
    )
    assert resp.status_code == 503
