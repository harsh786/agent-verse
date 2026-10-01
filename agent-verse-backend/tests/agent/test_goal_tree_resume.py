"""CORE-10: a redelivered goal-tree parent never re-runs a finished child.

Children ran under synthetic ids with checkpointing disabled and were re-run
from the parent's checkpoint, so a parent redelivered after a crash repeated
children whose side effects had already happened. Each child's completion is
now recorded in the durable fan-out ledger as it finishes; a resumed parent
reuses the stored decomposition and skips completed children.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.agent.goal_tree import execute_goal_tree
from app.agent.state import AgentState, GoalStatus
from app.providers.base import CompletionResponse
from app.tenancy.context import PlanTier, TenantContext
from tests.agent._fanout_fakes import MemoryLedger

CTX = TenantContext(tenant_id="t-tree-resume", plan=PlanTier.PROFESSIONAL, api_key_id="k")
PARENT = "q" * 32
_DECOMPOSITION = (
    '{"decompose": true, "sub_goals": ['
    '{"id": "sg-1", "description": "send the invoice"},'
    '{"id": "sg-2", "description": "file the receipt", "depends_on": ["sg-1"]}]}'
)


class _Planner:
    def __init__(self) -> None:
        self.decompositions = 0

    async def complete(self, request: Any) -> CompletionResponse:
        if request.messages[0].role == "system":
            self.decompositions += 1
            return CompletionResponse(content=_DECOMPOSITION, model="m")
        return CompletionResponse(content="synthesized", model="m")


class _ChildGraph:
    def __init__(self, ran: list[str], crash_on: str | None) -> None:
        self.ran, self.crash_on = ran, crash_on

    async def run(self, *, goal: str, tenant_ctx: Any, **_: Any) -> AgentState:
        if goal == self.crash_on:
            raise asyncio.CancelledError  # the worker dies mid-child
        self.ran.append(goal)
        state = AgentState(goal=goal, tenant_ctx=tenant_ctx)
        state.status = GoalStatus.COMPLETE
        return state


async def _tree(planner: _Planner, ledger: MemoryLedger, ran: list[str],
                crash_on: str | None = None) -> Any:
    return await execute_goal_tree(
        "close the books", planner=planner, tenant_ctx=CTX, parent_goal_id=PARENT,
        graph_factory=lambda: _ChildGraph(ran, crash_on), ledger=ledger,
    )


@pytest.mark.asyncio
async def test_completed_child_is_not_rerun_after_a_crash() -> None:
    ledger, planner = MemoryLedger(), _Planner()
    ran: list[str] = []
    with pytest.raises(asyncio.CancelledError):
        await _tree(planner, ledger, ran, crash_on="file the receipt")
    assert ran == ["send the invoice"]
    assert ledger.entries["sg-1"].status == "complete"

    ran_after: list[str] = []
    results = await _tree(planner, ledger, ran_after)

    assert ran_after == ["file the receipt"]  # the invoice is NOT sent again
    assert planner.decompositions == 1  # the stored decomposition is reused
    assert [sg.sub_goal_id for sg in results][:2] == ["sg-1", "sg-2"]
    assert all(sg.status is not GoalStatus.FAILED for sg in results)
    assert ledger.entries["sg-2"].status == "complete"


@pytest.mark.asyncio
async def test_unwritable_plan_runs_no_child() -> None:
    ledger = MemoryLedger()

    async def _down(entries: Any) -> Any:
        raise ConnectionError("db down")

    ledger.plan = _down  # type: ignore[method-assign]
    ran: list[str] = []
    with pytest.raises(ConnectionError):
        await _tree(_Planner(), ledger, ran)
    assert ran == []
