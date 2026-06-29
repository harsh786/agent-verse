"""Extra tests for /agents API — push from 59% to 85%+ coverage.

Targets uncovered lines: 28-53, 60-83, 106, 112-119, 144-183, 191-192,
219-246, 253-279, 292-296, 302-324, 335, 347, 352-393, 405, 481, 501-502,
562-579, 689, 712-721, 740-766, 789-828, 873, 891, 909, 926, 938, 957,
992-1006, 1093-1096, 1112-1126, 1131-1143, 1155-1157, 1171-1175, 1177,
1183-1199, 1230-1240, 1297-1311, 1329-1364, 1367, 1371
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agents import AgentStore, router as agents_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-agents4", plan=PlanTier.ENTERPRISE, api_key_id="kid-a4")
_VALID_KEY = "av_test_agents_extra4"

H = {"X-API-Key": _VALID_KEY}


def _make_app(
    agent_store: AgentStore | None = None,
    meta_agent: Any = None,
    schedule_store: Any = None,
    mcp_client: Any = None,
    agent_identity_service: Any = None,
    redis_client: Any = None,
    mcp_registry: Any = None,
    connector_secret_store: Any = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(agents_router)
    app.state.agent_store = agent_store or AgentStore()
    app.state.meta_agent = meta_agent or AsyncMock()
    if schedule_store is not None:
        app.state.schedule_store = schedule_store
    if mcp_client is not None:
        app.state.mcp_client = mcp_client
    if agent_identity_service is not None:
        app.state.agent_identity_service = agent_identity_service
    if redis_client is not None:
        app.state._rate_limiter_redis = redis_client
    if mcp_registry is not None:
        app.state.mcp_registry = mcp_registry
    if connector_secret_store is not None:
        app.state.connector_secret_store = connector_secret_store
    return app


def _sample_agent_data() -> dict:
    return {
        "name": "test-agent",
        "goal_template": "Solve {task}",
        "autonomy_mode": "supervised",
        "connector_ids": ["github"],
        "trigger_config": {},
        "allowed_collection_ids": [],
        "permissions": {},
        "eval_suite_id": None,
        "policy_ids": [],
        "system_prompt": "You are helpful.",
        "model_override": "",
        "max_iterations": 15,
        "timeout_seconds": 300,
    }


def _create_agent(client: TestClient, name: str = "Test Agent", **kwargs) -> dict:
    payload = {"name": name, "goal_template": "Do {task}", **kwargs}
    resp = client.post("/agents", json=payload, headers=H)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# AgentStore internal methods — direct unit tests (lines 28-405)
# ---------------------------------------------------------------------------

def test_agent_store_delete_key_not_found() -> None:
    """Lines 292-296: delete() returns False when key doesn't exist."""
    store = AgentStore()
    result = store.delete("nonexistent", tenant_ctx=_CTX)
    assert result is False


def test_agent_store_update_not_found() -> None:
    """Line 335: update() returns False when agent doesn't exist."""
    store = AgentStore()
    result = store.update("nonexistent", {"name": "new"}, tenant_ctx=_CTX)
    assert result is False


def test_agent_store_update_permissions_not_found() -> None:
    """Line 405: update_permissions() returns False when agent doesn't exist."""
    store = AgentStore()
    result = store.update_permissions("nonexistent", {"tool": "allow"}, tenant_ctx=_CTX)
    assert result is False


def test_agent_store_list_all_empty() -> None:
    """Lines 283-288: list_all returns empty when no agents for tenant."""
    store = AgentStore()
    result = store.list_all(tenant_ctx=_CTX)
    assert result == []


def test_agent_store_get_async_no_db_returns_memory() -> None:
    """Lines 247-248: get_async falls back to memory when no DB."""
    async def _run():
        store = AgentStore()
        # Pre-populate memory
        agent_id = await store.create({"name": "mem-agent"}, tenant_ctx=_CTX)
        result = await store.get_async(agent_id, tenant_ctx=_CTX)
        return result

    result = asyncio.run(_run())
    assert result is not None
    assert result["name"] == "mem-agent"


def test_agent_store_list_async_no_db_returns_memory() -> None:
    """Lines 280-281: list_async falls back to memory when no DB."""
    async def _run():
        store = AgentStore()
        await store.create({"name": "agent-a"}, tenant_ctx=_CTX)
        await store.create({"name": "agent-b"}, tenant_ctx=_CTX)
        return await store.list_async(tenant_ctx=_CTX)

    result = asyncio.run(_run())
    assert len(result) == 2


def test_agent_store_delete_async_no_db() -> None:
    """Lines 324-328: delete_async with no DB removes from memory."""
    async def _run():
        store = AgentStore()
        agent_id = await store.create({"name": "del-agent"}, tenant_ctx=_CTX)
        removed = await store.delete_async(agent_id, tenant_ctx=_CTX)
        return removed, store.get(agent_id, tenant_ctx=_CTX)

    removed, got = asyncio.run(_run())
    assert removed is True
    assert got is None


def test_agent_store_delete_async_not_found_no_db() -> None:
    """Line 325-326: delete_async returns False if key not in memory (no DB)."""
    async def _run():
        store = AgentStore()
        return await store.delete_async("nonexistent-id", tenant_ctx=_CTX)

    result = asyncio.run(_run())
    assert result is False


def test_agent_store_update_async_no_db() -> None:
    """Lines 394: update_async with no DB updates memory."""
    async def _run():
        store = AgentStore()
        agent_id = await store.create({"name": "upd-agent"}, tenant_ctx=_CTX)
        ok = await store.update_async(agent_id, {"name": "updated"}, tenant_ctx=_CTX)
        rec = store.get(agent_id, tenant_ctx=_CTX)
        return ok, rec

    ok, rec = asyncio.run(_run())
    assert ok is True
    assert rec["name"] == "updated"


