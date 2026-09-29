"""Goal-tree sub-goal spend is attributed to the parent goal.

Regression: ``execute_sub_goal`` ran each sub-agent graph without a goal id, so
every child got a random one — its LLM spend was charged against an unrelated
per-goal budget (never the parent's cap) and could not be traced back to the
goal that caused it. Children now run under a child id derived from the parent
and carry the parent id as their budget goal, which ``charge_llm_call`` charges.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.goal_tree import execute_sub_goal
from app.agent.state import AgentState, GoalStatus, SubGoal
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio

CTX = TenantContext(tenant_id="t-tree", plan=PlanTier.PROFESSIONAL, api_key_id="k")


async def test_sub_goal_runs_under_parent_derived_ids() -> None:
    seen: dict[str, Any] = {}

    class _Graph:
        async def run(self, **kwargs: Any) -> AgentState:
            seen.update(kwargs)
            return AgentState(goal="child", tenant_ctx=CTX, status=GoalStatus.COMPLETE)

    sg = SubGoal(sub_goal_id="sg-0", description="child", parent_goal_id="parent-123")
    await execute_sub_goal(
        sg, tenant_ctx=CTX, graph_factory=_Graph, semaphore=asyncio.Semaphore(1)
    )

    assert seen["goal_id"] and "parent-123" in seen["goal_id"]
    assert seen["goal_id"] != "parent-123"  # its own checkpoint thread
    ctx = seen["initial_context"]
    assert ctx["parent_goal_id"] == "parent-123"
    assert ctx["_budget_goal_id"] == "parent-123"


async def test_child_llm_spend_is_charged_to_the_parent_goal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent.nodes import llm_cost

    monkeypatch.setattr(
        "app.intelligence.cost_tracker.calculate_cost", lambda *a, **k: 0.25
    )
    controller = MagicMock()
    controller.check_and_record = AsyncMock(return_value=True)
    graph = SimpleNamespace(_cost_controller=controller, _cost_tracker=None, _state_lock=None)
    state = SimpleNamespace(
        goal_id="parent-123-sg-0-abc", context={"_budget_goal_id": "parent-123"}
    )
    resp = SimpleNamespace(
        model="m", usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        input_tokens=10, output_tokens=5,
    )

    await llm_cost.charge_llm_call(
        graph, resp=resp, role="planner", model="m", agent_state=state, tenant_ctx=CTX
    )

    controller.check_and_record.assert_awaited_once()
    assert controller.check_and_record.await_args.kwargs["goal_id"] == "parent-123"
