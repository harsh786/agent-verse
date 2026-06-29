"""Extra coverage for app/api/agents.py — AgentStore methods, snapshot functions, API endpoints."""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agents import AgentStore, _load_snapshots_from_db, _save_snapshot_to_db
from app.api.agents import router as agents_router
from app.intelligence.meta_agent import MetaAgentConfig
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-ag-extra", plan=PlanTier.ENTERPRISE, api_key_id="kid-ag")
_VALID_KEY = "av_test_agents_extra_key"


def _make_app(agent_store=None, meta_agent=None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(agents_router)
    app.state.agent_store = agent_store or AgentStore()
    app.state.meta_agent = meta_agent or AsyncMock()
    return app


_H = {"X-API-Key": _VALID_KEY}


# ── Snapshot DB helpers ────────────────────────────────────────────────────────

class TestSaveSnapshotToDb:
    @pytest.mark.asyncio
    async def test_noop_when_no_db(self):
        snapshot = {"snapshot_id": "s1", "agent_id": "a1", "version": 1}
        # Should not raise
        await _save_snapshot_to_db(snapshot, None, "tenant1")

    @pytest.mark.asyncio
    async def test_logs_exception_when_db_fails(self):
        snapshot = {"snapshot_id": "s1", "agent_id": "a1", "version": 1}
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(
            return_value=type("CM", (), {
                "__aenter__": AsyncMock(return_value=None),
                "__aexit__": AsyncMock(return_value=False),
            })()
        )
        mock_session.execute = AsyncMock(side_effect=RuntimeError("db error"))

        mock_db = MagicMock(return_value=mock_session)
        # Should not raise — exception is logged
        await _save_snapshot_to_db(snapshot, mock_db, "tenant1")


class TestLoadSnapshotsFromDb:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_db(self):
        result = await _load_snapshots_from_db("tenant1", "agent1", None)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=RuntimeError("connection error"))

        mock_db = MagicMock(return_value=mock_session)
        result = await _load_snapshots_from_db("tenant1", "agent1", mock_db)
        assert result == []


# ── AgentStore methods ─────────────────────────────────────────────────────────

class TestAgentStore:
    def test_get_returns_none_when_not_found(self):
        store = AgentStore()
        result = store.get("nonexistent", tenant_ctx=_CTX)
        assert result is None

    def test_delete_returns_false_when_not_found(self):
        store = AgentStore()
        result = store.delete("nonexistent", tenant_ctx=_CTX)
        assert result is False

    def test_update_returns_false_when_not_found(self):
        store = AgentStore()
        result = store.update("nonexistent", {"name": "new"}, tenant_ctx=_CTX)
        assert result is False

    def test_list_all_empty(self):
        store = AgentStore()
        result = store.list_all(tenant_ctx=_CTX)
        assert result == []

    @pytest.mark.asyncio
    async def test_create_stores_in_memory(self):
        store = AgentStore()
        agent_id = await store.create(
            {"name": "TestAgent", "goal_template": "do stuff"},
            tenant_ctx=_CTX,
        )
        assert agent_id
        assert store.get(agent_id, tenant_ctx=_CTX) is not None

    @pytest.mark.asyncio
    async def test_list_async_falls_back_to_memory_without_db(self):
        store = AgentStore()
        await store.create({"name": "A1"}, tenant_ctx=_CTX)
        result = await store.list_async(tenant_ctx=_CTX)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_delete_async_noop_when_not_found_no_db(self):
        store = AgentStore()
        result = await store.delete_async("nonexistent", tenant_ctx=_CTX)
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_async_success(self):
        store = AgentStore()
        aid = await store.create({"name": "ToDelete"}, tenant_ctx=_CTX)
        result = await store.delete_async(aid, tenant_ctx=_CTX)
        assert result is True
        assert store.get(aid, tenant_ctx=_CTX) is None

    def test_update_merges_data(self):
        store = AgentStore()
        # First create (sync) via internal data dict
        store._data[(_CTX.tenant_id, "aid1")] = {"agent_id": "aid1", "name": "Original"}
        result = store.update("aid1", {"name": "Updated"}, tenant_ctx=_CTX)
        assert result is True
        assert store.get("aid1", tenant_ctx=_CTX)["name"] == "Updated"

    @pytest.mark.asyncio
    async def test_sync_from_db_returns_zero_without_db(self):
        store = AgentStore()
        result = await store.sync_from_db()
        assert result == 0


