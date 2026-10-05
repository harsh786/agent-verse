"""OI-1: an approved, executed side-effecting call is done — never re-run, never re-asked.

Live (MCP-MONGO-HITL): an approved ``mongodb_delete_one`` ran, then every replan
planned it again, filed new approvals (19-22 in one goal) and re-ran it
(``deleted: 0``) until the goal failed although the document was gone.
"""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import pytest

from app.agent.goal_action_ledger import LEDGER_CONTEXT_KEY
from app.agent.graph import AgentGraph
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.agent.tool_context import ToolContext, ToolRef
from app.governance.hitl import ApprovalStatus
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="oi1-t1", plan=PlanTier.ENTERPRISE, api_key_id="oi1")
DELETE_ARGS = {"collection": "orders", "filter": {"order_no": "ORD-1-DEL"}}


class _Human:
    """An approval gateway whose human decides every request with ``decision``."""

    def __init__(self, decision: ApprovalStatus = ApprovalStatus.APPROVED) -> None:
        self.decision = decision
        self.filed: list[str] = []
        self._redis: Any = None

    async def request_approval_async(self, *, action: str, **_kw: Any) -> str:
        self.filed.append(action)
        return f"req-{len(self.filed)}"

    async def wait_for_approval(self, request_id: str, **_kw: Any) -> ApprovalStatus:
        return self.decision

    def list_pending(self, **_kw: Any) -> list[Any]:
        return []


