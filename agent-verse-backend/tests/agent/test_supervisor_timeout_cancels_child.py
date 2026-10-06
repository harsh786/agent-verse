"""a01-F006-05 (related): a sub-goal the supervisor gave up on is cancelled.

The parent waits on each sub-goal under ``asyncio.timeout`` (300 s by default).
On a timeout it marked the sub-task failed and synthesized without it, but the
sub-goal itself kept running on the sub-goal pool — spending the tenant's budget
and running tools whose results nobody would read. It is now cancelled.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.agent.supervisor import SupervisorAgent
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio

T = TenantContext(tenant_id="sup-timeout", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _GoalService:
    def __init__(self, *, hang: set[str], cancel_raises: bool = False) -> None:
        self._hang = hang
        self._goals: dict[str, str] = {}
        self.cancelled: list[str] = []
        self._cancel_raises = cancel_raises

    async def submit_goal(self, *, goal: str, **_: Any) -> dict[str, Any]:
        gid = f"child-{len(self._goals) + 1}"
        self._goals[gid] = goal
        return {"goal_id": gid}

    async def subscribe_events(self, *, goal_id: str, tenant_ctx: Any):
        if self._goals[goal_id] in self._hang:
            await asyncio.Event().wait()  # never finishes
        yield {"type": "step_complete", "output": f"{self._goals[goal_id]} done"}
        yield {"type": "goal_complete"}

    async def cancel_goal(self, *, goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        if self._cancel_raises:
            raise RuntimeError("cancel failed")
        self.cancelled.append(goal_id)
        return {"goal_id": goal_id, "status": "cancelled"}


def _planner() -> FakeProvider:
    return FakeProvider(
        responses=['{"sub_tasks": [{"goal": "fast"}, {"goal": "slow"}]}', "synthesized"]
    )


async def test_timed_out_sub_goal_is_cancelled() -> None:
    svc = _GoalService(hang={"slow"})
    result = await SupervisorAgent(
        planner_provider=_planner(), goal_service=svc, timeout_per_subtask=0.05
    ).run(goal="g", tenant_ctx=T)

    by_goal = {t.goal: t for t in result.tasks}
    assert by_goal["slow"].status == "failed"
    assert "Timeout" in by_goal["slow"].error
    assert svc.cancelled == [by_goal["slow"].goal_id]
    assert by_goal["fast"].status == "complete"


async def test_a_failed_cancel_never_breaks_the_parent() -> None:
    svc = _GoalService(hang={"slow"}, cancel_raises=True)
    result = await SupervisorAgent(
        planner_provider=_planner(), goal_service=svc, timeout_per_subtask=0.05
    ).run(goal="g", tenant_ctx=T)
    assert {t.goal: t.status for t in result.tasks} == {"fast": "complete", "slow": "failed"}
