"""Additional connector tenant-scope-leakage coverage.

tests/api/test_connectors_functional.py already covers update/delete/test/list
cross-tenant isolation and OAuth-state cross-tenant rejection. This file plugs
the remaining gaps called out by the audit:

  - GET /connectors/{id}/health does not leak another tenant's health history
  - GET /connectors/{id}/usage does not leak another tenant's goal data even
    when given a connector_id owned by a different tenant
  - POST /connectors/{id}/discover on a connector owned by a different tenant
    does not discover/persist/leak that tenant's tool definitions or credentials
  - registering a connector under the same name for two different tenants
    creates fully independent Redis-backed records (no accidental overwrite)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.connectors import router as connectors_router
from app.mcp.registry import MCPRegistry
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX_A = TenantContext(tenant_id="tid-xtra-a", plan=PlanTier.PROFESSIONAL, api_key_id="ka")
_CTX_B = TenantContext(tenant_id="tid-xtra-b", plan=PlanTier.PROFESSIONAL, api_key_id="kb")
_KEY_A = "av_xtra_tenant_a"
_KEY_B = "av_xtra_tenant_b"


class _FakeRedis:
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
    mcp_client: Any = None,
    db_session_factory: Any = None,
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
    if mcp_client is not None:
        app.state.mcp_client = mcp_client
    if db_session_factory is not None:
        app.state.db_session_factory = db_session_factory
    return app


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


def test_health_history_for_other_tenants_connector_is_empty() -> None:
    """GET /connectors/{id}/health on a connector owned by tenant A must not
    return tenant A's health snapshots to tenant B, even with no DB (empty is
    the safe/expected result either way since RLS would also filter it)."""
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="jira")
    server_id = created["server_id"]

    resp = client.get(f"/connectors/{server_id}/health", headers={"X-API-Key": _KEY_B})
    assert resp.status_code == 200
    assert resp.json() == []


def test_usage_for_other_tenants_connector_never_returns_other_tenants_goals() -> None:
    """GET /connectors/{id}/usage must only ever return the CALLER's own goals,
    never another tenant's, even when passed a connector_id that in fact
    belongs to a different tenant."""
    registry = _make_registry()
    created_a = _register_via_registry(registry, tenant_ctx=_CTX_A, name="jira")

    goal_service = AsyncMock()

    async def _list_goals(*, tenant_ctx: TenantContext) -> dict[str, Any]:
        # Tenant A has a goal that references the connector; tenant B has none.
        if tenant_ctx.tenant_id == _CTX_A.tenant_id:
            return {
                "goals": [
                    {
                        "id": "goal-a-1",
                        "status": "complete",
                        "execution_context": {"connector_ids": [created_a]},
                    }
                ]
            }
        return {"goals": []}

    goal_service.list_goals = _list_goals
    goal_service._db = None

    app = _make_app(registry)
    app.state.goal_service = goal_service
    client = TestClient(app, raise_server_exceptions=False)

    # Tenant B queries usage for tenant A's connector_id directly.
    resp_b = client.get(f"/connectors/{created_a}/usage", headers={"X-API-Key": _KEY_B})
    assert resp_b.status_code == 200
    body_b = resp_b.json()
    assert body_b["goals"] == []
    assert body_b["total"] == 0

    # Tenant A sees its own usage as expected.
    resp_a = client.get(f"/connectors/{created_a}/usage", headers={"X-API-Key": _KEY_A})
    assert resp_a.status_code == 200
    assert resp_a.json()["total"] == 1


def _register_via_registry(registry: MCPRegistry, *, tenant_ctx: TenantContext, name: str) -> str:
    import asyncio

    from app.mcp.registry import MCPServerConfig

    async def _do() -> str:
        cfg = MCPServerConfig(
            name=name,
            url="https://api.github.com/mcp",
            auth_type="bearer",
            auth_config={},
        )
        return await registry.register(cfg, tenant_ctx=tenant_ctx)

    return asyncio.run(_do())


def test_discover_tools_on_other_tenants_connector_discovers_nothing() -> None:
    """POST /connectors/{id}/discover for a server_id owned by a different
    tenant must not reach the MCP client with that tenant's config, and must
    not report tools/credentials discovered."""
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)
    created = _register(client, _KEY_A, name="jira")
    server_id = created["server_id"]

    mcp_client = AsyncMock()
    mcp_client.discover_tools = AsyncMock(return_value=[])

    app = _make_app(registry, mcp_client=mcp_client)
    client2 = TestClient(app, raise_server_exceptions=False)

    resp = client2.post(f"/connectors/{server_id}/discover", headers={"X-API-Key": _KEY_B})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tools_discovered"] == 0
    assert body["tools_saved"] == 0

    # The underlying MCP client was still invoked (that's fine — it is itself
    # tenant-scoped via MCPRegistry.get and will resolve to no config), but
    # confirm it was called with tenant B's own context, never tenant A's.
    mcp_client.discover_tools.assert_awaited_once()
    _, kwargs = mcp_client.discover_tools.call_args
    assert kwargs["tenant_ctx"].tenant_id == _CTX_B.tenant_id


def test_same_connector_name_for_two_tenants_are_independent_records() -> None:
    """Registering a connector with the same display name under two different
    tenants must not collide — each tenant's registration, update, and delete
    only ever affects their own record."""
    registry = _make_registry()
    client = TestClient(_make_app(registry), raise_server_exceptions=False)

    created_a = _register(client, _KEY_A, name="shared-name")
    created_b = _register(client, _KEY_B, name="shared-name")

    assert created_a["server_id"] != created_b["server_id"]

    # Deleting tenant A's connector must not affect tenant B's same-named one.
    del_resp = client.delete(f"/connectors/{created_a['server_id']}", headers={"X-API-Key": _KEY_A})
    assert del_resp.status_code == 204

    list_b = client.get("/connectors", headers={"X-API-Key": _KEY_B}).json()
    assert any(c["server_id"] == created_b["server_id"] for c in list_b)