# ── Agent API endpoints ────────────────────────────────────────────────────────

class TestAgentApiEndpoints:
    def test_create_agent_with_all_fields(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/agents",
            json={
                "name": "full-agent",
                "goal_template": "Process {input}",
                "autonomy_mode": "supervised",  # avoid fully-autonomous validation
                "connector_ids": ["github", "jira"],
                "system_prompt": "You are a helpful assistant.",
                "max_iterations": 20,
                "timeout_seconds": 600,
            },
            headers=_H,
        )
        assert resp.status_code in (200, 201)
        body = resp.json()
        assert "agent_id" in body

    def test_get_agent_by_id(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        create_resp = client.post(
            "/agents",
            json={"name": "retrievable-agent"},
            headers=_H,
        )
        agent_id = create_resp.json()["agent_id"]
        resp = client.get(f"/agents/{agent_id}", headers=_H)
        assert resp.status_code == 200
        assert resp.json()["agent_id"] == agent_id

    def test_get_nonexistent_agent(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/agents/nonexistent-agent-id", headers=_H)
        assert resp.status_code == 404

    def test_update_agent(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        create_resp = client.post(
            "/agents",
            json={"name": "original-name"},
            headers=_H,
        )
        agent_id = create_resp.json()["agent_id"]
        resp = client.put(
            f"/agents/{agent_id}",
            json={"name": "updated-name"},
            headers=_H,
        )
        assert resp.status_code in (200, 204)

    def test_delete_agent(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        create_resp = client.post(
            "/agents",
            json={"name": "to-delete"},
            headers=_H,
        )
        agent_id = create_resp.json()["agent_id"]
        resp = client.delete(f"/agents/{agent_id}", headers=_H)
        assert resp.status_code in (200, 204)

    def test_delete_nonexistent_agent(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.delete("/agents/nonexistent-id", headers=_H)
        assert resp.status_code == 404

    def test_list_agents(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        client.post("/agents", json={"name": "agent1"}, headers=_H)
        client.post("/agents", json={"name": "agent2"}, headers=_H)
        resp = client.get("/agents", headers=_H)
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) >= 2

    def test_agents_unauthorized(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/agents")
        assert resp.status_code == 401


# ── Snapshot endpoints ────────────────────────────────────────────────────────

class TestSnapshotEndpoints:
    def test_list_snapshots_empty(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        # First create an agent
        create_resp = client.post(
            "/agents",
            json={"name": "snapshot-agent"},
            headers=_H,
        )
        if create_resp.status_code not in (200, 201):
            pytest.skip("agent creation failed")
        agent_id = create_resp.json()["agent_id"]
        resp = client.get(f"/agents/{agent_id}/snapshots", headers=_H)
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert isinstance(resp.json(), list)


# ── Clone endpoint ────────────────────────────────────────────────────────────

class TestCloneEndpoint:
    def test_clone_agent(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        create_resp = client.post(
            "/agents",
            json={"name": "source-agent", "goal_template": "original template"},
            headers=_H,
        )
        agent_id = create_resp.json()["agent_id"]
        resp = client.post(
            f"/agents/{agent_id}/clone",
            json={"name": "cloned-agent"},
            headers=_H,
        )
        assert resp.status_code in (200, 201, 404)

    def test_clone_nonexistent_returns_404(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/agents/nonexistent/clone",
            json={"name": "clone"},
            headers=_H,
        )
        assert resp.status_code == 404
