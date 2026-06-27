"""Tests for SupervisorAgent multi-agent pattern."""
from __future__ import annotations

import pytest
from app.agent.supervisor import SupervisorAgent, SubAgentTask, SupervisionResult


def test_sub_agent_task_defaults() -> None:
    t = SubAgentTask(goal="do something")
    assert t.status == "pending"
    assert t.task_id is not None


@pytest.mark.asyncio
async def test_supervisor_decompose_fallback() -> None:
    """When LLM decompose fails, falls back to single task."""
    from app.providers.fake import FakeProvider

    fake = FakeProvider(responses=["invalid json"])
    supervisor = SupervisorAgent(
        planner_provider=fake, goal_service=None, max_parallel=2
    )
    tasks = await supervisor._decompose("do the thing", None)
    assert len(tasks) >= 1
    assert tasks[0].goal == "do the thing"


def test_supervision_result() -> None:
    r = SupervisionResult(success=True, tasks=[], synthesized_result="done")
    assert r.success is True