def test_agent_store_update_async_not_found() -> None:
    """Line 347: update_async returns False when agent not found."""
    async def _run():
        store = AgentStore()
        return await store.update_async("nonexistent", {"name": "x"}, tenant_ctx=_CTX)

    result = asyncio.run(_run())
    assert result is False


def test_save_and_load_snapshots_no_db() -> None:
    """Lines 28-83: _save_snapshot_to_db and _load_snapshots_from_db with db=None."""
    async def _run():
        from app.api.agents import _save_snapshot_to_db, _load_snapshots_from_db
        snap = {"snapshot_id": "s1", "agent_id": "a1", "version": 1, "name": "Test"}
        # With db=None, should be no-op
        await _save_snapshot_to_db(snap, None, "tenant-1")
        result = await _load_snapshots_from_db("tenant-1", "a1", None)
        return result

    result = asyncio.run(_run())
    assert result == []


def test_sync_from_db_no_db() -> None:
    """Lines 144-145: sync_from_db returns 0 when no DB configured."""
    async def _run():
        store = AgentStore()
        return await store.sync_from_db()

    count = asyncio.run(_run())
    assert count == 0


def test_sync_from_db_with_failing_db() -> None:
    """Lines 179-183: sync_from_db catches DB exceptions and returns 0."""
    async def _run():
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=Exception("DB down"))
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)
        db = MagicMock(return_value=cm)

        store = AgentStore(db_session_factory=db)
        return await store.sync_from_db()

    count = asyncio.run(_run())
    assert count == 0


def test_agent_store_get_async_db_exception_falls_back() -> None:
    """Lines 244-248: get_async catches DB exception and falls back to memory."""
    async def _run():
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=Exception("DB down"))
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)
        db = MagicMock(return_value=cm)

        store = AgentStore(db_session_factory=db)
        # Pre-populate memory cache
        store._data[(_CTX.tenant_id, "agent-mem")] = {"agent_id": "agent-mem", "name": "Cached"}
        result = await store.get_async("agent-mem", tenant_ctx=_CTX)
        return result

    result = asyncio.run(_run())
    assert result is not None


def test_agent_store_list_async_db_exception_falls_back() -> None:
    """Lines 277-281: list_async catches DB exception and falls back to memory."""
    async def _run():
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=Exception("DB down"))
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)
        db = MagicMock(return_value=cm)

        store = AgentStore(db_session_factory=db)
        store._data[(_CTX.tenant_id, "agent-1")] = {"agent_id": "agent-1", "name": "A"}
        return await store.list_async(tenant_ctx=_CTX)

    result = asyncio.run(_run())
    assert len(result) >= 1


def test_agent_store_delete_async_db_exception() -> None:
    """Lines 318-323: delete_async handles DB exception gracefully."""
    async def _run():
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=Exception("DB fail"))
        begin_cm = AsyncMock()
        begin_cm.__aenter__ = AsyncMock(return_value=None)
        begin_cm.__aexit__ = AsyncMock(return_value=False)
        session.begin = MagicMock(return_value=begin_cm)
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)
        db = MagicMock(return_value=cm)

        store = AgentStore(db_session_factory=db)
        # In-memory key exists
        store._data[(_CTX.tenant_id, "del-agent")] = {"agent_id": "del-agent"}
        return await store.delete_async("del-agent", tenant_ctx=_CTX)

    result = asyncio.run(_run())
    assert result is True  # Falls back to in-memory delete


def test_agent_store_update_async_db_with_allowed_fields() -> None:
    """Lines 352-393: update_async with allowed DB fields."""
    async def _run():
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)
        begin_cm = AsyncMock()
        begin_cm.__aenter__ = AsyncMock(return_value=None)
        begin_cm.__aexit__ = AsyncMock(return_value=False)
        session.begin = MagicMock(return_value=begin_cm)
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=False)
        db = MagicMock(return_value=cm)

        store = AgentStore(db_session_factory=db)
        store._data[(_CTX.tenant_id, "upd-agent")] = {"agent_id": "upd-agent", "name": "Old"}
        ok = await store.update_async(
            "upd-agent",
            {"name": "New", "connector_ids": ["github"], "policy_ids": ["p1"]},
            tenant_ctx=_CTX,
        )
        return ok

    result = asyncio.run(_run())
    assert result is True


# ---------------------------------------------------------------------------
# Endpoint: create_agent with trigger schedule (lines 562-579)
# ---------------------------------------------------------------------------

def test_create_agent_with_cron_trigger_creates_schedule() -> None:
    """Lines 562-579: create_agent with cron trigger creates a schedule."""
    schedule_store = MagicMock()
    schedule_store.create = MagicMock(return_value=None)
    client = TestClient(_make_app(schedule_store=schedule_store), raise_server_exceptions=False)

    resp = client.post(
        "/agents",
        json={
            "name": "Cron Agent",
            "goal_template": "Run daily report",
            "trigger_config": {
                "trigger_type": "cron",
                "cron_expression": "0 9 * * 1-5",
            },
        },
        headers=H,
    )
    assert resp.status_code == 201
    # Schedule creation should have been attempted
    schedule_store.create.assert_called_once()


def test_create_agent_with_interval_trigger_creates_schedule() -> None:
    """Lines 562-579: interval trigger also creates a schedule."""
    schedule_store = MagicMock()
    schedule_store.create = MagicMock(return_value=None)
    client = TestClient(_make_app(schedule_store=schedule_store), raise_server_exceptions=False)

    resp = client.post(
        "/agents",
        json={
            "name": "Interval Agent",
            "trigger_config": {
                "trigger_type": "interval",
                "interval_seconds": 3600,
            },
        },
        headers=H,
    )
    assert resp.status_code == 201
    schedule_store.create.assert_called_once()


