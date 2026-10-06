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


# ── a10-F239-01 / F239-03: tenant list + detail read Postgres, not one replica's memory ──


async def _admin_get(pg_url: str, path: str, **state: object) -> httpx.Response:
    import types

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(pg_url)
    app = FastAPI()
    app.include_router(admin.router)
    app.state.system_db_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    # This replica's in-memory copy knows none of the seeded tenants (they were
    # created "on another replica" after its startup sync).
    app.state.tenant_service = types.SimpleNamespace(
        _tenants={"stale-only": {"tenant_id": "stale-only", "plan": "enterprise"}}
    )
    for key, value in state.items():
        setattr(app.state, key, value)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            return await client.get(path, headers={"X-Admin-Key": "k"})
    finally:
        await engine.dispose()


async def _insert_tenant(pg_url: str, tid: str, name: str, plan: str, active: bool) -> None:
    dsn = make_url(pg_url).set(drivername="postgresql").render_as_string(hide_password=False)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
            "VALUES ($1, $2, $3, $4, $5)",
            tid,
            name,
            f"{tid}@example.test",
            plan,
            active,
        )
    finally:
        await conn.close()


async def test_tenant_list_and_detail_come_from_postgres(
    pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLATFORM_ADMIN_KEY", "k")
    tid = uuid.uuid4().hex
    await _insert_tenant(pg_url, tid, "Zebra Logistics", "professional", True)
    gone = uuid.uuid4().hex
    await _insert_tenant(pg_url, gone, "Zebra Archive", "free", False)

    resp = await _admin_get(pg_url, "/admin/tenants?search=zebra&limit=10")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["source"] == "postgres"
    assert data["total"] == 2
    by_id = {t["tenant_id"]: t for t in data["tenants"]}
    assert by_id[tid]["plan"] == "professional" and by_id[tid]["is_active"] is True
    assert by_id[gone]["is_active"] is False
    assert "stale-only" not in by_id

    everyone = (await _admin_get(pg_url, "/admin/tenants?limit=500")).json()
    assert everyone["total"] >= 5  # 3 usage tenants + the two above
    assert "stale-only" not in {t["tenant_id"] for t in everyone["tenants"]}

    paged = (await _admin_get(pg_url, "/admin/tenants?search=zebra&limit=1&offset=1")).json()
    assert paged["total"] == 2 and len(paged["tenants"]) == 1

    class _Tracker:
        async def get_budget_status(self, tenant_id: str) -> dict[str, float]:
            return {"daily_spent": 2.0 if tenant_id == tid else 0.0, "daily_limit": 50.0}

    detail = await _admin_get(pg_url, f"/admin/tenants/{tid}", cost_tracker=_Tracker())
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["plan"] == "professional" and body["name"] == "Zebra Logistics"
    assert body["usage"] == {"daily_spent": 2.0, "daily_limit": 50.0}

    missing = await _admin_get(pg_url, "/admin/tenants/stale-only")
    assert missing.status_code == 404  # memory-only tenants are not authoritative
