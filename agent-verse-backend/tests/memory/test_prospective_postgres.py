"""PostgresProspectiveMemoryService — durable prospective memory (integration)."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_NOW = datetime.now(UTC)


def _item(key: str, *, due_offset_h: float):
    from app.memory.prospective import ProspectiveMemory, prospective_id

    return ProspectiveMemory(
        memory_id=prospective_id("t1", key),
        tenant_id="t1",
        intention=key,
        due_at=_NOW + timedelta(hours=due_offset_h),
        expires_at=_NOW + timedelta(days=30),
        state="pending",
        source_goal_id="g1",
        source_execution_id="e1",
        policy_snapshot={},
        classification="internal",
        idempotency_key=key,
    )


async def test_postgres_prospective_lifecycle() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from testcontainers.postgres import PostgresContainer

    from app.memory.prospective_postgres import PostgresProspectiveMemoryService

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        admin_url = pg.get_connection_url()
        env = {**os.environ, "DATABASE_URL": admin_url, "ENVIRONMENT": "development"}
        r = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=_BACKEND_ROOT, env=env, capture_output=True, text=True,
        )
        assert r.returncode == 0, f"alembic failed:\n{r.stderr[-1500:]}"

        engine = create_async_engine(admin_url)
        svc = PostgresProspectiveMemoryService(async_sessionmaker(engine, expire_on_commit=False))

        # create idempotent
        await svc.create(_item("call vendor", due_offset_h=-1))
        await svc.create(_item("call vendor", due_offset_h=-1))  # same idempotency_key
        await svc.create(_item("review later", due_offset_h=48))

        # list_active is read-only + due-first + persists across a fresh instance
        active = await svc.list_active("t1", now=_NOW)
        assert [i.intention for i in active] == ["call vendor", "review later"]
        svc2 = PostgresProspectiveMemoryService(async_sessionmaker(engine, expire_on_commit=False))
        assert len(await svc2.list_active("t1", now=_NOW)) == 2

        # lease the due one (fencing token advances), then complete it
        leased = await svc.lease_due("t1", now=_NOW, lease_duration=timedelta(minutes=5))
        assert len(leased) == 1 and leased[0].intention == "call vendor"
        assert leased[0].fencing_token == 1
        done = await svc.complete(
            "t1", leased[0].memory_id, fencing_token=1, authorized=True, result={"ok": True}
        )
        assert done.state == "completed"

        # stale fencing token is rejected
        with pytest.raises(RuntimeError):
            await svc.complete("t1", leased[0].memory_id, fencing_token=999, authorized=True, result={})

        # tenant isolation
        assert await svc.list_active("other", now=_NOW) == ()
        await engine.dispose()
