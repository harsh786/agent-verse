"""MEM-16: prospective memory is created (API + agent tool), fired and purged.

The service and the planner's read-only block existed, but nothing created an
intention, process_due/purge had no Celery task, and the worker graph got no
prospective_service.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.memory.prospective import ProspectiveMemoryService
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

T = TenantContext(tenant_id="pm-tenant", plan=PlanTier.PROFESSIONAL, api_key_id="k")
KEY = "av_test_prospective"


async def _allow(*, content: str, **_kw: Any) -> dict[str, Any]:
    return {"blocked": "FORBIDDEN" in content}


@pytest.fixture(autouse=True)
def _guardrail() -> Any:
    with patch("app.guardrails_v2.engine.guardrails_engine.evaluate", side_effect=_allow):
        yield


@pytest.fixture
def service() -> Any:
    from app.memory import prospective_runtime

    svc = ProspectiveMemoryService()
    prospective_runtime.set_prospective_service(svc)
    yield svc
    prospective_runtime.set_prospective_service(None)


def _client(service: Any) -> TestClient:
    from app.api.memory import router

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return T if key == KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    app.state.prospective_memory_service = service
    app.state.db_session_factory = None
    return TestClient(app, raise_server_exceptions=False)


def test_api_create_list_cancel(service: Any) -> None:
    c = _client(service)
    h = {"X-API-Key": KEY}
    due = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    r = c.post("/memory/prospective", headers=h, json={"intention": "check the deploy", "due_at": due})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["state"] == "pending" and created["intention"] == "check the deploy"

    listed = c.get("/memory/prospective", headers=h).json()
    assert [i["id"] for i in listed] == [created["id"]]

    assert c.delete(f"/memory/prospective/{created['id']}", headers=h).status_code == 204
    assert c.get("/memory/prospective", headers=h).json() == []


def test_api_rejects_blocked_and_invalid_intentions(service: Any) -> None:
    c = _client(service)
    h = {"X-API-Key": KEY}
    due = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    blocked = c.post("/memory/prospective", headers=h,
                     json={"intention": "FORBIDDEN secret", "due_at": due})
    assert blocked.status_code == 422
    bad = c.post("/memory/prospective", headers=h,
                 json={"intention": "x", "due_at": due, "expires_at": due})
    assert bad.status_code == 422


async def test_agent_tool_defers_for_the_calling_tenant_only(service: Any) -> None:
    from app.mcp.client import MCPClient
    from app.mcp.registry import MCPServerConfig
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    # The handler exactly as the registry wires it (credential adapters applied).
    wired = next(c for c in get_builtin_server_configs() if c["server_id"] == "builtin-memory")
    cfg = MCPServerConfig(
        server_id=wired["server_id"],
        name=wired["name"],
        base_url="builtin://",
        tool_definitions=wired["tool_definitions"],
        builtin_handler=wired["handler"],
    )
    client = MCPClient(registry=AsyncMock(), secret_resolver=AsyncMock(return_value=None))
    result = await client._dispatch_builtin_tool(
        cfg, "defer_intention", {"intention": "re-run the report", "due_in_minutes": 5}, T
    )
    assert result.success, result.error
    items = await service.list_active(T.tenant_id, now=datetime.now(UTC))
    assert [i.intention for i in items] == ["re-run the report"]
    # No tenant -> refused.
    no_tenant = await client._dispatch_builtin_tool(cfg, "list_intentions", {}, None)
    assert not no_tenant.success


def test_builtin_memory_server_is_on_every_tenants_surface() -> None:
    from app.mcp.servers.registry_wiring import get_builtin_server_configs

    cfg = next(c for c in get_builtin_server_configs() if c["server_id"] == "builtin-memory")
    assert cfg["requires_env"] == []
    assert {t["name"] for t in cfg["tool_definitions"]} == {"defer_intention", "list_intentions"}


async def test_due_intention_fires_as_a_goal_and_is_marked_done(service: Any) -> None:
    from app.memory.prospective_runtime import create_intention, fire_due_intentions

    now = datetime.now(UTC)
    item = await create_intention(
        service, tenant_id=T.tenant_id, intention="send the weekly summary",
        due_at=now - timedelta(minutes=1), now=now - timedelta(minutes=2),
    )
    submitted: list[str] = []

    async def _submit(it: Any) -> dict[str, Any]:
        submitted.append(it.intention)
        return {"goal_id": "goal-123"}

    fired = await fire_due_intentions(service, tenant_id=T.tenant_id, submit=_submit, now=now)
    assert submitted == ["send the weekly summary"]
    assert [f.memory_id for f in fired] == [item.memory_id]
    done = await service.get(T.tenant_id, item.memory_id)
    assert done.state == "completed" and done.result == {"goal_id": "goal-123"}
    # Nothing fires twice.
    assert await fire_due_intentions(service, tenant_id=T.tenant_id, submit=_submit, now=now) == []


def test_celery_tasks_are_registered_and_scheduled() -> None:
    from app.scaling import tasks
    from app.scaling.celery_app import celery_app

    assert tasks.process_due_prospective_memories.name == (
        "agentverse.memory.process_due_prospective"
    )
    assert tasks.purge_expired_canonical_memories.name == (
        "agentverse.memory.purge_expired_canonical"
    )
    beat = {v["task"] for v in celery_app.conf.beat_schedule.values()}
    assert "agentverse.memory.process_due_prospective" in beat
    assert "agentverse.memory.purge_expired_canonical" in beat


def test_worker_memory_services_include_prospective() -> None:
    from app.memory.prospective_postgres import PostgresProspectiveMemoryService
    from app.memory.runtime_services import build_memory_graph_services

    services = build_memory_graph_services(lambda: None)
    assert isinstance(services["prospective_service"], PostgresProspectiveMemoryService)
