"""2.W-4: per-plan queue routing must actually place work on the plan's queue so
noisy-neighbour isolation holds.

Goal enqueue routing (CeleryGoalTaskQueue) is covered in
``tests/services/test_goal_queue_comprehensive.py``; this file closes the two
gaps: (1) all four goal plan tiers map to distinct queues, and (2) the workflow
runner dispatches ``execute_workflow_run`` onto ``workflows.{plan_tier}``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scaling.celery_app import PLAN_QUEUE_MAP
from app.workflow.dsl import WorkflowDefinition
from app.workflow.runner import WorkflowRunner


def test_all_goal_plan_tiers_route_to_distinct_queues() -> None:
    # Every plan maps to its own goals.* queue — no two share one.
    assert PLAN_QUEUE_MAP == {
        "free": "goals.free",
        "starter": "goals.starter",
        "professional": "goals.professional",
        "enterprise": "goals.enterprise",
    }
    assert len(set(PLAN_QUEUE_MAP.values())) == len(PLAN_QUEUE_MAP)


@pytest.mark.parametrize(
    ("plan", "expected_queue"),
    [
        ("free", "goals.free"),
        ("starter", "goals.starter"),
        ("professional", "goals.professional"),
        ("enterprise", "goals.enterprise"),
        ("unknown-plan", "goals.free"),  # safe default
    ],
)
def test_goal_enqueue_selects_plan_queue(plan: str, expected_queue: str) -> None:
    from app.services.goal_queue import CeleryGoalTaskQueue

    mock_task = MagicMock()
    mock_task.apply_async = MagicMock(return_value=MagicMock(id="tid"))
    with patch("app.scaling.tasks.run_goal", mock_task):
        CeleryGoalTaskQueue().enqueue_goal(
            goal_id="g1",
            tenant_id="t1",
            goal_text="do it",
            priority="normal",
            dry_run=False,
            plan=plan,
        )
    assert mock_task.apply_async.call_args[1]["queue"] == expected_queue


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tier", "expected_queue"),
    [
        ("free", "workflows.free"),
        ("enterprise", "workflows.enterprise"),
    ],
)
async def test_workflow_runner_dispatches_to_plan_tier_queue(
    tier: str, expected_queue: str
) -> None:
    runner = WorkflowRunner(
        compiler=MagicMock(),
        run_store=AsyncMock(),
        celery_app=MagicMock(),  # non-None → Celery (not inline) dispatch path
    )
    # Skip DB definition load / plan lookup with deterministic doubles.
    runner._load_definition = AsyncMock(return_value=WorkflowDefinition(name="wf", inputs={}))  # type: ignore[method-assign]
    runner._get_plan_tier = AsyncMock(return_value=tier)  # type: ignore[method-assign]

    mock_task = MagicMock()
    with patch("app.workflow.celery_tasks.execute_workflow_run", mock_task):
        await runner.run(
            workflow_id="wf-1",
            tenant_id="t1",
            inputs={},
            is_test_run=False,
            wait_for_completion=False,
        )

    mock_task.apply_async.assert_called_once()
    assert mock_task.apply_async.call_args[1]["queue"] == expected_queue
