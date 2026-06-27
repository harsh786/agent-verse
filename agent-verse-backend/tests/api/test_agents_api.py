"""Tests for /agents API endpoints."""

from __future__ import annotations

import pytest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agents import AgentStore
from app.api.agents import router as agents_router
from app.intelligence.meta_agent import MetaAgentConfig
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-agents", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_agentskey"


def _make_app(
    agent_store: AgentStore | None = None,
    meta_agent: Any | None = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(agents_router)
    app.state.agent_store = agent_store or AgentStore()
    app.state.meta_agent = meta_agent or AsyncMock()
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_list_agents_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/agents", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_agent() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/agents",
        json={
            "name": "pr-reviewer",
            "goal_template": "Review PR {pr_url}",
            "autonomy_mode": "supervised",
            "connector_ids": ["github"],
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "pr-reviewer"
    assert body["goal_template"] == "Review PR {pr_url}"
    assert body["autonomy_mode"] == "supervised"
    assert "agent_id" in body
    assert "created_at" in body


async def test_db_backed_agent_store_persists_agent_record() -> None:
    added: list[Any] = []

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        def begin(self) -> _Session:
            return self

        async def execute(
            self, statement: object, params: dict[str, str] | None = None
        ) -> None:
            return None

        def add(self, obj: object) -> None:
            added.append(obj)

    def fake_db_factory() -> _Session:
        return _Session()

    store = AgentStore(db_session_factory=fake_db_factory)
    agent_id = await store.create(
        {
            "name": "db-agent",
            "goal_template": "Run {task}",
            "autonomy_mode": "supervised",
            "connector_ids": ["github"],
            "trigger_config": {"trigger_type": "manual"},
            "permissions": {},
        },
        tenant_ctx=_CTX,
    )

    assert len(added) == 1
    persisted = added[0]
    assert persisted.id == agent_id
    assert persisted.tenant_id == _CTX.tenant_id
    assert persisted.name == "db-agent"
    assert persisted.goal_template == "Run {task}"
    assert persisted.autonomy_mode == "supervised"
    assert persisted.connector_ids == ["github"]
    assert persisted.trigger_config == {"trigger_type": "manual"}


async def test_db_backed_agent_store_sync_from_db_loads_agent() -> None:
    persisted_agent = SimpleNamespace(
        id="agent-db-1",
        tenant_id=_CTX.tenant_id,
        name="hydrated-agent",
        goal_template="Handle {ticket}",
        autonomy_mode="bounded-autonomous",
        connector_ids=["github", "slack"],
        trigger_config={"trigger_type": "manual"},
        created_at=None,
    )
    persisted_tenant = SimpleNamespace(id=_CTX.tenant_id, is_active=True)

    class _Result:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def scalars(self) -> _Result:
            return self

        def all(self) -> list[Any]:
            return self._rows

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def execute(
            self, statement: object, params: dict[str, str] | None = None
        ) -> _Result:
            query = str(statement)
            if "FROM tenants" in query:
                return _Result([persisted_tenant])
            if "FROM agents" in query:
                return _Result([persisted_agent])
            return _Result([])

    def fake_db_factory() -> _Session:
        return _Session()

    store = AgentStore(db_session_factory=fake_db_factory)

    loaded = await store.sync_from_db()
    agent = store.get("agent-db-1", tenant_ctx=_CTX)

    assert loaded == 1
    # Verify core fields are present (new fields from _row_to_dict expansion are also present)
    assert agent is not None
    assert agent["agent_id"] == "agent-db-1"
    assert agent["tenant_id"] == _CTX.tenant_id
    assert agent["name"] == "hydrated-agent"
    assert agent["goal_template"] == "Handle {ticket}"
    assert agent["autonomy_mode"] == "bounded-autonomous"
    assert agent["connector_ids"] == ["github", "slack"]
    assert agent["trigger_config"] == {"trigger_type": "manual"}
    assert agent["permissions"] == {}
    assert agent["created_at"] == ""
    # New fields (defaults for SimpleNamespace without these attributes)
    assert agent["system_prompt"] == ""
    assert agent["model_override"] == ""
    assert agent["max_iterations"] == 15
    assert agent["timeout_seconds"] == 300
    assert agent["allowed_collection_ids"] == []
    assert agent["eval_suite_id"] is None
    assert agent["policy_ids"] == []
    assert agent["cloned_from"] is None


def test_create_agent_surfaces_db_persistence_failure() -> None:
    class _ErrorSession:
        async def __aenter__(self) -> _ErrorSession:
            msg = "DB unavailable"
            raise RuntimeError(msg)

        async def __aexit__(self, *args: object) -> None:
            pass

    def failing_db_factory() -> _ErrorSession:
        return _ErrorSession()

    store = AgentStore(db_session_factory=failing_db_factory)
    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)

    resp = client.post(
        "/agents",
        json={"name": "db-fails", "goal_template": "Run {task}"},
        headers={"X-API-Key": _VALID_KEY},
    )

    assert resp.status_code == 500
    assert resp.json() == {"detail": "Agent persistence failed"}
    assert store.list_all(tenant_ctx=_CTX) == []


def test_get_agent() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    # Create first, then get.
    create_resp = client.post(
        "/agents",
        json={"name": "monitor-agent", "goal_template": "Monitor {service}"},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = create_resp.json()["agent_id"]

    get_resp = client.get(f"/agents/{agent_id}", headers={"X-API-Key": _VALID_KEY})
    assert get_resp.status_code == 200
    assert get_resp.json()["agent_id"] == agent_id


def test_get_agent_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/agents/nonexistent-id", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


def test_delete_agent() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    create_resp = client.post(
        "/agents",
        json={"name": "delete-me", "goal_template": "Do something"},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = create_resp.json()["agent_id"]

    del_resp = client.delete(f"/agents/{agent_id}", headers={"X-API-Key": _VALID_KEY})
    assert del_resp.status_code == 204

    # Confirm it's gone.
    get_resp = client.get(f"/agents/{agent_id}", headers={"X-API-Key": _VALID_KEY})
    assert get_resp.status_code == 404


def test_get_permissions() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    create_resp = client.post(
        "/agents",
        json={"name": "perm-agent", "goal_template": "Run {task}"},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = create_resp.json()["agent_id"]

    perm_resp = client.get(
        f"/agents/{agent_id}/permissions", headers={"X-API-Key": _VALID_KEY}
    )
    assert perm_resp.status_code == 200
    body = perm_resp.json()
    assert body["agent_id"] == agent_id
    assert isinstance(body["permissions"], dict)


def test_update_permissions() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    create_resp = client.post(
        "/agents",
        json={"name": "perm-agent2", "goal_template": "Run {task}"},
        headers={"X-API-Key": _VALID_KEY},
    )
    agent_id = create_resp.json()["agent_id"]

    put_resp = client.put(
        f"/agents/{agent_id}/permissions",
        json={"permissions": {"write_file": "allow_log", "delete_repo": "deny"}},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert put_resp.status_code == 200
    body = put_resp.json()
    assert body["permissions"]["write_file"] == "allow_log"
    assert body["permissions"]["delete_repo"] == "deny"


def test_create_agent_via_meta_agent() -> None:
    mock_planner = AsyncMock()
    mock_planner.plan.return_value = MetaAgentConfig(
        name="github-pr-bot",
        goal_template="Review pull request {pr_id}",
        connectors=["github", "slack"],
        trigger_type="webhook",
        autonomy_mode="bounded-autonomous",
        policy_suggestions=["never delete branches"],
    )
    client = TestClient(_make_app(meta_agent=mock_planner), raise_server_exceptions=False)
    resp = client.post(
        "/agents/create",
        json={"command": "Create an agent to review GitHub PRs and post to Slack"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "agent" in body
    assert "meta_agent_config" in body
    assert body["meta_agent_config"]["name"] == "github-pr-bot"
    assert body["agent"]["name"] == "github-pr-bot"
    assert body["agent"]["autonomy_mode"] == "bounded-autonomous"
    assert "agent_id" in body["agent"]


def test_agents_require_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/agents")
    assert resp.status_code == 401


# ── W-3: Agent versioning / snapshot tests ────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_snapshot_and_list():
    """Can snapshot an agent and list versions."""
    from app.main import create_app
    from httpx import AsyncClient, ASGITransport
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "V", "email": "v@v.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]

        # Create an agent
        r2 = await c.post("/agents", json={"name": "TestAgent", "goal_template": "do stuff"})
        assert r2.status_code in (200, 201), r2.text
        agent_id = r2.json().get("agent_id")

        # Snapshot it
        r3 = await c.post(f"/agents/{agent_id}/snapshot")
        assert r3.status_code == 200
        snapshot_id = r3.json()["snapshot_id"]

        # List versions
        r4 = await c.get(f"/agents/{agent_id}/versions")
        assert r4.status_code == 200
        versions = r4.json()
        assert len(versions) >= 1
        assert any(v["snapshot_id"] == snapshot_id for v in versions)


@pytest.mark.asyncio
async def test_agent_rollback():
    """Can roll back agent to a snapshot."""
    from app.main import create_app
    from httpx import AsyncClient, ASGITransport
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "V2", "email": "v2@v.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]

        r2 = await c.post("/agents", json={"name": "RollbackAgent", "goal_template": "original"})
        agent_id = r2.json().get("agent_id")

        # Snapshot before change
        r3 = await c.post(f"/agents/{agent_id}/snapshot")
        snap_id = r3.json()["snapshot_id"]

        # Rollback
        r4 = await c.post(f"/agents/{agent_id}/rollback/{snap_id}")
        assert r4.status_code == 200
        assert r4.json()["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_agent_rollback_unknown_snapshot():
    from app.main import create_app
    from httpx import AsyncClient, ASGITransport
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "V3", "email": "v3@v.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        r2 = await c.post("/agents", json={"name": "A", "goal_template": "t"})
        agent_id = r2.json().get("agent_id")
        r3 = await c.post(f"/agents/{agent_id}/rollback/ghost-snap-id")
        assert r3.status_code == 404


# ── W-11: Agent export tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_export_agent_openai_format() -> None:
    from app.main import create_app
    from httpx import AsyncClient, ASGITransport
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "E", "email": "e@e.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        r2 = await c.post("/agents", json={"name": "ExportAgent", "goal_template": "You help with data"})
        agent_id = r2.json().get("agent_id")
        r3 = await c.get(f"/agents/{agent_id}/export?format=openai")
        assert r3.status_code == 200
        data = r3.json()
        assert data.get("object") == "assistant"
        assert "instructions" in data
        assert data["name"] == "ExportAgent"


@pytest.mark.asyncio
async def test_export_agent_anthropic_format() -> None:
    from app.main import create_app
    from httpx import AsyncClient, ASGITransport
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "EA", "email": "ea@e.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        r2 = await c.post("/agents", json={"name": "AnthropicExport", "goal_template": "You assist"})
        agent_id = r2.json().get("agent_id")
        r3 = await c.get(f"/agents/{agent_id}/export?format=anthropic")
        assert r3.status_code == 200
        data = r3.json()
        assert "system" in data
        assert "model" in data


@pytest.mark.asyncio
async def test_export_agent_unknown_format() -> None:
    from app.main import create_app
    from httpx import AsyncClient, ASGITransport
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "EF", "email": "ef@e.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        r2 = await c.post("/agents", json={"name": "A", "goal_template": "t"})
        agent_id = r2.json().get("agent_id")
        r3 = await c.get(f"/agents/{agent_id}/export?format=unknown")
        assert r3.status_code == 400
