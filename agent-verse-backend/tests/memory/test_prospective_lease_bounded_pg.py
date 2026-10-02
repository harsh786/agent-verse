"""MEM-44 (integration): leasing is bounded and terminal rows are purged.

* 200 due intentions -> one fire run leases exactly ``maximum_items`` (50); the
  rest stay pending (they used to be leased — and then left for 5 minutes —
  although only 50 were processed).
* the daily purge deletes terminal rows past the retention window, in batches,
  and leaves pending and recent terminal rows alone.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_prospective_lease_bounded_pg.py -q -m integration
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.memory.prospective_postgres import (
    PostgresProspectiveMemoryService,
    purge_terminal_prospective,
)
from app.memory.prospective_runtime import fire_due_intentions
from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

TENANT = "mem44-tenant"
NOW = datetime.now(UTC)


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        yield url


async def _insert(admin_url: str, rows: list[tuple[str, str, datetime]]) -> None:
    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        for mid, state, due in rows:
            await c.execute(
                text(
                    "INSERT INTO prospective_memory (memory_id, tenant_id, intention, due_at, "
                    "expires_at, state, idempotency_key) VALUES (:m, :t, 'x', :d, :e, :s, :m)"
                ),
                {"m": mid, "t": TENANT, "d": due, "e": due + timedelta(days=400), "s": state},
            )
    await eng.dispose()


async def _states(admin_url: str) -> dict[str, int]:
    eng = create_async_engine(admin_url)
    async with eng.connect() as c:
        rows = (
            await c.execute(
                text("SELECT state, count(*) FROM prospective_memory GROUP BY state")
            )
        ).fetchall()
    await eng.dispose()
    return {str(r[0]): int(r[1]) for r in rows}


@pytest.fixture(scope="module")
async def sessions(admin_url: str) -> Any:
    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        await c.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:t, :t, :e)"),
            {"t": TENANT, "e": f"{TENANT}@example.test"},
        )
    await eng.dispose()
    app_eng = await app_role_engine(admin_url, ["prospective_memory", "goals"])
    yield sessionmaker_for(app_eng)
    await app_eng.dispose()


async def test_one_run_leases_exactly_maximum_items(admin_url: str, sessions: Any) -> None:
    await _insert(admin_url, [(f"due-{i}", "pending", NOW - timedelta(minutes=1))
                              for i in range(200)])
    submitted: list[str] = []

    async def _submit(item: Any) -> dict[str, Any]:
        submitted.append(item.memory_id)
        return {"goal_id": f"g-{item.memory_id}"}

    svc = PostgresProspectiveMemoryService(sessions)
    fired = await fire_due_intentions(
        svc, tenant_id=TENANT, submit=_submit, now=NOW, maximum_items=50
    )
    assert len(fired) == len(submitted) == 50
    states = await _states(admin_url)
    assert states == {"completed": 50, "pending": 150}  # nothing left leased-but-unprocessed


async def test_purge_removes_only_old_terminal_rows(admin_url: str) -> None:
    old = NOW - timedelta(days=120)
    await _insert(
        admin_url,
        [(f"old-c-{i}", "completed", old) for i in range(25)]
        + [("old-f", "failed", old), ("old-x", "cancelled", old), ("old-e", "expired", old)]
        + [("old-pending", "pending", old), ("recent-c", "completed", NOW - timedelta(days=1))],
    )
    before = await _states(admin_url)
    maintenance = async_sessionmaker(create_async_engine(admin_url), expire_on_commit=False)
    purged = await purge_terminal_prospective(
        maintenance, now=NOW, retention=timedelta(days=90), batch_size=10
    )
    assert purged == 28  # 25 + failed + cancelled + expired, in batches of 10
    after = await _states(admin_url)
    assert after["pending"] == before["pending"]  # old pending kept
    assert after["completed"] == before["completed"] - 25  # recent completed kept
    assert "failed" not in after and "cancelled" not in after and "expired" not in after
