"""a01-F006-05 / a01-F007-01: fan-out parents park instead of waiting in their slot.

A supervisor / goal-tree parent on a worker used to stream every sub-goal's
events under ``asyncio.timeout(300)`` while holding its Celery slot. In
continuation mode it dispatches its sub-goals as real goals (with a per-child
timeout), returns ``parked`` without waiting, and a later run (re-queued by the
last sub-goal) folds the finished children in from their goal rows / persisted
events, dispatches what is left and synthesizes in plan order.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent.fanout_ledger import (
    FANOUT_KIND_KEY,
    FANOUT_TASK_KEY,
    MIN_CHILD_TIMEOUT_S,
    ChildGoalState,
    child_timeout_seconds,
    goal_child_timeout_default,
)
from app.agent.goal_tree import advance_goal_tree, goal_tree_child_outcome
from app.agent.state import AgentState, GoalStatus
from app.agent.supervisor import SUBGOAL_MARKER, SupervisorAgent, supervisor_child_outcome
from app.providers.base import CompletionResponse
from app.tenancy.context import PLAN_LIMITS, PlanTier, TenantContext
from tests.agent._fanout_fakes import MemoryLedger

CTX = TenantContext(tenant_id="t-fanout-cont", plan=PlanTier.PROFESSIONAL, api_key_id="k")
PARENT = "p" * 32


class Planner:
    def __init__(self, decomposition: str) -> None:
        self.decomposition = decomposition
        self.calls: list[str] = []

    async def complete(self, request: Any) -> CompletionResponse:
        prompt = " ".join(str(m.content) for m in request.messages)
        self.calls.append(prompt)
        if "decomposer" in prompt:
            content = self.decomposition
        else:
            content = "synthesized: " + prompt[-200:]
        return CompletionResponse(content=content, model="m", input_tokens=1, output_tokens=1)


class GoalService:
    """submit_goal inserts the child's goals row (status planning); never streamed."""

    def __init__(self, ledger: MemoryLedger) -> None:
        self.ledger = ledger
        self.submitted: list[dict[str, Any]] = []

    async def submit_goal(self, **kwargs: Any) -> dict[str, Any]:
        gid = f"child{len(self.submitted)}".ljust(32, "0")
        self.submitted.append(kwargs)
        self.ledger.goal_rows[kwargs["execution_context"][FANOUT_TASK_KEY]] = gid
        self.ledger.child_status[gid] = ChildGoalState(status="planning")
        return {"goal_id": gid}

    def subscribe_events(self, **_: Any) -> Any:
        raise AssertionError("a continuation parent must never wait on a sub-goal's stream")


SUPERVISOR_DECOMP = (
    '{"sub_tasks": [{"goal": "research A"}, {"goal": "research B", "timeout_seconds": 90},'
    ' {"goal": "research C"}]}'
)


def _supervisor(planner: Planner, svc: GoalService, **kw: Any) -> SupervisorAgent:
    return SupervisorAgent(planner_provider=planner, goal_service=svc, continuation=True, **kw)


def _complete(answer: str) -> list[dict[str, Any]]:
    return [{"type": "step_complete", "step": "s", "output": answer}, {"type": "goal_complete"}]


async def test_parent_dispatches_children_and_parks_without_waiting() -> None:
    ledger, planner = MemoryLedger(), Planner(SUPERVISOR_DECOMP)
    svc = GoalService(ledger)
    events: list[dict[str, Any]] = []

    async def _cb(evt: dict[str, Any]) -> None:
        events.append(evt)

    result = await _supervisor(planner, svc, child_timeout_seconds=600).run(
        goal="research", tenant_ctx=CTX, parent_goal_id=PARENT, ledger=ledger,
        event_callback=_cb,
    )

    assert result.parked is True and result.success is False
    assert len(svc.submitted) == 3
    for sub in svc.submitted:
        ctx = sub["execution_context"]
        assert ctx[SUBGOAL_MARKER] == PARENT and ctx[FANOUT_KIND_KEY] == "supervisor"
    # Per-child timeouts: the plan's own (90 s) wins over the goal default (600 s).
    by_goal = {e.spec["goal"]: e.task_key for e in await ledger.load()}
    assert ledger.timeouts[by_goal["research B"]] == 90
    assert ledger.timeouts[by_goal["research A"]] == 600
    assert all(e.status == "dispatched" for e in await ledger.load())
    assert any(e["type"] == "supervisor_waiting_children" for e in events)


