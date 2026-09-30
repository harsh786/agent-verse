"""CORE-17: strategy evidence purge + per-strategy catalogue read on a migrated Postgres.

* Nothing deleted expired evidence rows, so the table grew forever.
* The tenant catalogue read was a single ``LIMIT 5000 ORDER BY observed_at`` —
  a hot strategy crowded quiet strategies out entirely.

Run with::

    DOCKER_HOST=... TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/integration/test_strategy_evidence_postgres.py -q --no-cov
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
import pytest
from sqlalchemy.engine import make_url

from app.orchestration.strategy_certification import StrategyEvidenceStore

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "sev_app_role"
APP_PASSWORD = "sev-app-role-password"
TENANT = uuid.uuid4().hex
NOW = datetime.now(UTC).replace(microsecond=0)


def _dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _seed(admin_url: str) -> None:
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
        await conn.execute(
            "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
            "VALUES ($1, 'T', $2, 'free', true)",
            TENANT,
            f"{TENANT}@example.test",
        )

        async def add(strategy: str, observed: datetime, expires: datetime) -> None:
            await conn.execute(
                "INSERT INTO strategy_certification_evidence (id, tenant_id, strategy_id, "
                "adapter_version, state_schema_version, evidence_type, result, "
                "artifact_reference, observed_at, expires_at, details) "
                "VALUES ($1, $2, $3, '1.0.0', 1, 'production_run', 'passed', 'goal:x', "
                "$4, $5, '{}'::jsonb)",
                uuid.uuid4().hex,
                TENANT,
                strategy,
                observed,
                expires,
            )

        # A hot strategy with many recent rows, a quiet one with two older rows.
        for i in range(30):
            await add("react", NOW - timedelta(minutes=i), NOW + timedelta(days=30))
        for i in range(2):
            await add("debate", NOW - timedelta(days=3, minutes=i), NOW + timedelta(days=20))
        # Expired rows the purge must remove.
        for i in range(5):
            await add("react", NOW - timedelta(days=40, minutes=i), NOW - timedelta(days=10))
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def pg() -> Iterator[tuple[str, str]]:
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
        asyncio.run(_seed(admin_url))
        app_url = (
            make_url(admin_url)
            .set(username=APP_ROLE, password=APP_PASSWORD)
            .render_as_string(hide_password=False)
        )
        yield app_url, admin_url


def _factory(url: str):  # type: ignore[no-untyped-def]
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    return async_sessionmaker(create_async_engine(url), expire_on_commit=False)


async def test_catalogue_read_keeps_quiet_strategies_and_purge_removes_expired(
    pg: tuple[str, str],
) -> None:
    app_url, admin_url = pg

    rows = await StrategyEvidenceStore(_factory(app_url)).list_current_for_tenant(
        tenant_id=TENANT, now=NOW, per_strategy=10
    )
    by_strategy: dict[str, int] = {}
    for row in rows:
        by_strategy[row["strategy_id"]] = by_strategy.get(row["strategy_id"], 0) + 1
    assert by_strategy == {"react": 10, "debate": 2}

    deleted = await StrategyEvidenceStore(_factory(admin_url)).purge_expired(
        now=NOW, batch_size=2
    )
    assert deleted == 5

    conn = await asyncpg.connect(_dsn(admin_url))
    try:
        remaining_expired = await conn.fetchval(
            "SELECT count(*) FROM strategy_certification_evidence WHERE expires_at <= $1", NOW
        )
        total = await conn.fetchval("SELECT count(*) FROM strategy_certification_evidence")
        indexes = {
            r["indexname"]
            for r in await conn.fetch(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'strategy_certification_evidence'"
            )
        }
    finally:
        await conn.close()
    assert remaining_expired == 0
    assert total == 32
    assert {"ix_strategy_evidence_recent", "ix_strategy_evidence_expires_at"} <= indexes


async def test_purge_on_the_application_role_fails_loudly(pg: tuple[str, str]) -> None:
    app_url, _ = pg
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):  # "query would be affected by row-level security"
        await StrategyEvidenceStore(_factory(app_url)).purge_expired(now=NOW)
