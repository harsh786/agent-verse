"""SVC-03 integration (Postgres): cancel / approve keep iterations; a lost race reports truth.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/services/test_goal_status_writes_integration.py -m integration --no-cov
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration


@pytest.fixture
async def db(pg_url: str) -> AsyncIterator[Any]:
    engine = create_async_engine(pg_url, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(db: Any, status: str, iterations: int) -> tuple[TenantContext, str]:
    tid, gid = uuid.uuid4().hex, uuid.uuid4().hex
    async with db() as s, s.begin():
        await s.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:t, 'T', :e)"),
            {"t": tid, "e": f"{tid}@example.test"},
        )
        await s.execute(
            text(
                "INSERT INTO goals (id, tenant_id, goal_text, status, priority, "
                "autonomy_mode, workflow_mode, execution_context, dry_run, iterations) "
                "VALUES (:g, :t, 'deploy', :st, 'normal', 'supervised', 'single_agent', "
                "'{}'::jsonb, false, :it)"
            ),
            {"g": gid, "t": tid, "st": status, "it": iterations},
        )
    return TenantContext(tenant_id=tid, plan=PlanTier.PROFESSIONAL, api_key_id="k"), gid


async def _row(db: Any, gid: str) -> tuple[str, int]:
    async with db() as s:
        r = (
            await s.execute(text("SELECT status, iterations FROM goals WHERE id = :g"), {"g": gid})
        ).one()
    return str(r[0]), int(r[1])


class _Redis:
    async def set(self, *_a: Any, **_k: Any) -> bool:
        return True

    async def delete(self, *_a: Any) -> int:
        return 0

    async def publish(self, *_a: Any) -> int:
        return 0


def _svc(db: Any) -> GoalService:
    svc = GoalService(db_session_factory=db, task_queue=MagicMock())
    svc._redis = _Redis()
    return svc


async def test_cancel_preserves_iterations(db: Any) -> None:
    ctx, gid = await _seed(db, "executing", 7)
    result = await _svc(db).cancel_goal(gid, ctx)
    assert result["status"] == "cancelled"
    assert await _row(db, gid) == ("cancelled", 7)


async def test_approve_preserves_iterations(db: Any) -> None:
    ctx, gid = await _seed(db, "waiting_human", 4)
    result = await _svc(db).resume_goal(gid, ctx, approved=True)
    assert result["status"] == "resumed"
    assert await _row(db, gid) == ("executing", 4)


async def test_reject_after_goal_finished_keeps_real_status(db: Any) -> None:
    ctx, gid = await _seed(db, "waiting_human", 2)
    svc = _svc(db)
    record = await svc._aget_record(gid, ctx)  # still waiting_human
    # The worker finishes the goal between the read and the reject's write.
    async with db() as s, s.begin():
        await s.execute(text("UPDATE goals SET status = 'complete' WHERE id = :g"), {"g": gid})
    real_read = svc._db_get_goal_record

    async def _stale_then_fresh(goal_id: str, tenant_ctx: Any) -> Any:
        svc._db_get_goal_record = real_read  # type: ignore[method-assign]
        return record

    svc._db_get_goal_record = _stale_then_fresh  # type: ignore[method-assign]
    result = await svc.resume_goal(gid, ctx, approved=False, feedback="no")
    assert result["status"] == "complete"
    assert await _row(db, gid) == ("complete", 2)
