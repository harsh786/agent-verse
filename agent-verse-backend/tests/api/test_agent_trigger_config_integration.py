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
