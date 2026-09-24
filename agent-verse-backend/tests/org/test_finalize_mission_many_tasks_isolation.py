"""``OrgService.finalize_mission`` must not let one bad task block the rest.

``finalize_mission`` closes every non-terminal task on a mission in a plain
Python loop, calling ``update_task_status`` (which flushes an UPDATE on the
task row and inserts an ``org_events`` row via ``_emit_event``) once per task.

Without per-task isolation, an exception raised while closing ANY task (a
stale row, a constraint violation, a transient failure inside the event
publish) propagates straight out of the ``for`` loop: the whole
``finalize_mission`` call raises, the tasks that were *already* closed in
this same transaction get rolled back with it, the tasks after the failing
one are never even attempted, and the mission itself never transitions out
of ``active`` — for a mission with dozens of tasks, one bad task blocks the
entire mission forever, and every retry hits the exact same task again.

This mirrors the exact bug class already fixed in
``app/scaling/tasks.py::_delete_expired_records`` (isolate per-table DELETEs
with SAVEPOINTs so one failure can't poison the rest of the batch) and
``app/enterprise/compliance.py`` (GDPR erasure) earlier in this session — the
fix here wraps each task's status update in its own SAVEPOINT
(``session.begin_nested()``) plus a try/except, so one bad task is logged and
skipped instead of blocking the batch.

Run with:
    uv run pytest tests/org/test_finalize_mission_many_tasks_isolation.py \
        -m integration --no-cov -q
"""

from __future__ import annotations

import os
import types
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.rls import sqlalchemy_rls_context, system_session
from app.org.service import OrgService

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse",
)


class _FakeGoalService:
    """Stands in for the wired GoalService: the goal is already terminal."""

    async def get_goal(self, goal_id: str, tenant_ctx: Any) -> dict:
        return {"status": "completed", "result_artifact": {"summary": "done"}}


@pytest.mark.integration
async def test_one_failing_task_does_not_block_closing_the_rest_or_the_mission() -> None:
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    mission_id = str(uuid.uuid4())
    task_ids = [str(uuid.uuid4()) for _ in range(6)]
    # Not the first, not the last — proves both "already processed" tasks
    # (ahead of it in the loop) and "not yet processed" tasks (after it) are
    # both unaffected by its failure.
    poison_task_id = task_ids[3]

    try:
        async with factory() as session, session.begin(), system_session(session):
            await session.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, 'Finalize Scale Test', :email, 'free', true)"
                ),
                {"id": tenant_id, "email": f"{tenant_id}@example.test"},
            )
            await session.execute(
                text(
                    "INSERT INTO organizations (id, tenant_id, name, slug) "
                    "VALUES (CAST(:id AS uuid), :tid, :name, :slug)"
                ),
                {
                    "id": org_id,
                    "tid": tenant_id,
                    "name": "Finalize Scale Org",
                    "slug": f"finalize-scale-{uuid.uuid4().hex[:8]}",
                },
            )
            await session.execute(
                text(
                    "INSERT INTO org_missions "
                    "(id, tenant_id, org_id, title, objective, status, priority, "
                    " source, extra_data) "
                    "VALUES (CAST(:id AS uuid), :tid, CAST(:oid AS uuid), "
                    "        :title, :objective, 'active', 'medium', 'manual', "
                    "        CAST(:extra AS jsonb))"
                ),
                {
                    "id": mission_id,
                    "tid": tenant_id,
                    "oid": org_id,
                    "title": "Scale test mission",
                    "objective": "Close many tasks",
                    "extra": '{"goal_id": "goal-xyz"}',
                },
            )
            for tid in task_ids:
                await session.execute(
                    text(
                        "INSERT INTO org_tasks "
                        "(id, tenant_id, org_id, mission_id, title, status, priority, "
                        " depth, extra_data) "
                        "VALUES (CAST(:id AS uuid), :tid, CAST(:oid AS uuid), "
                        "        CAST(:mid AS uuid), :title, 'running', 'medium', "
                        "        0, CAST(:extra AS jsonb))"
                    ),
                    {
                        "id": tid,
                        "tid": tenant_id,
                        "oid": org_id,
                        "mid": mission_id,
                        "title": f"subtask {tid}",
                        "extra": '{"task_kind": "subtask"}',
                    },
                )

        original_update_task_status = OrgService.update_task_status

        async def _flaky_update_task_status(self: OrgService, task_id: str, status: str, **kw: Any):
            if task_id == poison_task_id:
                raise RuntimeError("simulated failure closing this task")
            return await original_update_task_status(self, task_id, status, **kw)

        async with (
            factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            svc = OrgService(session=session, tenant_id=tenant_id)
            with patch.object(OrgService, "update_task_status", _flaky_update_task_status):
                result = await svc.finalize_mission(
                    mission_id,
                    app_state=types.SimpleNamespace(goal_service=_FakeGoalService()),
                    tenant_ctx=None,
                )

        # The whole call must not raise, and the mission must still finalize.
        assert result["finalized"] is True
        assert result["status"] == "completed"

        async with factory() as session, session.begin(), system_session(session):
            mission_status = await session.scalar(
                text("SELECT status FROM org_missions WHERE id = CAST(:id AS uuid)"),
                {"id": mission_id},
            )
            rows = (
                await session.execute(
                    text(
                        "SELECT id, status FROM org_tasks "
                        "WHERE mission_id = CAST(:mid AS uuid)"
                    ),
                    {"mid": mission_id},
                )
            ).all()
            statuses = {str(r.id): r.status for r in rows}

        assert mission_status == "completed", (
            "mission never finalized — one failing task blocked the whole batch"
        )
        # Every task EXCEPT the poisoned one must have been closed.
        for tid in task_ids:
            if tid == poison_task_id:
                continue
            assert statuses[tid] == "completed", (
                f"task {tid} was never closed — the poisoned task upstream of it "
                "in the loop blocked the rest of the batch"
            )
        # The poisoned task itself is left as-is (its update genuinely failed) —
        # not silently marked completed.
        assert statuses[poison_task_id] == "running"
    finally:
        async with factory() as session, session.begin(), system_session(session):
            await session.execute(
                text("DELETE FROM org_events WHERE org_id = CAST(:id AS uuid)"), {"id": org_id}
            )
            await session.execute(
                text("DELETE FROM org_tasks WHERE org_id = CAST(:id AS uuid)"), {"id": org_id}
            )
            await session.execute(
                text("DELETE FROM org_missions WHERE org_id = CAST(:id AS uuid)"), {"id": org_id}
            )
            await session.execute(
                text("DELETE FROM organizations WHERE id = CAST(:id AS uuid)"), {"id": org_id}
            )
            await session.execute(text("DELETE FROM tenants WHERE id=:id"), {"id": tenant_id})
        await engine.dispose()