async def test_reentry_aggregates_finished_children_in_plan_order() -> None:
    ledger, planner = MemoryLedger(), Planner(SUPERVISOR_DECOMP)
    svc = GoalService(ledger)
    first = await _supervisor(planner, svc).run(
        goal="research", tenant_ctx=CTX, parent_goal_id=PARENT, ledger=ledger
    )
    assert first.parked
    ids = [t.goal_id for t in first.tasks]
    # Children finish in reverse order; one of them fails.
    ledger.finish_child(ids[2], "complete", _complete("C result"))
    ledger.finish_child(ids[1], "failed", [], error="tool exploded")
    ledger.finish_child(ids[0], "complete", _complete("A result"))

    reentry_svc = GoalService(ledger)
    result = await _supervisor(planner, reentry_svc).run(
        goal="research", tenant_ctx=CTX, parent_goal_id=PARENT, ledger=ledger
    )

    assert result.parked is False
    assert reentry_svc.submitted == []  # nothing re-dispatched
    assert [t.goal for t in result.tasks] == ["research A", "research B", "research C"]
    assert [t.status for t in result.tasks] == ["complete", "failed", "complete"]
    assert result.tasks[0].result == "A result" and result.tasks[2].result == "C result"
    assert result.tasks[1].error == "tool exploded"
    assert result.success is False  # every sub-task must succeed
    synth_prompt = planner.calls[-1]
    assert synth_prompt.index("A result") < synth_prompt.index("C result")
    assert "FAILED sub-tasks" in synth_prompt


async def test_reentry_with_a_child_still_running_parks_again_without_resubmitting() -> None:
    ledger, planner = MemoryLedger(), Planner(SUPERVISOR_DECOMP)
    first = await _supervisor(planner, GoalService(ledger)).run(
        goal="research", tenant_ctx=CTX, parent_goal_id=PARENT, ledger=ledger
    )
    ledger.finish_child(first.tasks[0].goal_id, "complete", _complete("A"))
    again_svc = GoalService(ledger)
    again = await _supervisor(planner, again_svc).run(
        goal="research", tenant_ctx=CTX, parent_goal_id=PARENT, ledger=ledger
    )
    assert again.parked and again_svc.submitted == []
    assert [e.status for e in await ledger.load()] == ["complete", "dispatched", "dispatched"]


async def test_max_parallel_dispatches_in_waves() -> None:
    ledger, planner = MemoryLedger(), Planner(SUPERVISOR_DECOMP)
    svc = GoalService(ledger)
    first = await _supervisor(planner, svc, max_parallel=1).run(
        goal="research", tenant_ctx=CTX, parent_goal_id=PARENT, ledger=ledger
    )
    assert first.parked and len(svc.submitted) == 1
    ledger.finish_child(first.tasks[0].goal_id, "complete", _complete("A"))
    second = await _supervisor(planner, svc, max_parallel=1).run(
        goal="research", tenant_ctx=CTX, parent_goal_id=PARENT, ledger=ledger
    )
    assert second.parked and len(svc.submitted) == 2


async def test_crashed_dispatch_reattaches_to_the_existing_child_row() -> None:
    """A child row created just before a crash (ledger never learned its id)."""
    ledger, planner = MemoryLedger(), Planner(SUPERVISOR_DECOMP)
    svc = GoalService(ledger)
    first = await _supervisor(planner, svc).run(
        goal="research", tenant_ctx=CTX, parent_goal_id=PARENT, ledger=ledger
    )
    lost = first.tasks[0]
    ledger.entries[lost.task_id].child_goal_id = None
    ledger.entries[lost.task_id].status = "planned"
    reentry_svc = GoalService(ledger)
    await _supervisor(planner, reentry_svc).run(
        goal="research", tenant_ctx=CTX, parent_goal_id=PARENT, ledger=ledger
    )
    assert reentry_svc.submitted == []
    assert ledger.entries[lost.task_id].child_goal_id == lost.goal_id


async def test_erased_child_row_fails_instead_of_waiting_forever() -> None:
    ledger, planner = MemoryLedger(), Planner(SUPERVISOR_DECOMP)
    first = await _supervisor(planner, GoalService(ledger)).run(
        goal="research", tenant_ctx=CTX, parent_goal_id=PARENT, ledger=ledger
    )
    for t in first.tasks:
        ledger.child_status.pop(t.goal_id)
    result = await _supervisor(planner, GoalService(ledger)).run(
        goal="research", tenant_ctx=CTX, parent_goal_id=PARENT, ledger=ledger
    )
    assert not result.parked
    assert {t.error for t in result.tasks} == {"sub-goal row no longer exists"}


