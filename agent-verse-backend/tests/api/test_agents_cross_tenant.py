"""Dedicated cross-tenant scope-leakage tests for AgentStore / the agents API.

The existing agents test suites (test_agents_comprehensive*.py, test_agents_extra*.py)
all exercise a single tenant context. None of them prove that an agent created
under tenant A is actually inaccessible to tenant B. This file plugs that gap:
direct ID lookup, update, delete, permissions, and list-leak scenarios.
"""

from __future__ import annotations

from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from app.api.agents import AgentStore
from app.api.agents import router as agents_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX_A = TenantContext(tenant_id="tid-cross-a", plan=PlanTier.ENTERPRISE, api_key_id="kid-a")
_CTX_B = TenantContext(tenant_id="tid-cross-b", plan=PlanTier.ENTERPRISE, api_key_id="kid-b")
_KEY_A = "av_cross_tenant_key_a"
_KEY_B = "av_cross_tenant_key_b"


def _make_app(agent_store: AgentStore | None = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        if key == _KEY_A:
            return _CTX_A
        if key == _KEY_B:
            return _CTX_B
        return None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(agents_router)
    app.state.agent_store = agent_store or AgentStore()
    return app


_H_A = {"X-API-Key": _KEY_A}
_H_B = {"X-API-Key": _KEY_B}


def _create_agent_for_a(client: TestClient) -> str:
    resp = client.post(
        "/agents",
        json={"name": "Tenant A Agent", "goal_template": "do things"},
        headers=_H_A,
    )
    assert resp.status_code == status.HTTP_201_CREATED, resp.text
    return resp.json()["agent_id"]


# ── AgentStore unit-level isolation ─────────────────────────────────────────────


class TestAgentStoreCrossTenantIsolation:
    async def test_get_by_id_from_other_tenant_returns_none(self) -> None:
        store = AgentStore()
        agent_id = await store.create({"name": "A's Agent"}, tenant_ctx=_CTX_A)

        # Owning tenant can see it.
        assert store.get(agent_id, tenant_ctx=_CTX_A) is not None
        # A different tenant, given the exact same agent_id, gets nothing.
        assert store.get(agent_id, tenant_ctx=_CTX_B) is None

    async def test_get_async_by_id_from_other_tenant_returns_none(self) -> None:
        store = AgentStore()
        agent_id = await store.create({"name": "A's Agent"}, tenant_ctx=_CTX_A)

        assert await store.get_async(agent_id, tenant_ctx=_CTX_A) is not None
        assert await store.get_async(agent_id, tenant_ctx=_CTX_B) is None

    async def test_list_all_does_not_include_other_tenants_agents(self) -> None:
        store = AgentStore()
        await store.create({"name": "A1"}, tenant_ctx=_CTX_A)
        await store.create({"name": "A2"}, tenant_ctx=_CTX_A)
        await store.create({"name": "B1"}, tenant_ctx=_CTX_B)

        a_agents = store.list_all(tenant_ctx=_CTX_A)
        b_agents = store.list_all(tenant_ctx=_CTX_B)

        assert {a["name"] for a in a_agents} == {"A1", "A2"}
        assert {b["name"] for b in b_agents} == {"B1"}

    async def test_list_async_does_not_include_other_tenants_agents(self) -> None:
        store = AgentStore()
        await store.create({"name": "A1"}, tenant_ctx=_CTX_A)
        await store.create({"name": "B1"}, tenant_ctx=_CTX_B)

        a_agents = await store.list_async(tenant_ctx=_CTX_A)
        assert {a["name"] for a in a_agents} == {"A1"}

    async def test_update_by_other_tenant_fails_and_does_not_mutate(self) -> None:
        store = AgentStore()
        agent_id = await store.create({"name": "Original"}, tenant_ctx=_CTX_A)

        ok = store.update(agent_id, {"name": "Hijacked"}, tenant_ctx=_CTX_B)
        assert ok is False
        assert store.get(agent_id, tenant_ctx=_CTX_A)["name"] == "Original"

    async def test_update_async_by_other_tenant_fails_and_does_not_mutate(self) -> None:
        store = AgentStore()
        agent_id = await store.create({"name": "Original"}, tenant_ctx=_CTX_A)

        ok = await store.update_async(agent_id, {"name": "Hijacked"}, tenant_ctx=_CTX_B)
        assert ok is False
        assert (await store.get_async(agent_id, tenant_ctx=_CTX_A))["name"] == "Original"

    async def test_delete_by_other_tenant_fails_and_agent_survives(self) -> None:
        store = AgentStore()
        agent_id = await store.create({"name": "Survivor"}, tenant_ctx=_CTX_A)

        deleted = store.delete(agent_id, tenant_ctx=_CTX_B)
        assert deleted is False
        assert store.get(agent_id, tenant_ctx=_CTX_A) is not None

    async def test_delete_async_by_other_tenant_fails_and_agent_survives(self) -> None:
        store = AgentStore()
        agent_id = await store.create({"name": "Survivor"}, tenant_ctx=_CTX_A)

        deleted = await store.delete_async(agent_id, tenant_ctx=_CTX_B)
        assert deleted is False
        assert await store.get_async(agent_id, tenant_ctx=_CTX_A) is not None

    async def test_count_async_is_isolated_per_tenant(self) -> None:
        store = AgentStore()
        await store.create({"name": "A1"}, tenant_ctx=_CTX_A)
        await store.create({"name": "A2"}, tenant_ctx=_CTX_A)
        await store.create({"name": "B1"}, tenant_ctx=_CTX_B)

        assert await store.count_async(tenant_ctx=_CTX_A) == 2
        assert await store.count_async(tenant_ctx=_CTX_B) == 1


# ── HTTP API-level isolation ─────────────────────────────────────────────────────


class TestAgentsApiCrossTenantIsolation:
    def test_get_agent_direct_id_lookup_from_other_tenant_is_404(self) -> None:
        app = _make_app()
        with TestClient(app) as client:
            agent_id = _create_agent_for_a(client)

            resp = client.get(f"/agents/{agent_id}", headers=_H_B)
            assert resp.status_code == status.HTTP_404_NOT_FOUND

            # Owner still sees it fine.
            resp_owner = client.get(f"/agents/{agent_id}", headers=_H_A)
            assert resp_owner.status_code == status.HTTP_200_OK

    def test_list_agents_does_not_leak_other_tenants_agents(self) -> None:
        app = _make_app()
        with TestClient(app) as client:
            _create_agent_for_a(client)
            resp_b = client.get("/agents", headers=_H_B)
            assert resp_b.status_code == status.HTTP_200_OK
            assert resp_b.json() == []

            resp_a = client.get("/agents", headers=_H_A)
            assert len(resp_a.json()) == 1

    def test_update_agent_from_other_tenant_is_404_and_no_mutation(self) -> None:
        app = _make_app()
        with TestClient(app) as client:
            agent_id = _create_agent_for_a(client)

            resp = client.put(
                f"/agents/{agent_id}",
                json={"name": "Hijacked By B"},
                headers=_H_B,
            )
            assert resp.status_code == status.HTTP_404_NOT_FOUND

            resp_owner = client.get(f"/agents/{agent_id}", headers=_H_A)
            assert resp_owner.json()["name"] == "Tenant A Agent"

    def test_delete_agent_from_other_tenant_is_404_and_agent_survives(self) -> None:
        app = _make_app()
        with TestClient(app) as client:
            agent_id = _create_agent_for_a(client)

            resp = client.delete(f"/agents/{agent_id}", headers=_H_B)
            assert resp.status_code == status.HTTP_404_NOT_FOUND

            resp_owner = client.get(f"/agents/{agent_id}", headers=_H_A)
            assert resp_owner.status_code == status.HTTP_200_OK

    def test_get_permissions_from_other_tenant_is_404(self) -> None:
        app = _make_app()
        with TestClient(app) as client:
            agent_id = _create_agent_for_a(client)

            resp = client.get(f"/agents/{agent_id}/permissions", headers=_H_B)
            assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_clone_agent_from_other_tenant_is_404(self) -> None:
        app = _make_app()
        with TestClient(app) as client:
            agent_id = _create_agent_for_a(client)

            resp = client.post(f"/agents/{agent_id}/clone", headers=_H_B)
            assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_export_agent_from_other_tenant_is_404(self) -> None:
        app = _make_app()
        with TestClient(app) as client:
            agent_id = _create_agent_for_a(client)

            resp = client.get(f"/agents/{agent_id}/export", headers=_H_B)
            assert resp.status_code == status.HTTP_404_NOT_FOUND
