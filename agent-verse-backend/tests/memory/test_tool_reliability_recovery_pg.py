"""MEM-45 (integration): decayed counters and blacklist expiry on real Postgres
under a NOBYPASSRLS role, including the backfill of a pre-existing row.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_tool_reliability_recovery_pg.py -q -m integration
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.memory.tool_reliability import BLACKLIST_TTL, ToolReliabilityStore
from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

TENANT = "mem45-tenant"
NOW = datetime.now(UTC)


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        # A pre-MEM-45 row: 10 lifetime failures 60 days ago, blacklisted then.
        alembic_upgrade(url, "b44e8f0a2d63")
        import asyncio

        async def _seed() -> None:
            eng = create_async_engine(url)
            async with eng.begin() as c:
                await c.execute(text("ALTER TABLE tool_reliability_memory NO FORCE ROW LEVEL SECURITY"))
                await c.execute(
                    text(
                        "INSERT INTO tool_reliability_memory (tenant_id, tool_name, success_count, "
                        "failure_count, total_latency_ms, last_used_at, blacklisted_at, "
                        "blacklist_reason) VALUES (:t, 'old.flaky', 0, 10, 100.0, :old, :old, "
                        "'blacklisted_by_self_improvement')"
                    ),
                    {"t": TENANT, "old": NOW - timedelta(days=60)},
                )
                await c.execute(text("ALTER TABLE tool_reliability_memory FORCE ROW LEVEL SECURITY"))
            await eng.dispose()

        asyncio.run(_seed())
        alembic_upgrade(url, "head")
        yield url


async def test_old_failures_decay_and_old_blacklist_lapses(admin_url: str) -> None:
    engine = await app_role_engine(admin_url, ["tool_reliability_memory"])
    store = ToolReliabilityStore(db_session_factory=sessionmaker_for(engine))
    before = await store.get_reliability(tenant_id=TENANT, tool_name="old.flaky", now=NOW)
    assert before["blacklisted"] is False  # backfilled expiry (+7 days) has passed
    for _ in range(5):
        await store.record(tenant_id=TENANT, tool_name="old.flaky", success=True, now=NOW)
    after = await store.get_reliability(tenant_id=TENANT, tool_name="old.flaky", now=NOW)
    assert after["failure_count"] == 10 and after["success_count"] == 5
    assert after["recent_success_rate"] > 0.9
    assert after["unreliable"] is False
    await engine.dispose()


async def test_blacklist_expires_and_can_be_cleared(admin_url: str) -> None:
    engine = await app_role_engine(admin_url, ["tool_reliability_memory"])
    store = ToolReliabilityStore(db_session_factory=sessionmaker_for(engine))
    await store.blacklist(tenant_id=TENANT, tool_name="new.flaky", reason="loops", now=NOW)
    live = await store.get_reliability(tenant_id=TENANT, tool_name="new.flaky", now=NOW)
    assert live["blacklisted"] and live["unreliable"]
    lapsed = await store.get_reliability(
        tenant_id=TENANT, tool_name="new.flaky", now=NOW + BLACKLIST_TTL + timedelta(seconds=1)
    )
    assert not lapsed["blacklisted"]
    assert await store.clear_blacklist(tenant_id=TENANT, tool_name="new.flaky") is True
    assert await store.clear_blacklist(tenant_id=TENANT, tool_name="new.flaky") is False
    await engine.dispose()