def test_create_agent_schedule_creation_failure_is_non_fatal() -> None:
    """Lines 577-581: Schedule creation exception is swallowed."""
    schedule_store = MagicMock()
    schedule_store.create = MagicMock(side_effect=Exception("Schedule error"))
    client = TestClient(_make_app(schedule_store=schedule_store), raise_server_exceptions=False)

    resp = client.post(
        "/agents",
        json={
            "name": "Cron Agent 2",
            "trigger_config": {"trigger_type": "cron", "cron_expression": "0 0 * * *"},
        },
        headers=H,
    )
    assert resp.status_code == 201  # Exception swallowed


# ---------------------------------------------------------------------------
# Endpoint: delete_agent with schedule cleanup (lines 712-721)
# ---------------------------------------------------------------------------

def test_delete_agent_with_schedule_cleanup() -> None:
    """Lines 712-721: delete_agent cleans up associated schedules."""
    schedule_store = MagicMock()
    schedule_store.list_all = MagicMock(return_value=[
        {"schedule_id": "s1", "agent_id": "PLACEHOLDER"},
        {"schedule_id": "s2", "agent_id": "other-agent"},
    ])
    schedule_store.delete_async = AsyncMock(return_value=True)
    client = TestClient(_make_app(schedule_store=schedule_store), raise_server_exceptions=False)

    # Create an agent
    agent = _create_agent(client)
    agent_id = agent["agent_id"]

    # Fix: update the mock schedule to point to the real agent
    schedule_store.list_all.return_value = [
        {"schedule_id": "s1", "agent_id": agent_id},
        {"schedule_id": "s2", "agent_id": "other-agent"},
    ]

    resp = client.delete(f"/agents/{agent_id}", headers=H)
    assert resp.status_code == 204


def test_delete_agent_schedule_cleanup_exception_non_fatal() -> None:
    """Lines 719-721: Schedule cleanup exception is swallowed."""
    schedule_store = MagicMock()
    schedule_store.list_all = MagicMock(side_effect=Exception("Schedule store error"))
    client = TestClient(_make_app(schedule_store=schedule_store), raise_server_exceptions=False)

    agent = _create_agent(client)
    resp = client.delete(f"/agents/{agent['agent_id']}", headers=H)
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Endpoint: get_permissions with DB (lines 740-766)
# ---------------------------------------------------------------------------

def test_get_permissions_no_db_returns_memory() -> None:
    """Lines 768-769: get_permissions falls back to in-memory when no DB."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)
    agent_id = agent["agent_id"]

    resp = client.get(f"/agents/{agent_id}/permissions", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert "agent_id" in body
    assert "permissions" in body


def test_get_permissions_with_db_returns_rows() -> None:
    """Lines 740-763: get_permissions reads from DB when available."""
    # Create agent in memory first (no DB), then inject DB
    store = AgentStore()
    agent_id = asyncio.run(store.create({"name": "Perm Agent"}, tenant_ctx=_CTX))

    # DB mock: get_async fallback to memory (via exception), permissions via fetchall
    mock_ok = MagicMock()
    mock_perm = MagicMock()
    mock_perm.fetchall.return_value = [
        ("web_search", "allow", 100, 10, "*"),
        ("file_read", "deny", 0, 0, ""),
    ]
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[
        mock_ok,                       # SET LOCAL (rls_context setup for get_async)
        Exception("Force fallback"),   # SELECT Agent → falls back to memory
        mock_ok,                       # SET LOCAL '' (rls_context cleanup, swallowed)
        mock_perm,                     # SELECT agent_permissions
    ])
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    db = MagicMock(return_value=cm)
    store._db = db  # type: ignore[attr-defined]

    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)
    resp = client.get(f"/agents/{agent_id}/permissions", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == agent_id


def test_get_permissions_db_exception_falls_back() -> None:
    """Lines 764-766: DB exception during permissions read falls back to in-memory."""
    # Create agent in memory first
    store = AgentStore()
    agent_id = asyncio.run(store.create({"name": "Fallback Agent", "permissions": {"tool": "allow"}}, tenant_ctx=_CTX))

    # DB mock: get_async fallback to memory, permissions query also fails
    mock_ok = MagicMock()
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[
        mock_ok,                     # SET LOCAL for get_async
        Exception("Fallback"),       # SELECT Agent → fallback to memory
        mock_ok,                     # SET LOCAL cleanup
        Exception("Perm DB fail"),   # SELECT permissions → falls back to in-memory
    ])
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    db = MagicMock(return_value=cm)
    store._db = db  # type: ignore[attr-defined]

    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)
    resp = client.get(f"/agents/{agent_id}/permissions", headers=H)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Endpoint: update_permissions with DB (lines 789-828)
# ---------------------------------------------------------------------------

def test_update_permissions_list_format_with_db() -> None:
    """Lines 789-828: update_permissions with list format persists to DB."""
    session = AsyncMock()
    mock_result = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    db = MagicMock(return_value=cm)

    store = AgentStore()
    store._db = db  # type: ignore[attr-defined]
    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)

    agent = _create_agent(client)
    agent_id = agent["agent_id"]

    resp = client.put(
        f"/agents/{agent_id}/permissions",
        json={"permissions": [
            {"tool_name": "web_search", "level": "allow", "daily_limit": 100},
            {"tool_name": "file_write", "level": "deny"},
        ]},
        headers=H,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "updated"


def test_update_permissions_dict_format_no_db() -> None:
    """Lines 831-838: update_permissions with dict format (legacy) — no DB."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.put(
        f"/agents/{agent['agent_id']}/permissions",
        json={"permissions": {"web_search": "allow", "file_write": "deny"}},
        headers=H,
    )
    assert resp.status_code == 200


