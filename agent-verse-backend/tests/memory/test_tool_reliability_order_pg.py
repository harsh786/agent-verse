"""MEM-46 (integration): listing ranks in SQL BEFORE the LIMIT, and the
unreliable query filters by the threshold in SQL — a large tenant's one
unreliable tool is never cut off by an arbitrary LIMIT window.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_tool_reliability_order_pg.py -q -m integration
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.memory.tool_reliability import ToolReliabilityStore
from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

TENANT = "mem46-tenant"
NOW = datetime.now(UTC)


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        yield url


@pytest.fixture(scope="module")
async def store(admin_url: str) -> Any:
    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        await c.execute(text("ALTER TABLE tool_reliability_memory NO FORCE ROW LEVEL SECURITY"))
        insert = text(
            "INSERT INTO tool_reliability_memory (tenant_id, tool_name, success_count, "
            "failure_count, total_latency_ms, last_used_at, recent_success, recent_failure, "
            "decayed_at) VALUES (:t, :n, :s, :f, 0, :now, :rs, :rf, :now)"
        )

        def _p(name: str, s: int, f: int, now: datetime) -> dict[str, Any]:
            return {"t": TENANT, "n": name, "s": s, "f": f, "rs": float(s), "rf": float(f),
                    "now": now}

        # 300 healthy tools first (heap order), then the one unreliable tool.
        for i in range(300):
            await c.execute(insert, _p(f"aaa.healthy{i:03d}", 10, 0, NOW))
        await c.execute(insert, _p("aaa.flaky", 1, 9, NOW))
        # Only one old failure (decayed below min_calls): not unreliable.
        await c.execute(insert, _p("aaa.once", 0, 1, NOW - timedelta(days=1)))
        await c.execute(text("ALTER TABLE tool_reliability_memory FORCE ROW LEVEL SECURITY"))
    await eng.dispose()
    app_eng = await app_role_engine(admin_url, ["tool_reliability_memory"])
    yield ToolReliabilityStore(db_session_factory=sessionmaker_for(app_eng))
    await app_eng.dispose()


async def test_unreliable_tool_is_found_among_hundreds(store: ToolReliabilityStore) -> None:
    unreliable = await store.get_unreliable_tools(tenant_id=TENANT)
    assert [t["tool_name"] for t in unreliable] == ["aaa.flaky"]


async def test_listing_puts_the_least_reliable_first_before_the_limit(
    store: ToolReliabilityStore,
) -> None:
    rows = await store.list_tools(tenant_id=TENANT, limit=200)
    assert len(rows) == 200
    assert rows[0]["tool_name"] == "aaa.flaky" and rows[0]["unreliable"] is True
