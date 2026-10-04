"""MEM-47 (integration): LTM dedup of 100k rows with 10% duplicates uses one
grouping query, keeps the newest copy, and reads through the md5 index.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_ltm_dedup_pg.py -q -m integration
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.memory import ltm_maintenance as ltm
from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

T = "mem47-tenant"
ROWS = 100_000
DUP_GROUPS = 5_000  # 5k groups x 3 copies = 10k duplicate rows (10%)


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        yield url


async def _seed(admin_url: str) -> None:
    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        await c.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:t, :t, :e)"),
            {"t": T, "e": f"{T}@example.test"},
        )
        await c.execute(text("SET LOCAL row_security = off"))
        # Unique rows (all recent so retention leaves them alone).
        await c.execute(
            text(
                "INSERT INTO long_term_memory (id, tenant_id, content, memory_type, confidence, "
                "source_goal_id, tags, created_at) SELECT 'u' || g, :t, 'unique fact ' || g, "
                "'domain_fact', 0.9, 'g', '[]', now() - interval '1 hour' "
                f"FROM generate_series(1, {ROWS - 3 * DUP_GROUPS}) g"
            ),
            {"t": T},
        )
        # Each duplicate group: copies 1..3, copy 3 is the newest.
        await c.execute(
            text(
                "INSERT INTO long_term_memory (id, tenant_id, content, memory_type, confidence, "
                "source_goal_id, tags, created_at) SELECT 'd' || g || '-' || k, :t, "
                "'repeated fact ' || g, 'domain_fact', 0.9, 'g', '[]', "
                "now() - (4 - k) * interval '1 minute' "
                f"FROM generate_series(1, {DUP_GROUPS}) g, generate_series(1, 3) k"
            ),
            {"t": T},
        )
        await c.execute(text("ANALYZE long_term_memory"))
    await eng.dispose()


async def test_dedup_100k_rows_keeps_the_newest_copy(admin_url: str) -> None:
    await _seed(admin_url)
    system_db = async_sessionmaker(create_async_engine(admin_url), expire_on_commit=False)
    app_engine = await app_role_engine(
        admin_url, ["long_term_memory", "legal_holds", "tenant_settings"]
    )
    calls: list[str] = []

    def _count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if "HAVING count(*) > 1" in statement:
            calls.append(statement)

    from sqlalchemy import event

    event.listen(app_engine.sync_engine, "before_cursor_execute", _count)
    result = await ltm.consolidate_long_term_memory(
        system_db=system_db, app_db=sessionmaker_for(app_engine), batch_size=500,
        max_batches=50,
    )
    assert len(calls) == 1  # one grouping query for the tenant
    assert result["duplicates_removed"] == 2 * DUP_GROUPS

    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        await c.execute(text("SET LOCAL row_security = off"))
        left = (await c.execute(text("SELECT count(*) FROM long_term_memory"))).scalar_one()
        kept = {
            r[0]
            for r in (
                await c.execute(
                    text("SELECT id FROM long_term_memory WHERE id LIKE 'd%' ORDER BY id")
                )
            ).fetchall()
        }
        # The grouping query CAN read through the md5 index (the planner may
        # still prefer a scan on a small table, hence enable_seqscan off).
        await c.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            r[0]
            for r in (
                await c.execute(
                    text("EXPLAIN " + ltm._DUP_GROUPS_SQL), {"tid": T, "max_groups": 25_000}
                )
            ).fetchall()
        )
    await eng.dispose()
    await app_engine.dispose()
    assert left == ROWS - 2 * DUP_GROUPS
    assert kept == {f"d{g}-3" for g in range(1, DUP_GROUPS + 1)}
    assert "ix_long_term_memory_tenant_content_md5" in plan, plan
