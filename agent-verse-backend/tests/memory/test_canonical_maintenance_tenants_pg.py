"""MEM-39 (integration): canonical-maintenance tenant discovery pages the
tenants table and probes memory_records by index, never scanning it.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_canonical_maintenance_tenants_pg.py -q -m integration
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.memory.backfill_runner import (
    _MAINTENANCE_TENANT_PAGE_SQL,
    canonical_maintenance_tenant_pages,
)
from tests.memory._pg import alembic_upgrade

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

LEGACY, DONE, EMPTY = "m39-legacy", "m39-done", "m39-empty"
# 40k records spread over 400 tenants (realistic statistics: many tenants).
WITH_RECORDS = [f"m39-r{i:03d}" for i in range(400)]
PER_TENANT = 100


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        yield url


@pytest.fixture(scope="module")
async def system_factory(admin_url: str) -> Any:
    engine = create_async_engine(admin_url)  # superuser == the BYPASSRLS maintenance role
    async with engine.begin() as c:
        await c.execute(text("SET LOCAL row_security = off"))
        for t in (LEGACY, DONE, EMPTY):
            await c.execute(
                text("INSERT INTO tenants (id, name, email) VALUES (:t, :t, :e)"),
                {"t": t, "e": f"{t}@example.test"},
            )
        await c.execute(
            text(
                "INSERT INTO tenants (id, name, email) SELECT 'm39-r' || lpad(g::text, 3, '0'), "
                "'r' || g, 'r' || g || '@example.test' FROM generate_series(0, 399) g"
            )
        )
        # 200 more tenants with nothing, so the tenants side is not trivial.
        await c.execute(
            text(
                "INSERT INTO tenants (id, name, email) SELECT 'm39-x' || g, 'x' || g, "
                "'x' || g || '@example.test' FROM generate_series(1, 200) g"
            )
        )
        await c.execute(
            text(
                "INSERT INTO memory_records (id, tenant_id, memory_kind, content_ref, "
                "safe_summary, source_goal_id, source_execution_id, evidence_refs, "
                "classification, confidence, lifecycle_state, version, embedding_model, "
                "embedding_dimension, outcome_score, effectiveness_score, recall_count, "
                "helpful_count, harmful_count, retention_policy_id, idempotency_key, "
                "created_at, updated_at) SELECT 'm39-' || t || '-' || g, 'm39-r' || "
                "lpad(t::text, 3, '0'), 'reflexion', 'memory://x', "
                "'lesson ' || g, 'g', 'e', '[\"goal://g\"]', 'internal', 7000, 'active', 1, "
                "'memory-embedding-v1', 2048, 0, 0, 0, 0, 0, 'default', 'k' || g, now(), now() "
                f"FROM generate_series(0, 399) t, generate_series(1, {PER_TENANT}) g"
            )
        )
        for t in (LEGACY, DONE):
            await c.execute(
                text(
                    "INSERT INTO reflexion_lessons (id, tenant_id, lesson, source_goal_id, "
                    "failure_class) VALUES (:id, :t, 'retry later', 'g', 'timeout')"
                ),
                {"id": f"rl-{t}", "t": t},
            )
        await c.execute(
            text(
                "INSERT INTO memory_backfill_checkpoints (tenant_id, source_table, "
                "last_source_id, rows_processed, completed, updated_at) "
                "VALUES (:t, 'reflexion_lessons', 'rl', 1, true, now())"
            ),
            {"t": DONE},
        )
        await c.execute(text("ANALYZE"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_pages_name_only_tenants_with_work(system_factory: Any) -> None:
    pages = [p async for p in canonical_maintenance_tenant_pages(system_factory, page_size=50)]
    assert [t for p in pages for t in p] == sorted([*WITH_RECORDS, LEGACY])


async def test_tenant_discovery_never_scans_memory_records(system_factory: Any) -> None:
    async with system_factory() as s, s.begin():
        await s.execute(text("SET LOCAL row_security = off"))
        plan = "\n".join(
            r[0]
            for r in (
                await s.execute(
                    text("EXPLAIN " + str(_MAINTENANCE_TENANT_PAGE_SQL)),
                    {"after": "", "lim": 500},
                )
            ).fetchall()
        )
    assert "Seq Scan on memory_records" not in plan, plan
