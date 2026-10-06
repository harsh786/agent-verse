"""Integration (TRG-50): POST /agents answers 201 only once its schedule row exists.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/api/test_agent_trigger_config_integration.py -m integration
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.agents import AgentStore
from app.api.agents import router as agents_router
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_schedule_row_exists_when_201_and_quota_counts_in_postgres(pg_url: str) -> None:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid.uuid4().hex
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, :id, :e, 'free', true)"
            ),
            {"id": tenant_id, "e": f"{secrets.token_hex(4)}@example.test"},
        )
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k")
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Any, call_next: Any) -> Any:
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(agents_router)
    app.state.agent_store = AgentStore(db_session_factory=factory)
    schedules = ScheduleStore(db_session_factory=factory)
    app.state.schedule_store = schedules
    interval = {"trigger_type": "interval", "interval_seconds": 3600}
    try:
        for _ in range(4):  # one below the free plan's PLAN_MAX_TRIGGERS (5)
            await schedules.create_async(
                goal_id="g",
                spec=TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=3600),
                tenant_ctx=ctx,
            )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                "/agents",
                json={"name": "fifth", "goal_template": "report", "trigger_config": interval},
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            async with factory() as session:
                agent_of_row = (
                    await session.execute(
                        text("SELECT agent_id FROM schedules WHERE id = :id AND tenant_id = :t"),
                        {"id": body["schedule_id"], "t": tenant_id},
                    )
                ).scalar_one()
            assert agent_of_row == body.get("agent_id", body.get("id"))

            over = await client.post(
                "/agents", json={"name": "one-too-many", "trigger_config": interval}
            )
            assert over.status_code == 403
            listed = (await client.get("/agents")).json()
            assert "one-too-many" not in [a["name"] for a in listed]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_put_replaces_the_agents_schedule_row_in_postgres(pg_url: str) -> None:
    """QA-15: editing the trigger replaces the schedule row; manual removes it."""
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid.uuid4().hex
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, :id, :e, 'professional', true)"
            ),
            {"id": tenant_id, "e": f"{secrets.token_hex(4)}@example.test"},
        )
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Any, call_next: Any) -> Any:
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(agents_router)
    app.state.agent_store = AgentStore(db_session_factory=factory)
    app.state.schedule_store = ScheduleStore(db_session_factory=factory)

    async def _rows(agent_id: str) -> list[tuple[str, str, str]]:
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT id, cron_expression, goal_id_template FROM schedules "
                    "WHERE agent_id = :a AND tenant_id = :t"
                ),
                {"a": agent_id, "t": tenant_id},
            )
            return [(str(r[0]), str(r[1]), str(r[2])) for r in result.fetchall()]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post(
                "/agents",
                json={
                    "name": "reporter",
                    "goal_template": "report A",
                    "trigger_config": {"trigger_type": "cron", "cron_expression": "0 9 * * 1-5"},
                },
            )
            assert resp.status_code == 201, resp.text
            agent_id = resp.json()["agent_id"]
            [(old_id, _, _)] = await _rows(agent_id)

            upd = await client.put(
                f"/agents/{agent_id}",
                json={
                    "goal_template": "report B",
                    "trigger_config": {"trigger_type": "cron", "cron_expression": "30 18 * * *"},
                },
            )
            assert upd.status_code == 200, upd.text
            [(new_id, cron, template)] = await _rows(agent_id)
            assert new_id != old_id
            assert (cron, template) == ("30 18 * * *", "report B")

            bad = await client.put(
                f"/agents/{agent_id}",
                json={"trigger_config": {"trigger_type": "cron", "cron_expression": "nope"}},
            )
            assert bad.status_code == 422
            assert await _rows(agent_id) == [(new_id, cron, template)]

            manual = await client.put(
                f"/agents/{agent_id}", json={"trigger_config": {"trigger_type": "manual"}}
            )
            assert manual.status_code == 200, manual.text
            assert await _rows(agent_id) == []
    finally:
        await engine.dispose()
