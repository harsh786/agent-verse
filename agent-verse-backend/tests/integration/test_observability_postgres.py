"""``/observability/metrics`` + ``/timeseries`` against a real, migrated Postgres.

Regression: the endpoints queried ``goals.duration_s`` / ``goals.cost_usd``, which
do not exist — every query raised ``UndefinedColumn`` on real Postgres and a bare
``except: pass`` served zeros. This runs the real statements on ``alembic upgrade
head`` as a NOBYPASSRLS application role, so it proves both that the SQL is valid
against the deployed schema and that each tenant only sees its own rows.

Run with::

    DOCKER_HOST=... TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/integration/test_observability_postgres.py -q --no-cov
"""

from __future__ import annotations

import asyncio
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

from app.api import observability as obs

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "obs_app_role"
APP_PASSWORD = "obs-app-role-password"
TENANT_A = uuid.uuid4().hex
TENANT_B = uuid.uuid4().hex
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
        created = NOW - timedelta(hours=1)
        goals = [
            # tenant A: finished in 2 s and 4 s, one still running
            (TENANT_A, "complete", created, created + timedelta(seconds=2)),
            (TENANT_A, "failed", created, created + timedelta(seconds=4)),
            (TENANT_A, "executing", created, None),
            # tenant B: must never leak into A's numbers
            (TENANT_B, "complete", created, created + timedelta(seconds=100)),
        ]
        for tenant, status, created_at, completed_at in goals:
            await conn.execute(
                "INSERT INTO goals (id, tenant_id, goal_text, status, created_at, completed_at) "
                "VALUES ($1, $2, 'g', $3, $4, $5)",
                uuid.uuid4().hex,
                tenant,
                status,
                created_at,
                completed_at,
            )
        for tenant, model, tokens, cost in (
            (TENANT_A, "model-a", 300, 0.25),
            (TENANT_B, "model-b", 9_000, 9.0),
        ):
            await conn.execute(
                "INSERT INTO goal_cost_breakdowns (tenant_id, goal_id, role, model, "
                "input_tokens, output_tokens, cost_usd, calls, first_recorded_at, updated_at) "
                "VALUES ($1::uuid, $2, 'executor', $3, $4, 0, $5, 1, $6, $6)",
                tenant,
                uuid.uuid4().hex,
                model,
                tokens,
                cost,
                created,
            )
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def obs_postgres() -> Iterator[str]:
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
        yield (
            make_url(admin_url)
            .set(username=APP_ROLE, password=APP_PASSWORD)
            .render_as_string(hide_password=False)
        )


@pytest_asyncio.fixture
async def client_for(obs_postgres: str) -> AsyncIterator[Any]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(obs_postgres)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    clients: list[httpx.AsyncClient] = []

    def _make(tenant_id: str) -> httpx.AsyncClient:
        app = FastAPI()
        app.include_router(obs.router)
        app.state.db_session_factory = factory

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


async def test_metrics_run_on_real_schema_and_are_tenant_isolated(client_for: Any) -> None:
    resp = await client_for(TENANT_A).get("/observability/metrics")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_goals"] == 3
    assert data["success_rate"] == 0.5  # 1 complete of 2 finished
    assert data["latency_percentiles"]["p50"] == 3000  # median of 2 s and 4 s
    assert data["token_usage_by_provider"] == [{"label": "model-a", "value": 300}]


async def test_timeseries_run_on_real_schema_and_are_tenant_isolated(client_for: Any) -> None:
    resp = await client_for(TENANT_A).get("/observability/timeseries?bucket=day")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert sum(b["count"] for b in data["goals_per_hour"]) == 3
    assert sum(b["failed"] for b in data["goals_per_hour"]) == 1
    assert [b["cost_usd"] for b in data["cost_per_hour"]] == [0.25]
    assert data["avg_latency_per_hour"][0]["p50_ms"] == 3000


async def test_other_tenant_sees_only_its_own_rows(client_for: Any) -> None:
    data = (await client_for(TENANT_B).get("/observability/metrics")).json()
    assert data["total_goals"] == 1
    assert data["latency_percentiles"]["p50"] == 100_000
    assert data["token_usage_by_provider"] == [{"label": "model-b", "value": 9000}]
