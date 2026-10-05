"""GOAL-STRATEGIES e2e on real Postgres: a goal runs its pattern on a goal-linked session.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/coordination/test_goal_strategies_postgres.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.coordination.pattern_runs.goal_bridge import (
    build_pattern_state,
    build_worker_distributed_loop,
)
from app.governance.cost import CostController
from app.tenancy.context import PlanTier, TenantContext
from tests.coordination.goal_strategy_support import goal_profile, run_goal
from tests.coordination.pattern_run_support import ScriptedProvider

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TENANT_ID = "tenant-goal-pattern"
TENANT = TenantContext(
    tenant_id=TENANT_ID, plan=PlanTier.PROFESSIONAL, api_key_id="k", roles=("operator",)
)


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": admin_url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield admin_url


@pytest_asyncio.fixture
async def factories(postgres_url: str) -> AsyncIterator[tuple[Any, Any]]:
    password = secrets.token_urlsafe(24)
    role = f"test_app_goalpat_{secrets.token_hex(4)}"
    admin_engine = create_async_engine(postgres_url)
    async with admin_engine.begin() as conn:
        quoted = (
            await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})
        ).scalar_one()
        await conn.execute(
            text(
                f"CREATE ROLE {role} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {role}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        await conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
        )
        await conn.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, :id, :email, 'free', true) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": TENANT_ID, "email": f"{TENANT_ID}@example.test"},
        )
    app_url = (
        make_url(postgres_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=4, max_overflow=0)
    yield (
        async_sessionmaker(app_engine, expire_on_commit=False),
        async_sessionmaker(admin_engine, expire_on_commit=False),
    )
    await app_engine.dispose()
    await admin_engine.dispose()


@pytest.mark.asyncio
async def test_goal_runs_camel_on_a_goal_linked_postgres_session(factories: Any) -> None:
    app_factory, admin = factories
    provider = ScriptedProvider()
    state = build_pattern_state(db_factory=app_factory, provider=provider)
    result, events = await run_goal(
        state,
        "camel",
        goal="Draft the onboarding checklist",
        goal_id="goal-pg-camel",
        tenant_ctx=TENANT,
        provider=provider,
    )
    assert result.terminal_state == "succeeded", events[-1]
    assert result.answer == "CAMEL SOLUTION"
    session_id = next(e for e in events if e["type"] == "coordination_session")["session_id"]
    async with admin() as db:
        row = (
            await db.execute(
                text("SELECT goal_id, state, tenant_id FROM coordination_sessions WHERE id=:id"),
                {"id": session_id},
            )
        ).one()
        transcript = (
            await db.execute(
                text("SELECT count(*) FROM context_messages WHERE session_id=:id"),
                {"id": session_id},
            )
        ).scalar_one()
        events_count = (
            await db.execute(
                text("SELECT count(*) FROM coordination_events WHERE session_id=:id"),
                {"id": session_id},
            )
        ).scalar_one()
    assert (row.goal_id, row.state, row.tenant_id) == ("goal-pg-camel", "completed", TENANT_ID)
    assert transcript >= 2 and events_count >= 2
    runs = await state.camel_repository.list_session(TENANT_ID, session_id)
    assert runs[0].state["config"]["goal_id"] == "goal-pg-camel"


@pytest.mark.asyncio
async def test_worker_builds_a_pattern_loop_against_postgres(factories: Any) -> None:
    app_factory, _ = factories
    provider = ScriptedProvider()
    # The worker reserves budget before a distributed run and refuses one it cannot
    # verify (HITL-DISTRIBUTED-GATES / CORE-37), so it gets the cost controller the
    # Celery task passes: budgets resolved from budget_configs through the app role.
    cost = CostController()
    cost.set_budget_db(app_factory)
    loop = build_worker_distributed_loop(
        goal_profile("decentralized_swarm", "goal-pg-worker"),
        db_factory=app_factory,
        provider=provider,
        cost_controller=cost,
    )
    assert loop is not None
    events: list[dict[str, Any]] = []

    async def callback(event: dict[str, Any]) -> None:
        events.append(event)

    result = await loop.run(
        goal="Map the vendor landscape",
        tenant_ctx=TENANT,
        event_callback=callback,
        goal_id="goal-pg-worker",
    )
    assert result.terminal_state == "succeeded", events[-1]
    assert result.answer == "SWARM ANSWER"
    # supervisor/goal_tree/debate/voyager now also run on the worker's StrategyRunner
    # (HITL-DISTRIBUTED-GATES); a strategy outside it (react) still gets no loop.
    assert (
        build_worker_distributed_loop(
            goal_profile("react", "g"), db_factory=app_factory, provider=provider
        )
        is None
    )
