"""e2e_full (WF-18): the worker claim against real Postgres, in both orders.

1. A run_goal message delivered while the goal is still ``waiting_human`` (an
   acks_late redelivery or a stale duplicate) claims nothing: the row stays
   ``waiting_human`` and the worker skips it.
2. resume_goal on a suspended goal persists ``executing`` BEFORE it enqueues
   run_goal, so the relaunch message it sends IS claimable.

Uses the real ``_claim_goal_for_execution`` SQL (conditional UPDATE under RLS)
and the booted app's GoalService; the task queue is a recorder (no worker).
"""

from __future__ import annotations

import os
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _db_status(goal_id: str) -> str:
    import asyncpg

    conn = await asyncpg.connect(
        os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    )
    try:
        return str(await conn.fetchval("SELECT status FROM goals WHERE id = $1", goal_id))
    finally:
        await conn.close()


class _RecordingQueue:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []

    def enqueue_goal(self, **kwargs: Any) -> str:
        self.enqueued.append(kwargs)
        return "recorded"


async def test_waiting_goal_is_not_claimed_but_its_resume_relaunch_is(
    app: Any, tenant_client: Any
) -> None:
    from app.scaling.tasks import _claim_goal_for_execution
    from app.services.goal_service import _SUSPENDED_KEY, GoalStatus

    gs = app.state.goal_service
    queue = _RecordingQueue()
    prev_queue = gs._task_queue
    gs._task_queue = queue
    try:
        resp = await tenant_client.post("/goals", json={"goal": "Deploy the release notes"})
        assert resp.status_code == 202, resp.text
        goal_id = resp.json()["goal_id"]
        record = gs._goals[goal_id]
        from app.tenancy.context import PlanTier, TenantContext

        tenant_ctx = TenantContext(tenant_id=record.tenant_id, plan=PlanTier.FREE, api_key_id="e2e")

        # The goal's run ended waiting for approvals (supervised suspension).
        assert await gs._db_update_goal_status(
            goal_id, tenant_ctx.tenant_id, GoalStatus.WAITING_HUMAN.value
        )
        await gs._db_set_suspended(goal_id, tenant_ctx.tenant_id, True)
        record.status = GoalStatus.WAITING_HUMAN
        record.execution_context[_SUSPENDED_KEY] = True

        # Order 1: a message arrives while the goal still waits for a human.
        assert await _claim_goal_for_execution(goal_id, tenant_ctx.tenant_id) == "waiting_human"
        assert await _db_status(goal_id) == "waiting_human"

        # Order 2: the human approves; resume_goal relaunches it.
        queue.enqueued.clear()
        result = await gs.resume_goal(goal_id, tenant_ctx, approved=True)
        assert result["status"] == "resumed"
        assert [m["goal_id"] for m in queue.enqueued] == [goal_id]
        assert await _db_status(goal_id) == "executing"
        # The relaunch message is claimable.
        assert await _claim_goal_for_execution(goal_id, tenant_ctx.tenant_id) == "claimed"
    finally:
        gs._task_queue = prev_queue
