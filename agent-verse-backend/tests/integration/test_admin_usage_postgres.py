"""``/admin/usage`` against a real, migrated Postgres on the maintenance role.

Regression: it read ``GoalService._active_goals`` (does not exist -> always 0).
This proves the replacement statements are valid on ``alembic upgrade head`` and
count every tenant's goals through ``system_session``.

Run with::

    DOCKER_HOST=... TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/integration/test_admin_usage_postgres.py -q --no-cov
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.engine import make_url

from app.api import admin

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.now(UTC).replace(microsecond=0)


async def _seed(admin_url: str) -> None:
    dsn = make_url(admin_url).set(drivername="postgresql").render_as_string(hide_password=False)
    conn = await asyncpg.connect(dsn)
    try:
        tenants = [uuid.uuid4().hex for _ in range(3)]
        for t in tenants:
            await conn.execute(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES ($1, $1, $2, 'free', true)",
                t,
                f"{t}@example.test",
            )
        rows = [
            (tenants[0], "executing", NOW, None),
            (tenants[1], "waiting_human", NOW, None),
            (tenants[2], "complete", NOW, NOW + timedelta(seconds=4)),
            (tenants[2], "failed", NOW - timedelta(days=3), NOW - timedelta(days=3)),
        ]
        for tid, status, created, completed in rows:
            await conn.execute(
                "INSERT INTO goals (id, tenant_id, goal_text, status, created_at, completed_at) "
                "VALUES ($1, $2, 'g', $3, $4, $5)",
                uuid.uuid4().hex,
                tid,
                status,
                created,
                completed,
            )
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def pg_url() -> Iterator[str]:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": url},
            check=True,
            capture_output=True,
            text=True,
        )
        asyncio.run(_seed(url))
        yield url


async def test_usage_counts_every_tenant(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    monkeypatch.setenv("PLATFORM_ADMIN_KEY", "k")
    engine = create_async_engine(pg_url)
    app = FastAPI()
    app.include_router(admin.router)
    app.state.system_db_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            resp = await client.get("/admin/usage", headers={"X-Admin-Key": "k"})
    finally:
        await engine.dispose()
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["active_goals"] == 2
    assert data["total_goals"] == 4
    assert data["total_tenants"] >= 3
    assert data["completed_today"] == 1
    assert data["avg_latency_ms"] == 4000
    assert data["goals_by_status"]["failed"] == 1
