"""Regression: the in-graph supervisor deadlocked goals.

Once AgentGraph._goal_service was wired, the supervisor node started running:
it decomposed a goal into (often) one sub-task with the goal's own text,
submit_goal deduplicated it back to the PARENT's id, and the parent waited on
itself forever (goals stuck in "planning"). Sub-goals also re-ran the
supervisor recursively.
"""

from __future__ import annotations

from typing import Any

from app.agent.supervisor import SUBGOAL_MARKER, SubAgentTask, SupervisorAgent


class _Svc:
    def __init__(self, returns: str) -> None:
        self.returns = returns
        self.calls: list[dict[str, Any]] = []

    async def submit_goal(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return {"goal_id": self.returns}

    async def subscribe_events(self, **_: Any):  # pragma: no cover - must not be reached
        raise AssertionError("waited on a sub-goal")
        yield {}


async def test_single_sub_task_is_not_supervised() -> None:
    svc = _Svc("parent")
    sup = SupervisorAgent(planner_provider=None, goal_service=svc)

    async def one(goal: str, tenant_ctx: Any) -> list[SubAgentTask]:
        return [SubAgentTask(task_id="t1", goal=goal)]

    sup._decompose = one  # type: ignore[method-assign]
    res = await sup.run("do x", tenant_ctx=None, parent_goal_id="parent")
    assert svc.calls == [] and res.success is False


async def test_sub_goal_resolving_to_the_parent_is_not_awaited() -> None:
    svc = _Svc("parent")
    sup = SupervisorAgent(planner_provider=None, goal_service=svc)

    async def two(goal: str, tenant_ctx: Any) -> list[SubAgentTask]:
        return [SubAgentTask(task_id="a", goal="a"), SubAgentTask(task_id="b", goal="b")]

    sup._decompose = two  # type: ignore[method-assign]

    async def synth(*_a: Any, **_k: Any) -> str:
        return ""

    sup._synthesize = synth  # type: ignore[method-assign]
    res = await sup.run("do x", tenant_ctx=None, parent_goal_id="parent")
    assert all(t.status == "failed" for t in res.tasks)
    assert all(c["execution_context"][SUBGOAL_MARKER] == "parent" for c in svc.calls)