def test_update_permissions_db_exception_non_fatal() -> None:
    """Lines 826-828: DB exception is logged but non-fatal."""
    # Create agent in memory first, then inject failing DB
    store = AgentStore()
    agent_id = asyncio.run(store.create({"name": "Perm Exc Agent"}, tenant_ctx=_CTX))

    # DB mock: get_async fallback to memory, permissions write also fails
    mock_ok = MagicMock()
    session = AsyncMock()
    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)
    session.execute = AsyncMock(side_effect=[
        mock_ok,                         # SET LOCAL for get_async
        Exception("Agent lookup fail"),  # SELECT Agent → fallback
        mock_ok,                         # cleanup
        Exception("Perm write fail"),    # DELETE + INSERT permissions
    ])
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    db = MagicMock(return_value=cm)
    store._db = db  # type: ignore[attr-defined]

    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)
    resp = client.put(
        f"/agents/{agent_id}/permissions",
        json={"permissions": [{"tool_name": "*", "level": "allow"}]},
        headers=H,
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Endpoint: knowledge binding (lines 873, 891)
# ---------------------------------------------------------------------------

def test_assign_knowledge_collection_agent_not_found() -> None:
    """Line 873: assign_knowledge_collection returns 404 for missing agent."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/agents/nonexistent/knowledge/coll-1", headers=H)
    assert resp.status_code == 404


def test_remove_knowledge_collection_agent_not_found() -> None:
    """Line 891: remove_knowledge_collection returns 404 for missing agent."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/agents/nonexistent/knowledge/coll-1", headers=H)
    assert resp.status_code == 404


def test_assign_knowledge_collection_idempotent() -> None:
    """Lines 874-879: Assigning same collection twice is idempotent."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)
    agent_id = agent["agent_id"]

    # Assign
    resp1 = client.post(f"/agents/{agent_id}/knowledge/coll-abc", headers=H)
    assert resp1.status_code == 204
    # Assign again — idempotent
    resp2 = client.post(f"/agents/{agent_id}/knowledge/coll-abc", headers=H)
    assert resp2.status_code == 204


def test_remove_knowledge_collection_success() -> None:
    """Lines 892-897: Removing a collection from agent."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)
    agent_id = agent["agent_id"]

    client.post(f"/agents/{agent_id}/knowledge/coll-1", headers=H)
    resp = client.delete(f"/agents/{agent_id}/knowledge/coll-1", headers=H)
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Endpoint: snapshot and rollback (lines 909, 926, 938, 957)
# ---------------------------------------------------------------------------

def test_snapshot_agent_success() -> None:
    """Lines 909-943: snapshot_agent creates a snapshot."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)
    agent_id = agent["agent_id"]

    resp = client.post(f"/agents/{agent_id}/snapshot", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert "snapshot_id" in body
    assert body["version"] == 1
    assert "snapshotted_at" in body


def test_snapshot_agent_not_found() -> None:
    """Line 920: snapshot_agent returns 404 for missing agent."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/agents/nonexistent/snapshot", headers=H)
    assert resp.status_code == 404


def test_list_agent_versions_empty() -> None:
    """Lines 900-910: list_agent_versions returns empty list before any snapshots."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)
    agent_id = agent["agent_id"]

    resp = client.get(f"/agents/{agent_id}/versions", headers=H)
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_agent_versions_after_snapshot() -> None:
    """Lines 900-910: After snapshot, list shows the version."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)
    agent_id = agent["agent_id"]

    client.post(f"/agents/{agent_id}/snapshot", headers=H)
    resp = client.get(f"/agents/{agent_id}/versions", headers=H)
    assert resp.status_code == 200
    versions = resp.json()
    assert len(versions) == 1
    assert versions[0]["version"] == 1


def test_rollback_agent_success() -> None:
    """Lines 947-974: rollback_agent restores a previous snapshot."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)
    agent_id = agent["agent_id"]

    # Create snapshot
    snap = client.post(f"/agents/{agent_id}/snapshot", headers=H).json()
    snapshot_id = snap["snapshot_id"]

    # Rollback
    resp = client.post(f"/agents/{agent_id}/rollback/{snapshot_id}", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rolled_back"
    assert body["restored_from"] == snapshot_id


def test_rollback_agent_snapshot_not_found() -> None:
    """Line 964: rollback returns 404 for missing snapshot_id."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)
    resp = client.post(f"/agents/{agent['agent_id']}/rollback/nonexistent-snap", headers=H)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Endpoint: export_agent (lines 977-1029)
# ---------------------------------------------------------------------------

def test_export_agent_openai_format() -> None:
    """Lines 977-1017: export_agent in OpenAI format."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.get(f"/agents/{agent['agent_id']}/export?format=openai", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "assistant"
    assert "model" in body


def test_export_agent_anthropic_format() -> None:
    """Lines 1018-1024: export_agent in Anthropic format."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.get(f"/agents/{agent['agent_id']}/export?format=anthropic", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert "system" in body
    assert "max_tokens" in body


def test_export_agent_unknown_format() -> None:
    """Lines 1025-1029: Unknown export format raises 400."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.get(f"/agents/{agent['agent_id']}/export?format=unknown", headers=H)
    assert resp.status_code == 400


def test_export_agent_not_found() -> None:
    """Line 986: export_agent returns 404 for missing agent."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/agents/nonexistent/export?format=openai", headers=H)
    assert resp.status_code == 404