def test_supervisor_child_outcome_reads_the_real_answer() -> None:
    assert supervisor_child_outcome(
        "complete", "", [{"type": "goal_complete", "answer": "final"}]
    ) == ("complete", "final", "")
    assert supervisor_child_outcome("complete", "", _complete("from steps"))[1] == "from steps"
    # Bridged worker envelope {type, payload: {...}}.
    bridged = [{"type": "step_complete", "payload": {"type": "step_complete", "output": "x"}}]
    assert supervisor_child_outcome("complete", "", bridged)[1] == "x"
    status, _, error = supervisor_child_outcome("complete", "", [{"type": "goal_complete"}])
    assert status == "failed" and "without producing any output" in error
    assert supervisor_child_outcome(
        "cancelled", "", [{"type": "goal_cancelled", "reason": "Sub-goal timed out after 30s"}]
    ) == ("failed", "", "Sub-goal timed out after 30s")
    assert supervisor_child_outcome("failed", "boom", []) == ("failed", "", "boom")


def test_child_timeout_resolution_and_bounds() -> None:
    cap = float(PLAN_LIMITS[PlanTier.PROFESSIONAL].goal_timeout_seconds)
    assert child_timeout_seconds(tenant_ctx=CTX) == cap
    assert child_timeout_seconds(default=600, tenant_ctx=CTX) == 600
    assert child_timeout_seconds(requested=90, default=600, tenant_ctx=CTX) == 90
    assert child_timeout_seconds(requested=1, tenant_ctx=CTX) == MIN_CHILD_TIMEOUT_S
    assert child_timeout_seconds(requested=10**9, tenant_ctx=CTX) == cap
    assert child_timeout_seconds(requested=True, default="x", tenant_ctx=CTX) == cap
    assert goal_child_timeout_default({"subgoal_timeout_seconds": 120}, 900) == 120
    assert goal_child_timeout_default({}, 900) == 900
    assert goal_child_timeout_default({}, None) is None


# ── goal tree ─────────────────────────────────────────────────────────────────

TREE_DECOMP = (
    '{"decompose": true, "sub_goals": ['
    '{"id": "sg1", "description": "collect data", "depends_on": []},'
    '{"id": "sg2", "description": "collect more", "depends_on": []},'
    '{"id": "sg3", "description": "analyze", "depends_on": ["sg1", "sg2"]},'
    '{"id": "sg4", "description": "report", "depends_on": ["sg3"]}'
    "]}"
)


async def _advance(ledger: MemoryLedger, svc: GoalService, planner: Planner, **kw: Any) -> Any:
    return await advance_goal_tree(
        "big goal",
        planner=planner,  # type: ignore[arg-type]
        tenant_ctx=CTX,
        parent_goal_id=PARENT,
        goal_service=svc,
        ledger=ledger,  # type: ignore[arg-type]
        agent_id="agent-1",
        **kw,
    )


async def test_goal_tree_children_are_real_goals_dispatched_wave_by_wave() -> None:
    ledger, planner = MemoryLedger(), Planner(TREE_DECOMP)
    svc = GoalService(ledger)

    wave1 = await _advance(ledger, svc, planner, child_timeout_s=300)
    assert wave1.parked and wave1.pending == 4
    assert [s["goal"] for s in svc.submitted] == ["collect data", "collect more"]
    sub = svc.submitted[0]
    assert sub["agent_id"] == "agent-1"
    assert sub["execution_context"][FANOUT_KIND_KEY] == "goal_tree"
    assert sub["execution_context"][SUBGOAL_MARKER] == PARENT
    assert ledger.timeouts["sg1"] == 300

    # Only one of the first wave finished: nothing new is ready.
    ledger.finish_child(ledger.entries["sg2"].child_goal_id or "", "complete", _complete("more"))
    again = await _advance(ledger, svc, planner)
    assert again.parked and len(svc.submitted) == 2

    ledger.finish_child(ledger.entries["sg1"].child_goal_id or "", "complete", _complete("data"))
    wave2 = await _advance(ledger, svc, planner)
    assert wave2.parked and [s["goal"] for s in svc.submitted][2:] == ["analyze"]

    ledger.finish_child(ledger.entries["sg3"].child_goal_id or "", "complete", _complete("an"))
    wave3 = await _advance(ledger, svc, planner)
    assert wave3.parked and [s["goal"] for s in svc.submitted][3:] == ["report"]

    ledger.finish_child(ledger.entries["sg4"].child_goal_id or "", "complete", _complete("rp"))
    done = await _advance(ledger, svc, planner)
    assert not done.parked and len(svc.submitted) == 4
    # Plan order, then the synthesis record.
    assert [sg.sub_goal_id for sg in done.sub_goals] == ["sg1", "sg2", "sg3", "sg4", "synthesis"]
    assert done.sub_goals[0].result == "[s]: data"
    assert done.sub_goals[-1].status is GoalStatus.COMPLETE


