"""``/insights`` estimate / agent-health / query / benchmarks on a real, migrated Postgres.

Regression: the SQL read goal columns for cost, duration and a vector that do not
exist (and evaluation score columns that never existed); every query raised on
real Postgres and the error was swallowed into made-up defaults. This runs the
real statements after ``alembic upgrade head`` — tenant endpoints as a NOBYPASSRLS
application role, benchmarks on the (superuser) maintenance role — proving the
SQL is valid against the deployed schema and tenant-isolated.

Run with::

    DOCKER_HOST=... TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/integration/test_insights_postgres.py -q --no-cov
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.engine import make_url

from app.api import insights

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "ins_app_role"
APP_PASSWORD = "ins-app-role-password"
TENANT_A = uuid.uuid4().hex
TENANT_B = uuid.uuid4().hex
AGENT_A = uuid.uuid4().hex
NOW = datetime.now(UTC).replace(microsecond=0)


def _dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _provision_and_seed(admin_url: str) -> None:
    conn = await asyncpg.connect(_dsn(admin_url))
    try:
        await conn.execute(
            f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
        )
        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
        await conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
        )
        for tenant in (TENANT_A, TENANT_B):
            await conn.execute(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES ($1, $2, $3, 'free', true)",
                tenant,
                f"Tenant {tenant}",
                f"{tenant}@example.test",
            )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, is_active) VALUES ($1, $2, 'a', true)",
            AGENT_A,
            TENANT_A,
        )
        created = NOW - timedelta(hours=1)
        # (tenant, agent, text, status, seconds, iterations, cost)
        goals = [
            (TENANT_A, AGENT_A, "deploy the billing service", "complete", 30, 2, 0.02),
            (TENANT_A, AGENT_A, "deploy the billing service now", "complete", 90, 4, 0.06),
            (TENANT_A, AGENT_A, "deploy the billing services", "failed", 60, 6, None),
            (TENANT_A, None, "summarise quarterly marketing report", "complete", 5, 1, 0.5),
            # tenant B: must never leak into A's numbers
            (TENANT_B, None, "deploy the billing service", "failed", 999, 9, 9.0),
        ]
        for tenant, agent, text_, status, secs, iters, cost in goals:
            gid = uuid.uuid4().hex
            await conn.execute(
                "INSERT INTO goals (id, tenant_id, agent_id, goal_text, status, iterations, "
                "created_at, completed_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                gid,
                tenant,
                agent,
                text_,
                status,
                iters,
                created,
                created + timedelta(seconds=secs),
            )
            if cost is not None:
                await conn.execute(
                    "INSERT INTO goal_cost_breakdowns (tenant_id, goal_id, role, model, "
                    "input_tokens, output_tokens, cost_usd, calls) "
                    "VALUES ($1::uuid, $2, 'executor', 'm', 10, 0, $3, 1)",
                    tenant,
                    gid,
                    cost,
                )
            if agent is not None:
                await conn.execute(
                    "INSERT INTO evaluations (id, goal_id, tenant_id, scores, average_score, "
                    "passed, strategy_execution_id) "
                    "VALUES ($1, $2, $3, CAST($4 AS json), 0.8, true, $5)",
                    uuid.uuid4().hex,
                    gid,
                    tenant,
                    json.dumps({"accuracy": 0.9, "coherence": 0.6}),
                    f"x:{gid}",
                )
                for seq, tool in enumerate(("search", "fetch"), start=1):
                    await conn.execute(
                        "INSERT INTO goal_events (id, tenant_id, goal_id, sequence, "
                        "event_type, payload) VALUES ($1, $2, $3, $4, 'tool_call_complete', "
                        "CAST($5 AS jsonb))",
                        uuid.uuid4().hex,
                        tenant,
                        gid,
                        seq,
                        json.dumps({"type": "tool_call_complete", "tool": tool}),
                    )
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def ins_postgres() -> Iterator[tuple[str, str]]:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

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
        asyncio.run(_provision_and_seed(admin_url))
        app_url = (
            make_url(admin_url)
            .set(username=APP_ROLE, password=APP_PASSWORD)
            .render_as_string(hide_password=False)
        )
        yield app_url, admin_url


@pytest_asyncio.fixture
async def client_for(ins_postgres: tuple[str, str]) -> AsyncIterator[Any]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    app_url, admin_url = ins_postgres
    engine = create_async_engine(app_url)
    sys_engine = create_async_engine(admin_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sys_factory = async_sessionmaker(sys_engine, expire_on_commit=False)
    clients: list[httpx.AsyncClient] = []

    def _make(tenant_id: str) -> httpx.AsyncClient:
        app = FastAPI()
        app.include_router(insights.router)
        app.state.db_session_factory = factory
        app.state.system_db_session_factory = sys_factory

        @app.middleware("http")
        async def inject_tenant(request, call_next):  # type: ignore[no-untyped-def]
            request.state.tenant = SimpleNamespace(tenant_id=tenant_id)
            return await call_next(request)

        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        clients.append(client)
        return client

    yield _make
    for c in clients:
        await c.aclose()
    await engine.dispose()
    await sys_engine.dispose()


async def test_estimate_uses_similar_tenant_goals(client_for: Any) -> None:
    resp = await client_for(TENANT_A).post(
        "/insights/estimate", json={"goal": "deploy the billing service"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["similar_goals_count"] == 3  # not the marketing goal, not tenant B's
    assert data["success_probability"] == round(2 / 3, 3)
    assert data["estimated_cost_usd"] == {"min": 0.02, "mean": 0.04, "max": 0.06}
    assert data["estimated_duration_s"] == {"min": 30, "mean": 60, "max": 90}


async def test_estimate_without_similar_history_is_null(client_for: Any) -> None:
    data = (
        await client_for(TENANT_A).post("/insights/estimate", json={"goal": "zzqx wvvk"})
    ).json()
    assert data["similar_goals_count"] == 0
    assert data["success_probability"] is None


async def test_agent_health_on_real_schema(client_for: Any) -> None:
    resp = await client_for(TENANT_A).get(f"/insights/agent-health/{AGENT_A}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    h = data["health"]
    assert data["sample_size"] == 3
    assert data["eval_sample_size"] == 3
    assert h["success_rate"] == round(2 / 3, 3)
    assert h["speed"] == 0.5  # mean successful run 60 s
    assert h["cost_efficiency"] == 0.556  # mean goal cost $0.04 -> 1 / (1 + 0.8)
    assert h["accuracy"] == 0.9
    assert h["coherence"] == 0.6
    assert h["tool_coverage"] == 0.2


async def test_agent_health_is_tenant_isolated(client_for: Any) -> None:
    data = (await client_for(TENANT_B).get(f"/insights/agent-health/{AGENT_A}")).json()
    assert data["sample_size"] == 0
    assert all(v is None for v in data["health"].values())


async def test_query_reads_breakdown_costs(client_for: Any) -> None:
    resp = await client_for(TENANT_A).post(
        "/insights/query", json={"query": "complete goals cost more than $0.1"}
    )
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert [r["cost_usd"] for r in results] == [0.5]


async def test_benchmarks_run_on_real_schema(client_for: Any) -> None:
    resp = await client_for(TENANT_A).get("/insights/benchmarks")
    assert resp.status_code == 200, resp.text
    # Two tenants, five goals: below the anonymity threshold -> honest nulls.
    assert resp.json()["data_source"] == "insufficient_data"