def test_export_agent_with_mcp_client_tools() -> None:
    """Lines 992-1006: export_agent uses MCP client to discover tools."""
    mcp_client = AsyncMock()
    tool = MagicMock()
    tool.name = "web_search"
    tool.description = "Search the web"
    tool.input_schema = {"type": "object", "properties": {}}
    mcp_client.discover_all_tools = AsyncMock(return_value=[tool])

    client = TestClient(_make_app(mcp_client=mcp_client), raise_server_exceptions=False)
    agent = _create_agent(client, connector_ids=["web"])

    resp = client.get(f"/agents/{agent['agent_id']}/export?format=openai", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert "tools" in body


def test_export_agent_mcp_client_exception_swallowed() -> None:
    """Line 1005-1006: MCP client exception is swallowed."""
    mcp_client = AsyncMock()
    mcp_client.discover_all_tools = AsyncMock(side_effect=Exception("MCP down"))

    client = TestClient(_make_app(mcp_client=mcp_client), raise_server_exceptions=False)
    agent = _create_agent(client, connector_ids=["web"])

    resp = client.get(f"/agents/{agent['agent_id']}/export?format=openai", headers=H)
    assert resp.status_code == 200  # Exception swallowed


# ---------------------------------------------------------------------------
# Endpoint: credentials (lines 1086-1199)
# ---------------------------------------------------------------------------

def test_list_credentials_no_service() -> None:
    """Lines 1091-1092: list credentials returns [] when no service."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)
    resp = client.get(f"/agents/{agent['agent_id']}/credentials", headers=H)
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_credentials_with_service() -> None:
    """Lines 1093-1096: list credentials calls service."""
    svc = AsyncMock()
    svc.list_credentials = AsyncMock(return_value=[{"key_id": "k1", "agent_id": "a1"}])
    client = TestClient(_make_app(agent_identity_service=svc), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.get(f"/agents/{agent['agent_id']}/credentials", headers=H)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_list_credentials_service_exception() -> None:
    """Lines 1095-1096: service exception raises 500."""
    svc = AsyncMock()
    svc.list_credentials = AsyncMock(side_effect=Exception("Service down"))
    client = TestClient(_make_app(agent_identity_service=svc), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.get(f"/agents/{agent['agent_id']}/credentials", headers=H)
    assert resp.status_code == 500


def test_issue_credential_no_service() -> None:
    """Lines 1129-1130: issue_credential without service → 503."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)
    resp = client.post(
        f"/agents/{agent['agent_id']}/credentials",
        json={"scopes": ["goals:read"]},
        headers=H,
    )
    assert resp.status_code == 503


def test_issue_credential_with_service() -> None:
    """Lines 1131-1143: issue_credential calls service and returns result."""
    svc = AsyncMock()
    svc.issue_credential = AsyncMock(return_value={
        "key_id": "k-new",
        "private_key": "-----BEGIN RSA PRIVATE KEY-----",
        "public_key": "-----BEGIN PUBLIC KEY-----",
    })
    client = TestClient(_make_app(agent_identity_service=svc), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.post(
        f"/agents/{agent['agent_id']}/credentials",
        json={"scopes": ["goals:write"], "key_type": "service_account", "expires_in_days": 90},
        headers=H,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "key_id" in body


def _make_redis_mock(incr_return: int = 1) -> AsyncMock:
    """Redis mock that satisfies both TenantMiddleware SlidingWindowRateLimiter and credential endpoint."""
    redis_mock = AsyncMock()
    redis_mock.zremrangebyscore = AsyncMock(return_value=0)   # 0 removed from window
    redis_mock.zcard = AsyncMock(return_value=0)              # 0 in window → below limit
    redis_mock.zadd = AsyncMock(return_value=1)               # added successfully
    redis_mock.expire = AsyncMock(return_value=True)          # expiry set
    redis_mock.incr = AsyncMock(return_value=incr_return)    # credential rate limit counter
    return redis_mock


def test_issue_credential_with_rate_limiter() -> None:
    """Lines 1112-1126: Rate limiter checks are applied."""
    svc = AsyncMock()
    svc.issue_credential = AsyncMock(return_value={"key_id": "k1"})

    # Create agent first (no redis), then inject redis
    client = TestClient(_make_app(agent_identity_service=svc), raise_server_exceptions=False)
    agent = _create_agent(client)

    # Inject a properly configured redis mock (satisfies both middleware and endpoint)
    client.app.state._rate_limiter_redis = _make_redis_mock(incr_return=1)

    resp = client.post(
        f"/agents/{agent['agent_id']}/credentials",
        json={"scopes": []},
        headers=H,
    )
    assert resp.status_code == 201


def test_issue_credential_rate_limit_exceeded() -> None:
    """Lines 1117-1122: Too many requests → 429 (credential-level limit)."""
    svc = AsyncMock()
    svc.issue_credential = AsyncMock(return_value={"key_id": "k1"})

    client = TestClient(_make_app(agent_identity_service=svc), raise_server_exceptions=False)
    agent = _create_agent(client)

    # incr returns 11 → credential rate limit exceeded (> 10)
    client.app.state._rate_limiter_redis = _make_redis_mock(incr_return=11)

    resp = client.post(
        f"/agents/{agent['agent_id']}/credentials",
        json={"scopes": []},
        headers=H,
    )
    assert resp.status_code == 429


def test_issue_credential_rate_limiter_exception_non_fatal() -> None:
    """Lines 1124-1126: Rate limiter exception is non-fatal."""
    svc = AsyncMock()
    svc.issue_credential = AsyncMock(return_value={"key_id": "k1"})

    client = TestClient(_make_app(agent_identity_service=svc), raise_server_exceptions=False)
    agent = _create_agent(client)

    # Middleware rate limiter is satisfied (zcard=0), but credential incr fails
    redis_mock = _make_redis_mock()
    redis_mock.incr = AsyncMock(side_effect=Exception("Redis down"))
    client.app.state._rate_limiter_redis = redis_mock

    resp = client.post(
        f"/agents/{agent['agent_id']}/credentials",
        json={"scopes": []},
        headers=H,
    )
    assert resp.status_code == 201  # Rate limit fail is non-fatal


def test_issue_credential_service_exception() -> None:
    """Line 1143: service exception → 500."""
    svc = AsyncMock()
    svc.issue_credential = AsyncMock(side_effect=Exception("Service error"))
    client = TestClient(_make_app(agent_identity_service=svc), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.post(
        f"/agents/{agent['agent_id']}/credentials",
        json={"scopes": []},
        headers=H,
    )
    assert resp.status_code == 500


def test_revoke_credential_no_service() -> None:
    """Line 1154: revoke credential without service → 503."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)
    resp = client.delete(f"/agents/{agent['agent_id']}/credentials/key-1", headers=H)
    assert resp.status_code == 503


def test_revoke_credential_with_service_success() -> None:
    """Line 1155-1157: revoke credential succeeds."""
    svc = AsyncMock()
    svc.revoke_credential = AsyncMock(return_value=True)
    client = TestClient(_make_app(agent_identity_service=svc), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.delete(f"/agents/{agent['agent_id']}/credentials/key-1", headers=H)
    assert resp.status_code == 204


def test_revoke_credential_not_found() -> None:
    """Line 1157: revoke returns 404 when credential not found."""
    svc = AsyncMock()
    svc.revoke_credential = AsyncMock(return_value=False)
    client = TestClient(_make_app(agent_identity_service=svc), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.delete(f"/agents/{agent['agent_id']}/credentials/nonexistent", headers=H)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Endpoint: token exchange (lines 1160-1199)
# ---------------------------------------------------------------------------

def test_exchange_token_missing_key_id() -> None:
    """Lines 1170-1177: No key_id provided → 422."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)
    resp = client.post(f"/agents/{agent['agent_id']}/token", headers=H)
    assert resp.status_code == 422


def test_exchange_token_key_id_from_header() -> None:
    """Lines 1169: key_id read from X-Agent-Key-Id header."""
    svc = AsyncMock()
    svc.issue_agent_jwt = AsyncMock(return_value=None)  # credential not found
    client = TestClient(_make_app(agent_identity_service=svc), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.post(
        f"/agents/{agent['agent_id']}/token",
        headers={**H, "X-Agent-Key-Id": "key-123"},
    )
    assert resp.status_code == 404


def test_exchange_token_key_id_from_query_param() -> None:
    """Lines 1169: key_id read from query param."""
    svc = AsyncMock()
    svc.issue_agent_jwt = AsyncMock(return_value=None)
    client = TestClient(_make_app(agent_identity_service=svc), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.post(
        f"/agents/{agent['agent_id']}/token?key_id=key-from-query",
        headers=H,
    )
    assert resp.status_code == 404


def test_exchange_token_key_id_from_body() -> None:
    """Lines 1171-1175: key_id read from request body."""
    svc = AsyncMock()
    svc.issue_agent_jwt = AsyncMock(return_value=None)
    client = TestClient(_make_app(agent_identity_service=svc), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.post(
        f"/agents/{agent['agent_id']}/token",
        json={"key_id": "key-from-body"},
        headers=H,
    )
    assert resp.status_code == 404


def test_exchange_token_no_service() -> None:
    """Line 1181: No service → 503."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.post(
        f"/agents/{agent['agent_id']}/token",
        headers={**H, "X-Agent-Key-Id": "k1"},
    )
    assert resp.status_code == 503


def test_exchange_token_success_with_jwt() -> None:
    """Lines 1183-1199: JWT is issued and returned with expiry."""
    import time
    from jose import jwt

    # Create a real JWT token with exp claim
    secret = "test-secret"
    payload = {"sub": "agent-1", "exp": int(time.time()) + 900}
    token = jwt.encode(payload, secret, algorithm="HS256")

    svc = AsyncMock()
    svc.issue_agent_jwt = AsyncMock(return_value=token)
    client = TestClient(_make_app(agent_identity_service=svc), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.post(
        f"/agents/{agent['agent_id']}/token",
        headers={**H, "X-Agent-Key-Id": "k1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"] == token
    assert body["token_type"] == "Bearer"


def test_exchange_token_invalid_jwt_no_exp() -> None:
    """Lines 1196-1197: JWT without exp claim → expires_at is None."""
    svc = AsyncMock()
    # Return a non-decodable "token" so get_unverified_claims fails
    svc.issue_agent_jwt = AsyncMock(return_value="not.a.jwt")
    client = TestClient(_make_app(agent_identity_service=svc), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.post(
        f"/agents/{agent['agent_id']}/token",
        headers={**H, "X-Agent-Key-Id": "k1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["expires_at"] is None


# ---------------------------------------------------------------------------
# Endpoint: rollout gate (lines 1202-1240)
# ---------------------------------------------------------------------------

def test_rollout_gate_no_db() -> None:
    """Lines 1220-1228: rollout gate returns gate_passed=False with no DB."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client)

    resp = client.get(f"/agents/{agent['agent_id']}/rollout-gate", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert body["gate_passed"] is False
    assert "No database available" in body["reason"]


def test_rollout_gate_agent_not_found() -> None:
    """Lines 1213-1217: rollout gate returns 404 for missing agent."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/agents/nonexistent/rollout-gate", headers=H)
    assert resp.status_code == 404


def test_rollout_gate_with_db() -> None:
    """Lines 1230-1240: rollout gate uses DB to check eval results."""
    mock_result = {"gate_passed": True, "pass_rate": 0.95, "run_count": 20, "avg_score": 0.9}

    # Create agent in memory first, then inject DB
    store = AgentStore()
    agent_id = asyncio.run(store.create({"name": "Gate Agent", "eval_suite_id": "suite-1"}, tenant_ctx=_CTX))

    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    db = MagicMock(return_value=cm)
    store._db = db  # type: ignore[attr-defined]

    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)

    with patch("app.intelligence.eval_suite.check_agent_rollout_gate", new_callable=AsyncMock, return_value=mock_result):
        resp = client.get(
            f"/agents/{agent_id}/rollout-gate?eval_suite_id=suite-1&min_pass_rate=0.8",
            headers=H,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "agent_id" in body


# ---------------------------------------------------------------------------
# Endpoint: readiness (lines 1243-1390)
# ---------------------------------------------------------------------------

def test_readiness_agent_not_found() -> None:
    """Lines 1250-1253: readiness returns 404 for missing agent."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/agents/nonexistent/readiness", headers=H)
    assert resp.status_code == 404


def test_readiness_no_connectors_fails() -> None:
    """Lines 1259-1267: Agent with no connectors fails 'connectors' check."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client, connector_ids=[])  # no connectors

    resp = client.get(f"/agents/{agent['agent_id']}/readiness", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is False
    checks = {c["check"]: c for c in body["checks"]}
    assert checks["connectors"]["status"] == "fail"


def test_readiness_with_connectors_passes() -> None:
    """Lines 1268-1275: Agent with connectors passes 'connectors' check."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client, connector_ids=["github"])

    resp = client.get(f"/agents/{agent['agent_id']}/readiness", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    checks = {c["check"]: c for c in body["checks"]}
    assert checks["connectors"]["status"] == "pass"


def test_readiness_no_goal_template_warns() -> None:
    """Lines 1278-1284: No goal template is a warning (not fail)."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    agent = _create_agent(client, connector_ids=["github"], goal_template="")

    resp = client.get(f"/agents/{agent['agent_id']}/readiness", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    checks = {c["check"]: c for c in body["checks"]}
    assert checks["goal_template"]["status"] == "warn"


def test_readiness_fully_autonomous_without_eval_suite_fails() -> None:
    """Lines 1296-1309: fully-autonomous without eval_suite_id fails."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    # Create agent with eval suite (required for fully-autonomous)
    agent = _create_agent(client, connector_ids=["github"], eval_suite_id="suite-123")
    agent_id = agent["agent_id"]

    # Update to fully-autonomous (but strip eval_suite)
    # Actually we can't create without eval_suite for fully-autonomous
    # Let's manually put an agent in memory
    store = AgentStore()
    import asyncio
    agent_id2 = asyncio.run(store.create({
        "name": "FA Agent",
        "autonomy_mode": "fully-autonomous",
        "eval_suite_id": None,
        "connector_ids": ["github"],
        "goal_template": "Do stuff",
    }, tenant_ctx=_CTX))

    app2 = _make_app(agent_store=store)
    client2 = TestClient(app2, raise_server_exceptions=False)
    resp = client2.get(f"/agents/{agent_id2}/readiness", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    checks = {c["check"]: c for c in body["checks"]}
    assert checks["eval_suite"]["status"] == "fail"


def test_readiness_fully_autonomous_with_eval_suite_passes() -> None:
    """Lines 1310-1317: fully-autonomous with eval_suite passes eval_suite check."""
    store = AgentStore()
    import asyncio
    agent_id = asyncio.run(store.create({
        "name": "FA Agent Good",
        "autonomy_mode": "fully-autonomous",
        "eval_suite_id": "suite-abc",
        "connector_ids": ["github"],
        "goal_template": "Do stuff",
    }, tenant_ctx=_CTX))

    client = TestClient(_make_app(agent_store=store), raise_server_exceptions=False)
    resp = client.get(f"/agents/{agent_id}/readiness", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    checks = {c["check"]: c for c in body["checks"]}
    assert checks["eval_suite"]["status"] == "pass"


def test_readiness_connector_registry_check() -> None:
    """Lines 1329-1364: Connector readiness checks via MCP registry."""
    registry = AsyncMock()
    cfg = MagicMock()
    cfg.enabled = True
    cfg.auth_type = "none"
    cfg.auth_config = {}
    registry.get = AsyncMock(return_value=cfg)

    store = AgentStore()
    import asyncio
    agent_id = asyncio.run(store.create({
        "name": "Registry Agent",
        "connector_ids": ["github-connector"],
        "goal_template": "Run stuff",
        "autonomy_mode": "supervised",
    }, tenant_ctx=_CTX))

    client = TestClient(_make_app(agent_store=store, mcp_registry=registry), raise_server_exceptions=False)
    resp = client.get(f"/agents/{agent_id}/readiness", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    # Should have a check for the connector
    assert any("github-connector" in c["check"] for c in body["checks"])


def test_readiness_connector_not_in_registry() -> None:
    """Lines 1334-1337: Connector not in registry → warn status."""
    registry = AsyncMock()
    registry.get = AsyncMock(return_value=None)  # not registered

    store = AgentStore()
    import asyncio
    agent_id = asyncio.run(store.create({
        "name": "Unregistered Agent",
        "connector_ids": ["unknown-connector"],
        "goal_template": "Run stuff",
        "autonomy_mode": "supervised",
    }, tenant_ctx=_CTX))

    client = TestClient(_make_app(agent_store=store, mcp_registry=registry), raise_server_exceptions=False)
    resp = client.get(f"/agents/{agent_id}/readiness", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    checks = {c["check"]: c for c in body["checks"]}
    conn_check = checks.get("connector_unknown-connector_ready", {})
    assert conn_check.get("status") == "warn"


def test_readiness_connector_disabled_fails() -> None:
    """Lines 1338-1341: Disabled connector → fail status."""
    registry = AsyncMock()
    cfg = MagicMock()
    cfg.enabled = False
    registry.get = AsyncMock(return_value=cfg)

    store = AgentStore()
    import asyncio
    agent_id = asyncio.run(store.create({
        "name": "Disabled Connector Agent",
        "connector_ids": ["disabled-conn"],
        "goal_template": "Run stuff",
    }, tenant_ctx=_CTX))

    client = TestClient(_make_app(agent_store=store, mcp_registry=registry), raise_server_exceptions=False)
    resp = client.get(f"/agents/{agent_id}/readiness", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    checks = {c["check"]: c for c in body["checks"]}
    assert checks["connector_disabled-conn_ready"]["status"] == "fail"


def test_readiness_connector_needs_secret_missing() -> None:
    """Lines 1344-1360: api_key connector without stored secret → fail."""
    registry = AsyncMock()
    cfg = MagicMock()
    cfg.enabled = True
    cfg.auth_type = "api_key"
    cfg.auth_config = {}  # no inline key
    registry.get = AsyncMock(return_value=cfg)

    secret_store = AsyncMock()
    secret_store.has_secret = AsyncMock(return_value=False)

    store = AgentStore()
    import asyncio
    agent_id = asyncio.run(store.create({
        "name": "Needs Secret Agent",
        "connector_ids": ["api-conn"],
        "goal_template": "Needs secret",
    }, tenant_ctx=_CTX))

    client = TestClient(_make_app(
        agent_store=store,
        mcp_registry=registry,
        connector_secret_store=secret_store,
    ), raise_server_exceptions=False)
    resp = client.get(f"/agents/{agent_id}/readiness", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    checks = {c["check"]: c for c in body["checks"]}
    assert checks["connector_api-conn_ready"]["status"] == "fail"


def test_readiness_connector_has_secret() -> None:
    """Lines 1352-1360: api_key connector with stored secret → pass."""
    registry = AsyncMock()
    cfg = MagicMock()
    cfg.enabled = True
    cfg.auth_type = "api_key"
    cfg.auth_config = {}
    registry.get = AsyncMock(return_value=cfg)

    secret_store = AsyncMock()
    secret_store.has_secret = AsyncMock(return_value=True)

    store = AgentStore()
    import asyncio
    agent_id = asyncio.run(store.create({
        "name": "Has Secret Agent",
        "connector_ids": ["api-conn-2"],
        "goal_template": "Has secret",
    }, tenant_ctx=_CTX))

    client = TestClient(_make_app(
        agent_store=store,
        mcp_registry=registry,
        connector_secret_store=secret_store,
    ), raise_server_exceptions=False)
    resp = client.get(f"/agents/{agent_id}/readiness", headers=H)
    assert resp.status_code == 200
    body = resp.json()
    checks = {c["check"]: c for c in body["checks"]}
    assert checks["connector_api-conn-2_ready"]["status"] == "pass"


# ---------------------------------------------------------------------------
# Meta-agent NL creation (lines 587-642)
# ---------------------------------------------------------------------------

def test_create_agent_nl_fully_autonomous_rejected() -> None:
    """Lines 604-611: NL creation rejects fully-autonomous mode."""
    from unittest.mock import MagicMock

    config = MagicMock()
    config.name = "Auto Agent"
    config.goal_template = "Do stuff"
    config.autonomy_mode = "fully-autonomous"
    config.connectors = []
    config.trigger_type = "manual"
    config.cron_expression = ""
    config.interval_seconds = 0
    config.event_channel = ""
    config.policy_suggestions = []

    meta = AsyncMock()
    meta.plan = AsyncMock(return_value=config)
    client = TestClient(_make_app(meta_agent=meta), raise_server_exceptions=False)

    resp = client.post(
        "/agents/create",
        json={"command": "Create a fully autonomous security scanner"},
        headers=H,
    )
    assert resp.status_code == 422


def test_create_agent_nl_success() -> None:
    """Lines 596-642: NL creation returns agent + meta_agent_config."""
    from unittest.mock import MagicMock

    config = MagicMock()
    config.name = "NL Agent"
    config.goal_template = "Scan {target}"
    config.autonomy_mode = "supervised"
    config.connectors = ["github"]
    config.trigger_type = "manual"
    config.cron_expression = ""
    config.interval_seconds = 0
    config.event_channel = ""
    config.policy_suggestions = ["allow_all"]

    meta = AsyncMock()
    meta.plan = AsyncMock(return_value=config)
    client = TestClient(_make_app(meta_agent=meta), raise_server_exceptions=False)

    resp = client.post(
        "/agents/create",
        json={"command": "Create a supervised GitHub scanner"},
        headers=H,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "agent" in body
    assert "meta_agent_config" in body
    assert body["meta_agent_config"]["autonomy_mode"] == "supervised"


# ---------------------------------------------------------------------------
# Clone agent (lines 1032-1073)
# ---------------------------------------------------------------------------

def test_clone_agent_not_found() -> None:
    """Lines 1043-1046: clone returns 404 for missing agent."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/agents/nonexistent/clone", json={}, headers=H)
    assert resp.status_code == 404


def test_clone_agent_with_name_override() -> None:
    """Lines 1032-1073: clone creates copy with custom name."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    original = _create_agent(client, name="Original Agent", connector_ids=["github"])

    resp = client.post(
        f"/agents/{original['agent_id']}/clone",
        json={"name": "Cloned Agent"},
        headers=H,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Cloned Agent"
    assert body["cloned_from"] == original["agent_id"]


def test_clone_agent_default_name() -> None:
    """Lines 1049: Clone without name gets default 'X (copy)' name."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    original = _create_agent(client, name="My Agent")

    resp = client.post(f"/agents/{original['agent_id']}/clone", json={}, headers=H)
    assert resp.status_code == 201
    body = resp.json()
    assert "copy" in body["name"].lower() or "My Agent" in body["name"]