async def test_goal_tree_failed_child_skips_its_dependents() -> None:
    ledger, planner = MemoryLedger(), Planner(TREE_DECOMP)
    svc = GoalService(ledger)
    await _advance(ledger, svc, planner)
    ledger.finish_child(ledger.entries["sg1"].child_goal_id or "", "failed", [], error="nope")
    ledger.finish_child(ledger.entries["sg2"].child_goal_id or "", "complete", _complete("ok"))
    done = await _advance(ledger, svc, planner)
    assert not done.parked and len(svc.submitted) == 2  # sg3 / sg4 never dispatched
    by_id = {sg.sub_goal_id: sg for sg in done.sub_goals}
    assert by_id["sg1"].error == "nope"
    assert "dependency sg1" in by_id["sg3"].error
    assert "dependency sg3" in by_id["sg4"].error


async def test_goal_tree_no_decomposition_runs_the_plan_normally() -> None:
    ledger = MemoryLedger()
    out = await _advance(ledger, GoalService(ledger), Planner('{"decompose": false}'))
    assert not out.parked and out.sub_goals == []


def test_goal_tree_child_outcome_shape() -> None:
    assert goal_tree_child_outcome("complete", "", _complete("x")) == ("complete", "[s]: x", "")
    assert goal_tree_child_outcome("cancelled", "timed out", []) == ("failed", "", "timed out")


# ── the graph ends the run when a fan-out parks ──────────────────────────────


class _GraphProvider:
    """Planner / executor / verifier for a graph whose supervisor parks."""

    _default_model = "m"

    async def complete(self, request: Any) -> CompletionResponse:
        prompt = " ".join(str(m.content) for m in request.messages)
        if "decomposer" in prompt:
            content = SUPERVISOR_DECOMP
        elif "steps" in prompt.lower():
            raise AssertionError("a parked parent must not plan")
        else:
            content = "{}"
        return CompletionResponse(content=content, model="m", input_tokens=1, output_tokens=1)


async def test_parked_supervisor_ends_the_graph_run_waiting_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent import fanout_ledger
    from app.agent.graph import AgentGraph

    ledger = MemoryLedger()
    monkeypatch.setattr(fanout_ledger, "ledger_for", lambda *a, **k: ledger)
    p = _GraphProvider()
    graph = AgentGraph(planner=p, executor=p, verifier=p, enable_supervisor=True)
    svc = GoalService(ledger)
    graph._goal_service = svc  # type: ignore[attr-defined]
    graph._fanout_continuations = True  # type: ignore[attr-defined]
    graph._subgoal_timeout_s = 900  # type: ignore[attr-defined]
    events: list[dict[str, Any]] = []

    async def _cb(evt: dict[str, Any]) -> None:
        events.append(evt)

    state: AgentState = await graph.run(
        goal="research three things", tenant_ctx=CTX, goal_id=PARENT, event_callback=_cb
    )

    assert state.status is GoalStatus.WAITING_CHILDREN
    assert state.context.get("fanout_parked") == "supervisor"
    assert not state.context.get("supervisor_applied")
    assert len(svc.submitted) == 3 and state.steps == []
    types = [e.get("type") for e in events]
    assert "goal_waiting_children" in types and "goal_failed" not in types


class _TreeGraphProvider:
    _default_model = "m"

    def __init__(self) -> None:
        self.executed: list[str] = []

    async def complete(self, request: Any) -> CompletionResponse:
        prompt = " ".join(str(m.content) for m in request.messages)
        if "goal decomposer" in prompt:
            content = TREE_DECOMP
        else:
            content = '{"steps": ["collect data", "collect more", "analyze", "report"]}'
        return CompletionResponse(content=content, model="m", input_tokens=1, output_tokens=1)

    async def stream_tokens(self, request: Any, on_token: Any) -> Any:
        self.executed.append(str(request.messages[-1].content))
        raise AssertionError("a parked goal-tree parent must not execute its plan in-process")


async def test_parked_goal_tree_ends_the_graph_run_waiting_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agent import fanout_ledger
    from app.agent.graph import AgentGraph

    ledger = MemoryLedger()
    monkeypatch.setattr(fanout_ledger, "ledger_for", lambda *a, **k: ledger)
    p = _TreeGraphProvider()
    graph = AgentGraph(planner=p, executor=p, verifier=p, enable_goal_tree=True)
    svc = GoalService(ledger)
    graph._goal_service = svc  # type: ignore[attr-defined]
    graph._fanout_continuations = True  # type: ignore[attr-defined]

    state: AgentState = await graph.run(goal="big goal", tenant_ctx=CTX, goal_id=PARENT)

    assert state.status is GoalStatus.WAITING_CHILDREN, state.error_message
    assert state.context.get("fanout_parked") == "goal_tree"
    assert [s["goal"] for s in svc.submitted] == ["collect data", "collect more"]
    assert p.executed == [] and state.steps == []
