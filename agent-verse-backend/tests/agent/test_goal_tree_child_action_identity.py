"""a01-F007-01 (partial fix): a re-run goal-tree child keeps its action identity.

Each child runs under ``<parent>-<sub_goal_id>-<uuid8>``, a new id per
execution. The OI-1 action ledger (Redis hash per goal id) and the a06-F101-04
idempotency keys (``goal:<goal_id>:<fingerprint>``) were keyed by that id, so a
child interrupted by a worker crash and re-run when the parent was redelivered
re-dispatched the side-effecting calls it had already made, under NEW
idempotency keys — the remote system could not dedupe them. The child now
carries a stable action scope ``<parent>:<sub_goal_id>`` that the ledger and the
idempotency key use; its checkpoint thread id stays unique per execution.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.agent.goal_action_ledger import call_fingerprint
from app.agent.goal_tree import execute_sub_goal
from app.agent.graph import AgentGraph
from app.agent.state import AgentState, GoalStatus, SubGoal
from app.agent.tool_context import ToolRef
from app.mcp.client import current_idempotency_key
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-tree-ids", plan=PlanTier.PROFESSIONAL, api_key_id="k")
WRITE = ToolRef(
    server_id="srv-1", server_name="crm", name="create_ticket", description="", input_schema={}
)
ARGS = {"title": "Refund order ORD-7"}


async def _child_runs(times: int) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    class _Graph:
        async def run(self, **kwargs: Any) -> AgentState:
            seen.append(kwargs)
            return AgentState(goal="child", tenant_ctx=CTX, status=GoalStatus.COMPLETE)

    for _ in range(times):
        sg = SubGoal(sub_goal_id="sg-1", description="file the ticket", parent_goal_id="p-42")
        await execute_sub_goal(
            sg, tenant_ctx=CTX, graph_factory=_Graph, semaphore=asyncio.Semaphore(1)
        )
    return seen


async def test_rerun_child_has_a_new_thread_but_the_same_action_scope() -> None:
    first, second = await _child_runs(2)
    assert first["goal_id"] != second["goal_id"]  # its own checkpoint thread per run
    assert first["initial_context"]["_action_scope_id"] == "p-42:sg-1"
    assert second["initial_context"]["_action_scope_id"] == "p-42:sg-1"


def _child_state(run: int) -> AgentState:
    state = AgentState(goal="file the ticket", tenant_ctx=CTX)
    state.goal_id = f"p-42-sg-1-run{run}"
    state.context["_action_scope_id"] = "p-42:sg-1"
    return state


def test_the_idempotency_key_survives_a_rerun_of_the_child() -> None:
    keys = []
    for run in (1, 2):
        with AgentGraph._tool_idempotency_scope(_child_state(run), WRITE, ARGS):
            keys.append(current_idempotency_key())
    fp = call_fingerprint("srv-1", "create_ticket", ARGS)
    assert keys == [f"goal:p-42:sg-1:{fp[:32]}"] * 2


def test_a_top_level_goal_keeps_its_own_goal_id_as_scope() -> None:
    state = AgentState(goal="g", tenant_ctx=CTX)
    state.goal_id = "goal-top"
    with AgentGraph._tool_idempotency_scope(state, WRITE, ARGS):
        assert (current_idempotency_key() or "").startswith("goal:goal-top:")


@pytest.mark.asyncio
async def test_a_rerun_child_replays_a_call_its_interrupted_run_executed() -> None:
    from fakeredis import FakeAsyncRedis

    redis = FakeAsyncRedis(decode_responses=True)
    graph = AgentGraph(planner=FakeProvider(), executor=FakeProvider(), verifier=FakeProvider())
    graph._hitl_gateway = type("H", (), {"_redis": redis})()
    fp = call_fingerprint("srv-1", "create_ticket", ARGS)

    first = graph._goal_action_ledger(_child_state(1), CTX)
    await first.record_executed(
        fp, step_id="s1", tool="create_ticket", server_id="srv-1", arguments=ARGS, output="T-1"
    )
    rerun = graph._goal_action_ledger(_child_state(2), CTX)
    entry = await rerun.executed(fp)
    assert entry is not None and entry["output"] == "T-1"