class _Mongo:
    """Records every dispatched call; the first delete deletes, later ones find nothing."""

    def __init__(self, *, fail_first: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail_first = fail_first

    async def call_tool(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        n = len(self.calls)
        fail = self._fail_first and n == 1

        class Result:
            success = not fail
            output = {"deleted": 1 if n == 1 or (self._fail_first and n == 2) else 0}
            error = "connection reset" if fail else ""

        return Result()


def _tool_context() -> ToolContext:
    return ToolContext(
        connectors=[],
        tools=[
            ToolRef(
                server_id="mongo-1",
                server_name="orders-db",
                name="mongodb_delete_one",
                description="delete one document",
                input_schema={},
            )
        ],
    )


def _call(args: dict[str, Any] | None = None) -> str:
    import json

    return json.dumps({"tool": "mongodb_delete_one", "arguments": args or DELETE_ARGS})


def _graph(executor: FakeProvider, human: _Human, mcp: _Mongo, **kw: Any) -> AgentGraph:
    return AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=executor,
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        hitl_gateway=human,
        mcp_client=mcp,
        autonomy_mode="supervised",
        **kw,
    )


def _state(goal_id: str = "g-oi1") -> AgentState:
    state = AgentState(goal="remove the cancelled test order", tenant_ctx=T)
    state.goal_id = goal_id
    state.context["tool_context"] = _tool_context()
    return state


async def _run_step(graph: AgentGraph, state: AgentState, step: str) -> str:
    state.steps.append(StepResult(description=step, status=StepStatus.RUNNING))
    return await graph._execute_step(step, state, T)


def _tool_requests(human: _Human) -> list[str]:
    return [a for a in human.filed if a == "mongodb_delete_one"]


async def test_replanned_identical_call_is_not_rerun_nor_reapproved() -> None:
    human, mongo = _Human(), _Mongo()
    graph = _graph(FakeProvider(responses=[_call(), _call()]), human, mongo)
    state = _state()

    first = await _run_step(graph, state, "Run the removal of order ORD-1-DEL")
    # A replan words the step differently but issues the identical call.
    second = await _run_step(graph, state, "Run the removal of order ORD-1-DEL again")

    assert len(mongo.calls) == 1
    assert len(_tool_requests(human)) == 1
    assert "'deleted': 1" in first
    assert "ALREADY EXECUTED" in second and "'deleted': 1" in second
    executed = state.context[LEDGER_CONTEXT_KEY]["executed"]
    (entry,) = executed.values()
    assert entry["tool"] == "mongodb_delete_one"
    assert entry["step_id"] == state.steps[0].step_id


async def test_identical_step_text_reuses_the_step_level_approval() -> None:
    human, mongo = _Human(), _Mongo()
    graph = _graph(FakeProvider(responses=[_call(), _call()]), human, mongo)
    state = _state()
    step = "Delete the cancelled order ORD-1-DEL with mongodb_delete_one"

    await _run_step(graph, state, step)
    filed_after_first = list(human.filed)
    await _run_step(graph, state, step)

    # First run: the step gate (high-risk "delete") + the tool gate.
    assert filed_after_first.count(step) == 1
    assert len(_tool_requests(human)) == 1
    # Second run of the same step: nothing new filed, nothing re-run.
    assert human.filed == filed_after_first
    assert len(mongo.calls) == 1


async def test_different_arguments_need_their_own_approval_and_run() -> None:
    human, mongo = _Human(), _Mongo()
    other = {"collection": "orders", "filter": {"order_no": "ORD-2-DEL"}}
    graph = _graph(FakeProvider(responses=[_call(), _call(other)]), human, mongo)
    state = _state()

    await _run_step(graph, state, "Run the removal of order ORD-1-DEL")
    await _run_step(graph, state, "Run the removal of order ORD-2-DEL")

    assert len(mongo.calls) == 2
    assert len(_tool_requests(human)) == 2


async def test_approval_is_reused_for_a_retry_after_a_failed_call() -> None:
    human, mongo = _Human(), _Mongo(fail_first=True)
    graph = _graph(FakeProvider(responses=[_call(), _call()]), human, mongo)
    state = _state()

    await _run_step(graph, state, "Run the removal of order ORD-1-DEL")
    retry = await _run_step(graph, state, "Run the removal of order ORD-1-DEL (retry)")

    # The failed call was not "done": it ran again, on the approval already given.
    assert len(mongo.calls) == 2
    assert len(_tool_requests(human)) == 1
    assert "'deleted': 1" in retry and "ALREADY EXECUTED" not in retry


async def test_rejected_call_is_never_reused() -> None:
    human, mongo = _Human(ApprovalStatus.REJECTED), _Mongo()
    graph = _graph(FakeProvider(responses=[_call(), _call()]), human, mongo)
    state = _state()

    for step in ("Run the removal of order ORD-1-DEL", "Run it once more"):
        with pytest.raises(PermissionError, match="rejected"):
            await _run_step(graph, state, step)

    assert mongo.calls == []
    # Each attempt asked a human again: a rejection is never reused.
    assert len(human.filed) == 2


async def test_other_goal_is_not_affected() -> None:
    human, mongo = _Human(), _Mongo()
    graph = _graph(FakeProvider(responses=[_call(), _call()]), human, mongo)

    await _run_step(graph, _state("g-a"), "Run the removal of order ORD-1-DEL")
    await _run_step(graph, _state("g-b"), "Run the removal of order ORD-1-DEL")

    assert len(mongo.calls) == 2
    assert len(_tool_requests(human)) == 2


async def test_shared_redis_ledger_spans_replicas() -> None:
    """A second replica (fresh state, new graph) of the same goal sees the record."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    mongo = _Mongo()
    human_a, human_b = _Human(), _Human()
    human_a._redis = redis
    human_b._redis = redis
    graph_a = _graph(FakeProvider(responses=[_call()]), human_a, mongo)
    graph_b = _graph(FakeProvider(responses=[_call()]), human_b, mongo)

    await _run_step(graph_a, _state(), "Run the removal of order ORD-1-DEL")
    out = await _run_step(graph_b, _state(), "Run the removal of order ORD-1-DEL now")

    assert len(mongo.calls) == 1
    assert human_b.filed.count("mongodb_delete_one") == 0
    assert "ALREADY EXECUTED" in out


async def test_unreadable_ledger_never_counts_as_done() -> None:
    class _Broken:
        async def hget(self, *_a: Any) -> Any:
            raise ConnectionError("redis down")

        async def hset(self, *_a: Any) -> Any:
            raise ConnectionError("redis down")

        async def expire(self, *_a: Any) -> Any:
            raise ConnectionError("redis down")

    mongo = _Mongo()
    human_a, human_b = _Human(), _Human()
    human_a._redis = _Broken()
    human_b._redis = _Broken()
    graph_a = _graph(FakeProvider(responses=[_call()]), human_a, mongo)
    graph_b = _graph(FakeProvider(responses=[_call()]), human_b, mongo)

    await _run_step(graph_a, _state(), "Run the removal of order ORD-1-DEL")
    await _run_step(graph_b, _state(), "Run the removal of order ORD-1-DEL")

    # No shared record could be read: the second replica asks a human again.
    assert human_b.filed.count("mongodb_delete_one") == 1


class _VerifierFailsOnce(FakeProvider):
    """Rejects the first verification (forcing a replan), accepts every later one."""

    async def complete(self, request: Any) -> Any:
        from app.providers.base import CompletionResponse

        self.call_history.append(request)
        first = len(self.call_history) == 1
        content = (
            '{"success": false, "reason": "cannot confirm the deletion", "retry": true}'
            if first
            else '{"success": true, "reason": "deleted: 1 recorded"}'
        )
        return CompletionResponse(content=content, model=request.model)


async def test_full_goal_approve_once_executes_once_and_completes() -> None:
    """plan → delete (approved) → verify fails → replan → same delete → complete."""
    plan = '{"steps": ["Run the removal of order ORD-1-DEL", "Report the outcome"]}'
    planner = FakeProvider(responses=[plan])
    report = "{'deleted': 1}"
    executor = FakeProvider(responses=[_call(), report])
    human, mongo = _Human(), _Mongo()
    events: list[dict[str, Any]] = []

    async def _on_event(event: dict[str, Any]) -> None:
        events.append(event)

    graph = AgentGraph(
        planner=planner,
        executor=executor,
        verifier=_VerifierFailsOnce(),
        hitl_gateway=human,
        mcp_client=mongo,
        autonomy_mode="supervised",
        max_iterations=5,
    )

    state = await graph.run(
        goal="Remove cancelled test order ORD-1-DEL",
        tenant_ctx=T,
        initial_context={"tool_context": _tool_context()},
        event_callback=_on_event,
    )

    assert state.status == GoalStatus.COMPLETE, state.error_message
    assert state.iterations >= 2  # it really replanned
    assert len(mongo.calls) == 1
    assert len(_tool_requests(human)) == 1
    # The replanned removal step reused its approval instead of asking again.
    assert human.filed.count("Run the removal of order ORD-1-DEL") == 1
    types = [e.get("type") for e in events]
    assert "tool_call_already_executed" in types
    assert "approval_reused" in types
