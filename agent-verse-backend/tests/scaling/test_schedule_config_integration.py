"""Integration proof: a FILE_DROP schedule created via ScheduleStore actually
fires through the Celery beat loop.

This locks in the fix for the bug where FILE_DROP triggers never fired: the
watch path lived only on the TriggerSpec and was dropped by every schedule
serializer, so ``fire_due_schedules`` always saw an empty watch path. With the
generic ``config`` JSONB column, the store persists ``file_drop_path`` /
``file_pattern`` to Postgres and the beat loop reads them back.

Requires a real Postgres container — run with: uv run pytest -m integration
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def postgres_url() -> Any:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        environment = {**os.environ, "DATABASE_URL": admin_url}
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        yield admin_url


def test_file_drop_schedule_from_store_fires_via_beat_loop(
    postgres_url: str, monkeypatch: Any, tmp_path: Path
) -> None:
    from app.scaling import tasks
    from app.triggers.models import TriggerSpec, TriggerType
    from app.triggers.store import ScheduleStore

    tenant_id = uuid.uuid4().hex
    dropped_file = tmp_path / "orders.csv"
    dropped_file.write_text("id,total\n1,42\n", encoding="utf-8")

    # ── 1. Create tenant + a FILE_DROP schedule via the real store ──────────
    async def _setup() -> dict[str, Any]:
        engine = create_async_engine(postgres_url)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        from app.db.models.scheduling import Schedule
        from app.db.models.tenant import Tenant

        async with sessionmaker() as session, session.begin():
            session.add(Tenant(id=tenant_id, name="FileDrop Co", email=f"{tenant_id}@ex.com"))

        from app.tenancy.context import PlanTier, TenantContext

        store = ScheduleStore(db_session_factory=sessionmaker)
        spec = TriggerSpec(
            trigger_type=TriggerType.FILE_DROP,
            file_drop_path=str(tmp_path),
            file_pattern="*.csv",
        )
        sched_id = await store.create_async(
            goal_id="Ingest dropped CSV files",
            spec=spec,
            tenant_ctx=TenantContext(
                tenant_id=tenant_id,
                plan=PlanTier.PROFESSIONAL,
                api_key_id="test",
            ),
        )

        # Prove the config blob actually persisted to Postgres.
        async with sessionmaker() as session:
            row = (
                await session.execute(select(Schedule).where(Schedule.id == sched_id))
            ).scalar_one()
            persisted_config = dict(row.config)

        await engine.dispose()
        return {"sched_id": sched_id, "config": persisted_config}

    setup = asyncio.run(_setup())
    assert setup["config"] == {"file_drop_path": str(tmp_path), "file_pattern": "*.csv"}

    # ── 2. Run the beat loop against the same Postgres, capturing dispatch ───
    dispatched: list[dict[str, Any]] = []

    def fake_apply_async(*, kwargs: dict[str, Any], queue: str) -> Any:
        dispatched.append({"kwargs": kwargs, "queue": queue})

        class _Result:
            id = "task-fd-1"

        return _Result()

    def fresh_factory() -> Any:
        engine = create_async_engine(postgres_url)
        return async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "true")
    monkeypatch.setattr("app.db.session.get_session_factory", fresh_factory)
    monkeypatch.setattr(tasks.run_goal, "apply_async", fake_apply_async)

    result = tasks.fire_due_schedules()

    # ── 3. The schedule was discovered and the dropped file fired a goal ────
    assert result["schedules_checked"] == 1
    assert result["schedules_fired"] == 1
    assert len(dispatched) == 1
    fired = dispatched[0]
    assert fired["queue"] == "schedules"
    assert fired["kwargs"]["tenant_id"] == tenant_id
    # The goal text carries the watch path + concrete dropped file (proving the
    # watch path surfaced from the persisted config, not an empty string).
    assert str(tmp_path) in fired["kwargs"]["goal_text"]
    assert "orders.csv" in fired["kwargs"]["goal_text"]
