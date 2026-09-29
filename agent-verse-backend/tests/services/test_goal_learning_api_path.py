"""API (in-process GoalService) path: terminal goals write canonical Reflexion memory.

Goal 1 completes → a lesson is written. Goal 2's planner recalls it (the real
AgentGraph plan node records the recalled id), goal 2 completes → the lesson's
effectiveness is updated. Dry runs write nothing.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from app.agent.graph import AgentGraph, GraphState
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.memory.goal_learning import RECALLED_MEMORY_IDS_KEY
from app.memory.reflexion import ReflexionService
from app.memory.repository import InMemoryMemoryRepository
from app.providers.fake import FakeProvider
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="api-learn-t", plan=PlanTier.PROFESSIONAL, api_key_id="k")
GOAL = "Reconcile the vendor invoices for March"


class _Loop:
    """Stands in for the agent loop: returns a scripted terminal AgentState."""

    def __init__(self, state: AgentState) -> None:
        self._state = state
        self._pause_gate: Any = None
        self.calls = 0

    async def run(self, **kwargs: Any) -> AgentState:
        self.calls += 1
        self._state.goal_id = kwargs["goal_id"]
        return self._state


def _service(loop: _Loop, reflexion: ReflexionService) -> GoalService:
    class _Svc(GoalService):
        def _make_agent_loop_for_tenant(self, *_a: Any, **_k: Any) -> Any:
            return loop

    return _Svc(app_state=SimpleNamespace(reflexion_service=reflexion))


def _record(goal_id: str, *, dry_run: bool = False) -> GoalRecord:
    return GoalRecord(
        goal_id=goal_id,
        goal_text=GOAL,
        status=GoalStatus.EXECUTING,
        tenant_id=T.tenant_id,
        priority="normal",
        dry_run=dry_run,
        created_at="2026-09-29T00:00:00+00:00",
        agent_id="agent-inv",
    )


def _completed(goal_id: str, context: dict[str, Any] | None = None) -> AgentState:
    state = AgentState(goal=GOAL, tenant_ctx=T, goal_id=goal_id, context=context or {})
    state.status = GoalStatus.COMPLETE
    state.iterations = 1
    state.steps = [
        StepResult(step_id="s1", description="Match invoices to POs", status=StepStatus.COMPLETE)
    ]
    return state


async def _run(svc: GoalService, goal_id: str) -> None:
    from app.agent.tool_context import ToolContext

    await svc._run_agent_loop(
        goal_id=goal_id,
        goal_text=GOAL,
        tenant_ctx=T,
        tool_context=ToolContext(connectors=[], tools=[]),
    )


async def test_completed_goal_writes_memory_and_later_goal_recall_scores_it() -> None:
    repo = InMemoryMemoryRepository()
    reflexion = ReflexionService(repository=repo)

    # Goal 1 completes on the API path → a lesson is written.
    svc1 = _service(_Loop(_completed("goal-api-1")), reflexion)
    svc1._goals["goal-api-1"] = _record("goal-api-1")
    await _run(svc1, "goal-api-1")
    records = await repo.list_records(T.tenant_id)
    assert len(records) == 1
    lesson = records[0]
    assert lesson.source_goal_id == "goal-api-1"
    assert lesson.agent_id == "agent-inv"
    assert lesson.lifecycle_state == "active"

    # Goal 2: the real planner node recalls the lesson and records its id.
    provider = FakeProvider()
    graph = AgentGraph(
        planner=provider,
        executor=provider,
        verifier=provider,
        max_iterations=2,
        reflexion_service=reflexion,
    )
    graph._agent_id = "agent-inv"
    graph._event_callback = AsyncMock()
    plan_state = AgentState(goal=GOAL, tenant_ctx=T, goal_id="goal-api-2")
    gstate: GraphState = {
        "goal": GOAL,
        "tenant_ctx": T,
        "iteration": 0,
        "rag_context": "",
        "agent_state": plan_state,
    }
    planned = (await graph._node_plan(gstate))["agent_state"]
    assert planned.context[RECALLED_MEMORY_IDS_KEY] == [lesson.memory_id]

    # Goal 2 completes → effectiveness of the recalled lesson is recorded.
    svc2 = _service(_Loop(_completed("goal-api-2", dict(planned.context))), reflexion)
    svc2._goals["goal-api-2"] = _record("goal-api-2")
    await _run(svc2, "goal-api-2")
    by_id = {r.memory_id: r for r in await repo.list_records(T.tenant_id)}
    assert by_id[lesson.memory_id].helpful_count == 1
    assert by_id[lesson.memory_id].effectiveness_score > 0
    assert len(by_id) == 2  # goal 2's own lesson


async def test_dry_run_goal_writes_no_memory() -> None:
    repo = InMemoryMemoryRepository()
    svc = _service(_Loop(_completed("goal-dry")), ReflexionService(repository=repo))
    svc._goals["goal-dry"] = _record("goal-dry", dry_run=True)
    await _run(svc, "goal-dry")
    assert await repo.list_records(T.tenant_id) == ()


async def test_unknown_goal_record_fails_closed() -> None:
    repo = InMemoryMemoryRepository()
    svc = _service(_Loop(_completed("goal-ghost")), ReflexionService(repository=repo))
    await _run(svc, "goal-ghost")  # no GoalRecord → dry-run status unknown
    assert await repo.list_records(T.tenant_id) == ()


async def test_failed_goal_is_learned_as_a_failure_lesson() -> None:
    repo = InMemoryMemoryRepository()
    state = AgentState(goal=GOAL, tenant_ctx=T, goal_id="goal-fail")
    state.status = GoalStatus.FAILED
    state.error_message = "permission denied reading the ledger"
    svc = _service(_Loop(state), ReflexionService(repository=repo))
    svc._goals["goal-fail"] = _record("goal-fail")
    await _run(svc, "goal-fail")
    (record,) = await repo.list_records(T.tenant_id)
    assert "failed (auth_failure)" in record.safe_summary
    assert "goal://goal-fail/outcome/failed" in record.evidence_refs


async def test_learning_failure_never_fails_the_goal_run() -> None:
    class _Broken:
        async def learn(self, **_: Any) -> Any:
            raise RuntimeError("memory store down")

    svc = _service(_Loop(_completed("goal-ok")), _Broken())  # type: ignore[arg-type]
    svc._goals["goal-ok"] = _record("goal-ok")
    await _run(svc, "goal-ok")  # must not raise


async def test_persistent_goal_learns_from_the_final_attempt() -> None:
    repo = InMemoryMemoryRepository()
    loop = _Loop(_completed("goal-persist"))
    svc = _service(loop, ReflexionService(repository=repo))
    svc._goals["goal-persist"] = _record("goal-persist")
    await svc._run_agent_loop_persistent(
        goal_id="goal-persist",
        goal_text=GOAL,
        tenant_ctx=T,
        persistence_config={"max_attempts": 1, "base_backoff_seconds": 0.0},
    )
    assert loop.calls == 1
    (record,) = await repo.list_records(T.tenant_id)
    assert record.source_goal_id == "goal-persist"
