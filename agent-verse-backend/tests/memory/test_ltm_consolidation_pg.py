"""MEM-17 (integration): daily long-term-memory consolidation is tenant-scoped.

Under the NOBYPASSRLS application role the old task deleted nothing and reported
success; under a BYPASSRLS DSN it deleted across every tenant, ignoring legal
holds. Now: the tenant scan runs on the maintenance (BYPASSRLS) factory, every
DELETE runs per tenant under that tenant's RLS on the app role, held tenants and
held rows are skipped, per-tenant retention is honoured, and an error fails.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_ltm_consolidation_pg.py -q -m integration
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

A, B, HELD, ROWHOLD = "cons-a", "cons-b", "cons-held", "cons-rowhold"


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        yield url


async def _seed(admin_url: str) -> None:
    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        for t in (A, B, HELD, ROWHOLD):
            await c.execute(
                text("INSERT INTO tenants (id, name, email) VALUES (:t, :t, :e)"),
                {"t": t, "e": f"{t}@example.test"},
            )
        rows = []
        for t in (A, B, HELD, ROWHOLD):
            rows += [
                (f"{t}-d1", t, "dup fact", "now() - interval '2 days'"),
                (f"{t}-d2", t, "dup fact", "now()"),
                (f"{t}-old", t, "ancient fact", "now() - interval '400 days'"),
                (f"{t}-keep", t, "fresh fact", "now()"),
            ]
        for rid, t, content, ts in rows:
            await c.execute(
                text(
                    "INSERT INTO long_term_memory (id, tenant_id, content, memory_type, "
                    f"confidence, source_goal_id, tags, created_at) VALUES (:id, :t, :c, "
                    f"'domain_fact', 0.9, 'g', '[]', {ts})"
                ),
                {"id": rid, "t": t, "c": content},
            )
        await c.execute(
            text(
                "INSERT INTO legal_holds (id, tenant_id, name, resource_type, resource_ids, "
                "user_ids, status) VALUES "
                "('h1', :held, 'matter', 'tenant', '[]', '[]', 'active'), "
                "('h2', :rh, 'matter', 'memory', :rids, '[]', 'active')"
            ),
            {"held": HELD, "rh": ROWHOLD, "rids": f'["{ROWHOLD}-old", "{ROWHOLD}-d1"]'},
        )
        # Tenant B keeps memories for 1000 days.
        await c.execute(
            text(
                "INSERT INTO tenant_settings (tenant_id, settings) "
                "VALUES (:t, CAST(:s AS jsonb))"
            ),
            {"t": B, "s": '{"retention_days": 1000}'},
        )
    await eng.dispose()


async def _ids(admin_url: str) -> set[str]:
    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        await c.execute(text("SET LOCAL row_security = off"))
        rows = (await c.execute(text("SELECT id FROM long_term_memory"))).fetchall()
    await eng.dispose()
    return {r[0] for r in rows}


async def test_consolidation_is_per_tenant_and_respects_holds(admin_url: str) -> None:
    from app.memory.ltm_maintenance import consolidate_long_term_memory

    await _seed(admin_url)
    system_db = async_sessionmaker(create_async_engine(admin_url), expire_on_commit=False)
    app_engine = await app_role_engine(
        admin_url, ["long_term_memory", "legal_holds", "tenant_settings"]
    )

    result = await consolidate_long_term_memory(
        system_db=system_db, app_db=sessionmaker_for(app_engine), batch_size=1
    )

    left = await _ids(admin_url)
    # A: duplicate and expired rows removed, newest duplicate + fresh kept.
    assert {f"{A}-d2", f"{A}-keep"} <= left and not {f"{A}-d1", f"{A}-old"} & left
    # B: duplicate removed, 400-day row kept (tenant retention 1000 days).
    assert f"{B}-old" in left and f"{B}-d1" not in left
    # Tenant-wide hold: untouched.
    assert {f"{HELD}-d1", f"{HELD}-old"} <= left
    # Row holds: the held rows survive, the rest of the tenant is consolidated.
    assert {f"{ROWHOLD}-old", f"{ROWHOLD}-d1"} <= left
    assert result["duplicates_removed"] == 2 and result["expired_removed"] == 1
    assert result["tenants_skipped_legal_hold"] == 1
    await app_engine.dispose()


async def test_db_error_raises(admin_url: str) -> None:
    from app.memory.ltm_maintenance import consolidate_long_term_memory

    class _Broken:
        def __call__(self) -> object:
            raise ConnectionError("db down")

    with pytest.raises(ConnectionError):
        await consolidate_long_term_memory(system_db=_Broken(), app_db=_Broken())
