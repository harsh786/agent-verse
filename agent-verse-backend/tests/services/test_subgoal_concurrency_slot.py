"""CORE-07: a supervisor's sub-goals run under the parent's concurrency slot.

With POST /goals workflow_mode=supervisor now creating a real parent goal, the
parent holds one of the tenant's concurrent-goal slots while it waits for its
sub-goals. If each sub-goal also needed a slot, a tenant at its limit (2 on the
free plan) had its parent starve its own children — the tenant-limit twin of the
CORE-09 worker-slot starvation. Sub-goals therefore neither take nor release a
slot, consistently on the API (in-process) and worker paths; the fan-out is
bounded (at most 6 sub-tasks, one level, SUBGOAL_MARKER).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.supervisor import SUBGOAL_MARKER
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-slot", plan=PlanTier.FREE, api_key_id="kid")


class _Queue:
    def enqueue_goal(self, **kwargs: Any) -> str:
        return "task"


async def test_subgoal_submission_takes_no_concurrency_slot() -> None:
    svc = GoalService(task_queue=_Queue())
    inc = AsyncMock()
    with patch("app.tenancy.limits.check_and_increment_concurrent_goals", new=inc):
        await svc.submit_goal(
            goal="part A", priority="normal", dry_run=False, tenant_ctx=_CTX,
            execution_context={SUBGOAL_MARKER: "parent-1"},
        )
        inc.assert_not_awaited()
        await svc.submit_goal(goal="ordinary", priority="normal", dry_run=False, tenant_ctx=_CTX)
        inc.assert_awaited_once()


async def test_subgoal_terminal_event_releases_no_slot() -> None:
    svc = GoalService(task_queue=_Queue())
    with patch("app.tenancy.limits.check_and_increment_concurrent_goals", new=AsyncMock()):
        sub = await svc.submit_goal(
            goal="part B", priority="normal", dry_run=False, tenant_ctx=_CTX,
            execution_context={SUBGOAL_MARKER: "parent-2"},
        )
        plain = await svc.submit_goal(
            goal="plain", priority="normal", dry_run=False, tenant_ctx=_CTX
        )
    dec = AsyncMock()
    with patch("app.tenancy.limits.decrement_concurrent_goals", new=dec):
        await svc._dispatch_event(sub["goal_id"], {"type": "goal_complete"}, tenant_ctx=_CTX)
        dec.assert_not_awaited()
        await svc._dispatch_event(plain["goal_id"], {"type": "goal_complete"}, tenant_ctx=_CTX)
        dec.assert_awaited_once()


def test_celery_subgoal_message_tells_the_worker_it_is_a_subgoal() -> None:
    from app.services.goal_queue import CeleryGoalTaskQueue

    task = MagicMock()
    task.apply_async.return_value = MagicMock(id="t")
    with patch("app.scaling.tasks.run_goal", task):
        queue = CeleryGoalTaskQueue()
        queue.enqueue_goal(goal_id="g", tenant_id="t", goal_text="x", priority="normal",
                           dry_run=False, subgoal=True)
        assert task.apply_async.call_args.kwargs["kwargs"]["subgoal"] is True
        queue.enqueue_goal(goal_id="g2", tenant_id="t", goal_text="x", priority="normal",
                           dry_run=False)
        # Ordinary goals keep the unchanged message shape.
        assert "subgoal" not in task.apply_async.call_args.kwargs["kwargs"]


async def test_worker_subgoal_run_releases_no_slot() -> None:
    from app.scaling import tasks

    dec = AsyncMock()
    with patch("app.tenancy.limits.decrement_concurrent_goals", new=dec):
        token = tasks._SUBGOAL_RUN.set(True)
        try:
            await tasks._decrement_after_completion(
                "tid-slot", "redis://localhost:1/0", goal_id="goal-slot"
            )
        finally:
            tasks._SUBGOAL_RUN.reset(token)
        dec.assert_not_awaited()
        # RATE-06: the release is keyed by goal id (ZREM goal_id), so pass it
        # explicitly rather than relying on a _RUN_GOAL_ID leaked by another test.
        await tasks._decrement_after_completion(
            "tid-slot", "redis://localhost:1/0", goal_id="goal-slot"
        )
        dec.assert_awaited_once()
