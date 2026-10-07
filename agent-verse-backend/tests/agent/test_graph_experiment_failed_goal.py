"""a05-F087-02: a FAILED goal of a SelfOptimizerV2 experiment arm is recorded.

The verify node recorded the arm only on success, so a candidate config that
made goals fail was never penalised: its arm only ever saw the goals it got
right. A terminal failure now records the arm with eval_score 0.0 — once.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import GoalStatus
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="f087-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


def _optimizer() -> SimpleNamespace:
    return SimpleNamespace(
        get_arm_assignment=AsyncMock(
            return_value={
                "arm": "candidate",
                "experiment_id": "exp-1",
                "changed_keys": ["system_prompt"],
                "config": {"system_prompt": "a worse prompt"},
            }
        ),
        on_goal_completed=AsyncMock(),
    )


def _graph(verdict: str, optimizer: SimpleNamespace) -> AgentGraph:
    graph = AgentGraph(
        # A structured plan: a plain-text reply is turned into an empty schema mock
        # by FakeProvider, and a run that executes nothing has no answer.
        planner=FakeProvider(responses=[
            '{"steps": [{"id": "s0", "description": "do the thing", "depends_on": []}]}'
        ]),
        executor=FakeProvider(responses=["did the thing"]),
        verifier=FakeProvider(responses=[verdict]),
        max_iterations=1,
    )
    graph._app_state = SimpleNamespace(self_optimizer_v2=optimizer)
    graph._agent_id = "agent-exp"
    return graph


@pytest.mark.asyncio
async def test_failed_goal_of_an_experiment_arm_is_recorded_as_a_zero_score() -> None:
    optimizer = _optimizer()
    graph = _graph('{"success": false, "reason": "wrong answer", "retry": false}', optimizer)

    final = await graph.run(goal="answer the question", tenant_ctx=T, goal_id="g-fail")

    assert final.status == GoalStatus.FAILED
    optimizer.on_goal_completed.assert_awaited_once()
    kwargs = optimizer.on_goal_completed.call_args.kwargs
    assert kwargs["tenant_id"] == T.tenant_id
    assert kwargs["agent_id"] == "agent-exp"
    assert kwargs["goal_id"] == "g-fail"
    assert kwargs["eval_score"] == 0.0


@pytest.mark.asyncio
async def test_successful_goal_of_an_experiment_arm_is_recorded_once() -> None:
    optimizer = _optimizer()
    graph = _graph('{"success": true, "reason": "done"}', optimizer)

    final = await graph.run(goal="answer the question", tenant_ctx=T, goal_id="g-ok")
    for task in list(graph._background_tasks):
        await task

    assert final.status == GoalStatus.COMPLETE
    # The verify node recorded it; the terminal hook must not record it again.
    optimizer.on_goal_completed.assert_called_once()
    assert optimizer.on_goal_completed.call_args.kwargs["goal_id"] == "g-ok"


@pytest.mark.asyncio
async def test_failed_goal_outside_an_experiment_records_nothing() -> None:
    optimizer = _optimizer()
    optimizer.get_arm_assignment = AsyncMock(side_effect=RuntimeError("no experiment"))
    graph = _graph('{"success": false, "reason": "wrong answer", "retry": false}', optimizer)

    final = await graph.run(goal="answer the question", tenant_ctx=T, goal_id="g-none")

    assert final.status == GoalStatus.FAILED
    optimizer.on_goal_completed.assert_not_called()


@pytest.mark.asyncio
async def test_recording_failure_never_fails_the_goal_run() -> None:
    optimizer = _optimizer()
    optimizer.on_goal_completed = AsyncMock(side_effect=RuntimeError("redis down"))
    graph = _graph('{"success": false, "reason": "wrong answer", "retry": false}', optimizer)

    final = await graph.run(goal="answer the question", tenant_ctx=T, goal_id="g-err")

    assert final.status == GoalStatus.FAILED
    optimizer.on_goal_completed.assert_awaited_once()
