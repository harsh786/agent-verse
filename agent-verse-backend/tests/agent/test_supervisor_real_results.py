"""Supervisor sub-task results are the real sub-goal outputs (audit item 3)."""

from __future__ import annotations

from typing import Any

import pytest

from app.agent.supervisor import SupervisorAgent
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio

T = TenantContext(tenant_id="sup-real", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _GoalService:
    """Emits the same event shapes as the real GoalService (goal_complete has no output)."""

    def __init__(self, scripts: dict[str, list[dict[str, Any]]]) -> None:
        self._scripts = scripts
        self._by_goal: dict[str, list[dict[str, Any]]] = {}

    async def submit_goal(self, *, goal: str, **_: Any) -> dict[str, Any]:
        gid = f"g-{len(self._by_goal) + 1}"
        self._by_goal[gid] = self._scripts[goal]
        return {"goal_id": gid}

    async def subscribe_events(self, *, goal_id: str, tenant_ctx: Any):
        for evt in self._by_goal[goal_id]:
            yield evt


def _planner(*subgoals: str) -> FakeProvider:
    tasks = ", ".join(f'{{"goal": "{g}"}}' for g in subgoals)
    return FakeProvider(responses=[f'{{"sub_tasks": [{tasks}]}}', "synthesized answer"])


async def test_sub_task_result_is_real_output_not_literal_completed() -> None:
    svc = _GoalService(
        {
            "find the price": [
                {"type": "step_complete", "step": "s", "output": "The price is $42."},
                {"type": "goal_complete"},
            ],
            "find the stock": [
                {"type": "step_complete", "step": "s", "output": "In stock."},
                {"type": "goal_complete"},
            ],
        }
    )
    result = await SupervisorAgent(
        planner_provider=_planner("find the price", "find the stock"), goal_service=svc
    ).run(goal="g", tenant_ctx=T)
    by_goal = {t.goal: t for t in result.tasks}
    assert by_goal["find the price"].status == "complete"
    assert by_goal["find the price"].result == "The price is $42."
    assert by_goal["find the price"].result != "completed"


async def test_failures_and_empty_completions_are_surfaced() -> None:
    svc = _GoalService(
        {
            "a": [{"type": "step_complete", "output": "A done"}, {"type": "goal_complete"}],
            "b": [{"type": "goal_failed", "reason": "tool denied"}],
            "c": [{"type": "goal_complete"}],  # completed with no output at all
            "d": [{"type": "step_started"}],  # stream ended without a terminal event
        }
    )
    result = await SupervisorAgent(
        planner_provider=_planner("a", "b", "c", "d"), goal_service=svc
    ).run(goal="g", tenant_ctx=T)
    by_goal = {t.goal: t for t in result.tasks}
    assert by_goal["a"].status == "complete"
    assert by_goal["b"].status == "failed" and by_goal["b"].error == "tool denied"
    assert by_goal["c"].status == "failed"
    assert by_goal["d"].status == "failed"
    assert result.success is False


def _bridged(goal_id: str, event: dict[str, Any]) -> dict[str, Any]:
    """The envelope Celery workers publish (and the Redis bridge forwards)."""
    return {"goal_id": goal_id, "tenant_id": T.tenant_id, "type": event["type"], "payload": event}


async def test_worker_bridged_events_are_unwrapped() -> None:
    """Sub-goals run on workers deliver {type, payload:{...}}; outputs sat in payload."""
    svc = _GoalService(
        {
            "find the price": [
                _bridged("g-1", {"type": "step_complete", "output": "The price is $42."}),
                _bridged("g-1", {"type": "goal_complete"}),
            ],
            "find the stock": [
                _bridged("g-2", {"type": "goal_failed", "reason": "tool denied"}),
            ],
        }
    )
    result = await SupervisorAgent(
        planner_provider=_planner("find the price", "find the stock"), goal_service=svc
    ).run(goal="g", tenant_ctx=T)
    by_goal = {t.goal: t for t in result.tasks}
    assert by_goal["find the price"].status == "complete"
    assert by_goal["find the price"].result == "The price is $42."
    assert by_goal["find the stock"].status == "failed"
    assert by_goal["find the stock"].error == "tool denied"
