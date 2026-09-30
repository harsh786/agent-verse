"""Integration: goal analytics run against real Postgres under RLS (ENT-01/02).

Proves the goal query (agent filter, completed_at duration, cost_ledger join)
is valid SQL on the migrated schema, runs as a NOBYPASSRLS role under the
tenant GUC, sees only the caller's tenant, and that an empty tenant is zeros.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/analytics/test_aggregator_postgres.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.analytics.aggregator import GoalAnalyticsAggregator

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TENANT_A = "tenant-an-a"
TENANT_B = "tenant-an-b"
TENANT_EMPTY = "tenant-an-empty"


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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def app_factory(postgres_url: str) -> AsyncIterator[async_sessionmaker]:  # type: ignore[type-arg]
    password = secrets.token_urlsafe(24)
    role = f"test_app_an_{secrets.token_hex(4)}"
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
        await conn.execute(text(f"GRANT SELECT ON goals, cost_ledger TO {role}"))
        for tid in (TENANT_A, TENANT_B, TENANT_EMPTY):
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :id, :email, 'free', true)"
                ),
                {"id": tid, "email": f"{tid}@example.test"},
            )
        for aid, tid in (("agent-a1", TENANT_A), ("agent-a2", TENANT_A), ("agent-b1", TENANT_B)):
            await conn.execute(
                text("INSERT INTO agents (id, tenant_id, name) VALUES (:i, :t, 'a')"),
                {"i": aid, "t": tid},
            )
        goals = [
            # id, tenant, agent, status, seconds from created to completed (None = open)
            ("ga1", TENANT_A, "agent-a1", "complete", 30),
            ("ga2", TENANT_A, "agent-a1", "failed", 10),
            ("ga3", TENANT_A, "agent-a2", "complete", None),
            ("gb1", TENANT_B, "agent-b1", "complete", 5),
            ("gb2", TENANT_B, "agent-b1", "complete", 5),
        ]
        for gid, tid, aid, status, secs in goals:
            await conn.execute(
                text(
                    "INSERT INTO goals (id, tenant_id, agent_id, goal_text, status, created_at, "
                    "completed_at) VALUES (:id, :t, :a, 'g', :s, NOW() - INTERVAL '1 hour', "
                    "CASE WHEN CAST(:secs AS integer) IS NULL THEN NULL ELSE "
                    "NOW() - INTERVAL '1 hour' + make_interval(secs => CAST(:secs AS integer)) "
                    "END)"
                ),
                {"id": gid, "t": tid, "a": aid, "s": status, "secs": secs},
            )
        for gid, tid, cost in (("ga1", TENANT_A, 0.5), ("ga2", TENANT_A, 1.5),
                               ("gb1", TENANT_B, 7.0)):
            await conn.execute(
                text(
                    "INSERT INTO cost_ledger (tenant_id, goal_id, model, cost_usd, cost_type) "
                    "VALUES (:t, :g, 'gpt-4o', :c, 'llm')"
                ),
                {"t": tid, "g": gid, "c": cost},
            )
    app_url = (
        make_url(postgres_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=2, max_overflow=0)
    yield async_sessionmaker(app_engine, expire_on_commit=False)
    await app_engine.dispose()
    await admin_engine.dispose()


async def test_goal_metrics_are_tenant_scoped_with_duration_and_cost(
    app_factory: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    agg = GoalAnalyticsAggregator(db=app_factory)
    m = await agg.goal_metrics(tenant_id=TENANT_A, days=30)
    assert (m.total, m.completed, m.failed) == (3, 2, 1)
    assert m.total_cost_usd == pytest.approx(2.0)
    assert m.avg_duration_s == pytest.approx(20.0, abs=0.5)


async def test_goal_metrics_honour_agent_id(
    app_factory: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    agg = GoalAnalyticsAggregator(db=app_factory)
    m = await agg.goal_metrics(tenant_id=TENANT_A, days=30, agent_id="agent-a2")
    assert (m.total, m.completed) == (1, 1)
    assert m.total_cost_usd == 0.0


async def test_empty_tenant_is_zero(
    app_factory: async_sessionmaker,  # type: ignore[type-arg]
) -> None:
    agg = GoalAnalyticsAggregator(db=app_factory)
    m = await agg.goal_metrics(tenant_id=TENANT_EMPTY, days=30)
    assert m.total == 0
    assert await agg.agent_metrics_db(tenant_id=TENANT_EMPTY, days=30) == []
    assert await agg.cost_trends_db(tenant_id=TENANT_EMPTY, days=30) == []
