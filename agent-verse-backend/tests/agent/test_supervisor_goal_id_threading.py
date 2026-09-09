"""Regression: SubAgentTask must carry the REAL goal_id from submit_goal so the
supervisor response can correlate results to persisted goals."""
from __future__ import annotations

from typing import Any

import pytest

from app.agent.supervisor import SubAgentTask, SupervisorAgent
from app.providers.fake import FakeProvider

_DECOMPOSE = '{"sub_tasks": [{"goal": "task A"}, {"goal": "task B"}]}'


class _FakeGoalService:
    def __init__(self) -> None:
        self._n = 0

    async def submit_goal(self, **_kw: Any) -> dict[str, str]:
        self._n += 1
        return {"goal_id": f"goal-{self._n:03d}"}

    async def subscribe_events(self, *, goal_id: str, tenant_ctx: Any) -> Any:
        yield {"type": "goal_complete", "output": f"done:{goal_id}"}


def test_sub_agent_task_has_goal_id_field() -> None:
    assert SubAgentTask().goal_id == ""


@pytest.mark.asyncio
async def test_run_threads_real_goal_id_onto_each_task() -> None:
    # FakeProvider: 1st response decomposes, later responses synthesise.
    provider = FakeProvider(responses=[_DECOMPOSE, "synthesised result"])
    supervisor = SupervisorAgent(
        planner_provider=provider,
        goal_service=_FakeGoalService(),
        max_parallel=2,
    )
    result = await supervisor.run(goal="do a complex thing", tenant_ctx=None)

    assert len(result.tasks) == 2
    # Every task carries the REAL goal id returned by submit_goal (not the random
    # task_id), and they are distinct per sub-task.
    goal_ids = {t.goal_id for t in result.tasks}
    assert all(gid.startswith("goal-") for gid in goal_ids), goal_ids
    assert len(goal_ids) == 2  # distinct real ids
    for t in result.tasks:
        assert t.goal_id and t.goal_id != t.task_id
        assert t.status == "complete"
