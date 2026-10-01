"""CORE-31: a supervisor parent redelivered after a crash never re-dispatches.

``supervisor_applied`` was set only in memory after every sub-goal finished and
``submit_goal`` got no idempotency key, so a redelivered parent re-decomposed
and launched duplicate sub-goals, repeating completed side effects. The
decomposition and child ids now live in the durable fan-out ledger.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.agent.fanout_ledger import FANOUT_TASK_KEY, LedgerEntry
from app.agent.supervisor import SupervisorAgent
from app.providers.base import CompletionResponse
from app.tenancy.context import PlanTier, TenantContext
from tests.agent._fanout_fakes import MemoryLedger

CTX = TenantContext(tenant_id="t-sup-resume", plan=PlanTier.PROFESSIONAL, api_key_id="k")
PARENT = "p" * 32


class Planner:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: Any) -> CompletionResponse:
        self.calls += 1
        if "decomposer" in request.messages[0].content:
            content = '{"sub_tasks": [{"goal": "research A"}, {"goal": "research B"}]}'
        else:
            content = "synthesized"
        return CompletionResponse(content=content, model="m", input_tokens=1, output_tokens=1)


class GoalService:
    def __init__(self, ledger: MemoryLedger, *, hang: bool) -> None:
        self.ledger = ledger
        self.hang = hang
        self.submitted: list[dict[str, Any]] = []

    async def submit_goal(self, **kwargs: Any) -> dict[str, Any]:
        gid = f"child{len(self.submitted)}".ljust(32, "0")
        self.submitted.append(kwargs)
        # submit_goal inserts the goals row (parent + task key) itself.
        self.ledger.goal_rows[kwargs["execution_context"][FANOUT_TASK_KEY]] = gid
        return {"goal_id": gid}

    async def subscribe_events(self, goal_id: str, tenant_ctx: Any) -> AsyncIterator[dict]:
        if self.hang:
            await asyncio.Event().wait()
        yield {"type": "goal_complete", "answer": f"answer from {goal_id}"}


async def _run(planner: Planner, svc: GoalService, ledger: MemoryLedger) -> Any:
    sup = SupervisorAgent(planner_provider=planner, goal_service=svc)
    return await sup.run(goal="research", tenant_ctx=CTX, parent_goal_id=PARENT, ledger=ledger)


@pytest.mark.asyncio
async def test_redelivered_parent_reattaches_to_the_same_children() -> None:
    ledger = MemoryLedger()
    planner = Planner()
    crashed = GoalService(ledger, hang=True)
    task = asyncio.create_task(_run(planner, crashed, ledger))
    for _ in range(100):
        if len(crashed.submitted) == 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()  # the worker dies while the children run
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(crashed.submitted) == 2
    decompositions = planner.calls

    redelivered = GoalService(ledger, hang=False)
    result = await _run(planner, redelivered, ledger)

    assert redelivered.submitted == []  # no duplicate sub-goals
    assert planner.calls == decompositions + 1  # synthesis only, no re-decomposition
    assert result.success
    assert {t.goal_id for t in result.tasks} == set(ledger.goal_rows.values())
    assert all(e.status == "complete" for e in ledger.entries.values())


@pytest.mark.asyncio
async def test_crash_between_submit_and_ledger_write_finds_the_goal_row() -> None:
    ledger = MemoryLedger()
    planner = Planner()

    async def _lost_write(task_key: str, child_goal_id: str) -> None:
        raise ConnectionError("ledger unreachable")

    ledger.mark_dispatched = _lost_write  # type: ignore[method-assign]
    crashed = GoalService(ledger, hang=True)
    task = asyncio.create_task(_run(planner, crashed, ledger))
    for _ in range(100):
        if len(crashed.submitted) == 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    redelivered = GoalService(ledger, hang=False)
    result = await _run(planner, redelivered, ledger)
    assert redelivered.submitted == []
    assert result.success


@pytest.mark.asyncio
async def test_finished_children_are_not_waited_on_again() -> None:
    ledger = MemoryLedger()
    planner = Planner()
    await ledger.plan([
        LedgerEntry(task_key="a", position=0, spec={"goal": "research A"},
                    child_goal_id="c" * 32, status="complete", result="A done"),
        LedgerEntry(task_key="b", position=1, spec={"goal": "research B"},
                    child_goal_id="d" * 32, status="complete", result="B done"),
    ])
    svc = GoalService(ledger, hang=True)  # any subscribe would hang the test
    result = await asyncio.wait_for(_run(planner, svc, ledger), 5)
    assert svc.submitted == []
    assert [t.result for t in result.tasks] == ["A done", "B done"]


@pytest.mark.asyncio
async def test_unwritable_plan_refuses_the_fan_out() -> None:
    ledger = MemoryLedger()

    async def _down(entries: list[LedgerEntry]) -> list[LedgerEntry]:
        raise ConnectionError("db down")

    ledger.plan = _down  # type: ignore[method-assign]
    svc = GoalService(ledger, hang=False)
    with pytest.raises(ConnectionError):
        await _run(Planner(), svc, ledger)
    assert svc.submitted == []
