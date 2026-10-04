"""RV-02 on real Postgres under a NOBYPASSRLS role: both routing paths see the
tenant's durable agents, and only that tenant's.

API path: the router built by create_app over the in-memory store is moved to
the DB-backed store by the lifespan's swap step (``_bind_agent_router``).
Worker path: the Celery worker's GoalService (``_build_worker_goal_service``,
no app state) routes over a DB-backed AgentStore.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/agent/test_router_rv02_rls_pg.py -q -m integration --no-cov
"""

from __future__ import annotations

import types
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.agent.router import AgentRouter
from app.api.agents import AgentStore
from app.tenancy.context import PlanTier, TenantContext
from tests.memory._pg import app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration]

GOAL = "reconcile the vendor invoices with stripe payments"


def _ctx(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")


async def _seed(pg_url: str, tenant_id: str, agents: list[tuple[str, str, str]]) -> None:
    engine = create_async_engine(pg_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id}
            )
            await conn.execute(
                text("INSERT INTO tenants (id, name, email) VALUES (:t, 'rv02', :e)"),
                {"t": tenant_id, "e": f"{tenant_id}@rv02.local"},
            )
            for agent_id, name, template in agents:
                await conn.execute(
                    text(
                        "INSERT INTO agents (id, tenant_id, name, goal_template, connector_ids) "
                        "VALUES (:i, :t, :n, :g, '[\"builtin-stripe\"]')"
                    ),
                    {"i": agent_id, "t": tenant_id, "n": name, "g": template},
                )
    finally:
        await engine.dispose()


async def test_api_and_worker_route_to_the_tenants_db_agent_under_rls(
    pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.db.session as session_mod
    from app.main import _bind_agent_router
    from app.scaling.tasks import _build_worker_goal_service

    tenant_a, tenant_b, tenant_empty = (uuid.uuid4().hex for _ in range(3))
    agent_a, agent_b = uuid.uuid4().hex, uuid.uuid4().hex
    await _seed(
        pg_url,
        tenant_a,
        [
            (agent_a, "Invoice reconciler", "reconcile vendor invoices against payments"),
            (uuid.uuid4().hex, "Release notes writer", "draft release notes"),
        ],
    )
    # Tenant B's agent matches the goal even better; RLS must hide it from A.
    await _seed(
        pg_url,
        tenant_b,
        [(agent_b, "Stripe invoice reconciler", "reconcile the vendor invoices with stripe")],
    )
    await _seed(pg_url, tenant_empty, [])

    engine = await app_role_engine(pg_url, ["agents", "goals", "evaluations", "tenants"])
    factory = sessionmaker_for(engine)
    try:
        # ── API path: create_app's router, then the lifespan's swap step ──────
        router = AgentRouter(agent_store=AgentStore())
        state: Any = types.SimpleNamespace(agent_router=router)
        assert (await router.route(GOAL, _ctx(tenant_a))).reason == "no_agents"
        _bind_agent_router(state, AgentStore(db_session_factory=factory), factory)

        decision = await router.route(GOAL, _ctx(tenant_a))
        assert decision.agent_id == agent_a, decision.to_dict()
        assert agent_b not in {s.agent_id for s in decision.all_scores}
        assert (await router.route(GOAL, _ctx(tenant_b))).agent_id == agent_b
        assert (await router.route(GOAL, _ctx(tenant_empty))).reason == "no_agents"

        # ── Worker path: the Celery worker's GoalService ─────────────────────
        monkeypatch.setattr(session_mod, "get_session_factory", lambda: factory)
        goal_service, db_factory = _build_worker_goal_service()
        assert goal_service is not None and db_factory is factory

        agent_id, outcome = await goal_service._auto_route_goal(GOAL, _ctx(tenant_a))
        assert agent_id == agent_a
        assert outcome is not None and outcome["reason"] == "routed"

        agent_id, outcome = await goal_service._auto_route_goal(GOAL, _ctx(tenant_empty))
        assert agent_id is None
        assert outcome is not None and outcome["reason"] == "no_agents"
    finally:
        await engine.dispose()
        admin = create_async_engine(pg_url)
        async with admin.begin() as conn:
            for tid in (tenant_a, tenant_b, tenant_empty):
                await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tid})
                await conn.execute(text("DELETE FROM agents WHERE tenant_id = :t"), {"t": tid})
                await conn.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tid})
        await admin.dispose()
