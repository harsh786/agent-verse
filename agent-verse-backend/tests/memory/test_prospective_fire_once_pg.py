"""MEM-43 (integration): an intention is submitted as a goal at most once, on
real Postgres under the NOBYPASSRLS app role.

* ``complete`` is lost after a successful submission -> after the lease
  expires the next run finds the goal by its prospective_memory_id and
  completes with it; nothing is submitted again.
* two runs racing over the same due intentions submit each one once.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_prospective_fire_once_pg.py -q -m integration
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.memory.prospective import ProspectiveMemory
from app.memory.prospective_postgres import PostgresProspectiveMemoryService
from app.memory.prospective_runtime import create_intention, fire_due_intentions
from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

TENANT = "mem43-tenant"


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        yield url


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


class _Submitter:
    """Creates the goal row the way submit_goal does (execution_context carries
    the intention id) and counts submissions."""

    def __init__(self, sessions: Any) -> None:
        self._sessions = sessions
        self.calls: list[str] = []

    async def __call__(self, item: ProspectiveMemory) -> dict[str, Any]:
        from app.db.rls import sqlalchemy_rls_context

        self.calls.append(item.memory_id)
        gid = uuid.uuid4().hex
        async with (
            self._sessions() as s,
            s.begin(),
            sqlalchemy_rls_context(s, item.tenant_id),
        ):
            await s.execute(
                text(
                    "INSERT INTO goals (id, tenant_id, goal_text, status, priority, "
                    "execution_context) VALUES (:id, :t, :g, 'planning', 'normal', "
                    "CAST(:ctx AS json))"
                ),
                {
                    "id": gid,
                    "t": item.tenant_id,
                    "g": item.intention,
                    "ctx": json.dumps({"prospective_memory_id": item.memory_id}),
                },
            )
        return {"goal_id": gid}


async def _intention(svc: PostgresProspectiveMemoryService, now: datetime, key: str) -> str:
    item = await create_intention(
        svc, tenant_id=TENANT, intention=f"check deploy {key}",
        due_at=now - timedelta(minutes=1), idempotency_key=key, now=now - timedelta(hours=1),
    )
    return item.memory_id


async def test_lost_complete_is_resolved_without_resubmitting(sessions: Any) -> None:
    svc = PostgresProspectiveMemoryService(sessions)
    now = datetime.now(UTC)
    mid = await _intention(svc, now, "lost-complete")
    submit = _Submitter(sessions)
    real_complete = svc.complete

    async def _lost(*_a: Any, **_kw: Any) -> Any:
        raise ConnectionError("connection reset after commit of the goal")

    svc.complete = _lost  # type: ignore[method-assign]
    assert await fire_due_intentions(svc, tenant_id=TENANT, submit=submit, now=now) == []
    svc.complete = real_complete  # type: ignore[method-assign]

    later = now + timedelta(minutes=10)
    fired = await fire_due_intentions(svc, tenant_id=TENANT, submit=submit, now=later)
    assert submit.calls == [mid]  # submitted exactly once
    assert len(fired) == 1 and fired[0].result is not None
    assert fired[0].result["deduplicated"] is True
    assert (await svc.get(TENANT, mid)).state == "completed"  # type: ignore[union-attr]


async def test_racing_runs_submit_each_intention_once(sessions: Any) -> None:
    svc = PostgresProspectiveMemoryService(sessions)
    now = datetime.now(UTC) + timedelta(hours=1)
    mids = {await _intention(svc, now, f"race-{i}") for i in range(10)}
    submit = _Submitter(sessions)
    await asyncio.gather(
        fire_due_intentions(svc, tenant_id=TENANT, submit=submit, now=now),
        fire_due_intentions(svc, tenant_id=TENANT, submit=submit, now=now),
    )
    assert sorted(submit.calls) == sorted(mids)
