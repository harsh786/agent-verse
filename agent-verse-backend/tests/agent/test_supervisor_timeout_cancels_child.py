"""a01-F006-05 (related): a sub-goal the supervisor gave up on is cancelled.

In-slot mode (in-process runs without a durable ledger): the parent waits on
each sub-goal under ``asyncio.timeout`` — that child's timeout (the plan's /
agent's, no longer a fixed 300 s; ``timeout_per_subtask`` overrides it here). On
a timeout it marked the sub-task failed and synthesized without it, but the
sub-goal itself kept running on the sub-goal pool — spending the tenant's budget
and running tools whose results nobody would read. It is now cancelled.

On a worker the parent no longer waits at all (continuation mode): the fan-out
sweeper cancels an overdue child instead
(tests/services/test_fanout_continuation_pg.py).
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


async def test_default_wait_follows_the_childs_timeout_not_a_fixed_300s() -> None:
    from app.tenancy.context import PLAN_LIMITS

    sup = SupervisorAgent(planner_provider=_planner(), goal_service=_GoalService(hang=set()))
    plan_cap = float(PLAN_LIMITS[PlanTier.ENTERPRISE].goal_timeout_seconds)
    from app.agent.supervisor import SubAgentTask

    assert sup._task_timeout(SubAgentTask(goal="g"), T) == plan_cap
    assert sup._task_timeout(SubAgentTask(goal="g", timeout_s=120), T) == 120
    agent_bound = SupervisorAgent(
        planner_provider=_planner(),
        goal_service=_GoalService(hang=set()),
        child_timeout_seconds=900,
    )
    assert agent_bound._task_timeout(SubAgentTask(goal="g"), T) == 900
