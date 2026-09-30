"""CORE-09: supervisor sub-goals run on a dedicated Celery queue family.

A worker-run supervisor parent holds its Celery slot while it waits for its
sub-goals. When the sub-goals went to the same ``goals.{plan}`` queue, a pool
with few slots (every slot held by a waiting parent) never ran them: the parent
starved its own children. Sub-goals now go to ``goals.subgoals.{plan}``, which
only a dedicated sub-goal worker pool consumes.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.supervisor import SUBGOAL_MARKER
from app.scaling.celery_app import PLAN_QUEUE_MAP, SUBGOAL_QUEUE_MAP, goal_queue_for
from app.services.goal_service import GoalService, GoalStatus
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-sub", plan=PlanTier.STARTER, api_key_id="kid")


class _Queue:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []

    def enqueue_goal(self, **kwargs: Any) -> str:
        self.enqueued.append(kwargs)
        return "task"


def test_subgoal_queues_are_distinct_from_the_main_goal_queues() -> None:
    assert set(SUBGOAL_QUEUE_MAP) == set(PLAN_QUEUE_MAP)
    assert not set(SUBGOAL_QUEUE_MAP.values()) & set(PLAN_QUEUE_MAP.values())
    assert goal_queue_for("enterprise") == "goals.enterprise"
    assert goal_queue_for("enterprise", subgoal=True) == "goals.subgoals.enterprise"
    # An unknown plan falls back to the free tier in both families.
    assert goal_queue_for("bogus") == "goals.free"
    assert goal_queue_for("bogus", subgoal=True) == "goals.subgoals.free"


@pytest.mark.parametrize(("subgoal", "expected"), [(False, "goals.professional"),
                                                   (True, "goals.subgoals.professional")])
def test_celery_queue_routes_subgoals_to_the_subgoal_queue(subgoal: bool, expected: str) -> None:
    from app.services.goal_queue import CeleryGoalTaskQueue

    task = MagicMock()
    task.apply_async.return_value = MagicMock(id="tid")
    with patch("app.scaling.tasks.run_goal", task):
        CeleryGoalTaskQueue().enqueue_goal(
            goal_id="g", tenant_id="t", goal_text="x", priority="normal",
            dry_run=False, plan="professional", subgoal=subgoal,
        )
    assert task.apply_async.call_args.kwargs["queue"] == expected


async def test_submit_goal_marks_supervisor_subgoals_for_the_subgoal_queue() -> None:
    queue = _Queue()
    svc = GoalService(task_queue=queue)
    await svc.submit_goal(
        goal="Research part A", priority="normal", dry_run=False, tenant_ctx=_CTX,
        execution_context={SUBGOAL_MARKER: "parent-1"},
    )
    await svc.submit_goal(
        goal="An ordinary goal", priority="normal", dry_run=False, tenant_ctx=_CTX,
    )
    assert queue.enqueued[0].get("subgoal") is True
    # Ordinary goals keep the unchanged enqueue call shape (main pool).
    assert "subgoal" not in queue.enqueued[1]


async def test_relaunched_suspended_subgoal_stays_on_the_subgoal_queue() -> None:
    queue = _Queue()
    svc = GoalService(task_queue=queue)
    result = await svc.submit_goal(
        goal="Research part B", priority="normal", dry_run=False, tenant_ctx=_CTX,
        execution_context={SUBGOAL_MARKER: "parent-2"},
    )
    record = svc._goals[result["goal_id"]]
    record.status = GoalStatus.WAITING_HUMAN
    queue.enqueued.clear()
    with patch("app.tenancy.limits.check_and_increment_concurrent_goals", new=AsyncMock()):
        await svc._relaunch_suspended_goal(record, _CTX)
    assert queue.enqueued and queue.enqueued[0].get("subgoal") is True
