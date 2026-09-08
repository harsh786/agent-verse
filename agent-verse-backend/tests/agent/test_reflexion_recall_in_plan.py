"""D-17: ReflexionService.recall() must be invoked during the planning phase.

Proves the structured reflexion recall path is wired into the planner mixin,
so evidence-backed lessons are injected alongside the free-text ReflexionWirer
lessons.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agent.graph import AgentGraph, GraphState
from app.agent.state import AgentState
from app.memory.contracts import MemoryRecord
from app.memory.reflexion import ReflexionService
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="d17-t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


def _make_graph(*, reflexion_service: Any = None) -> AgentGraph:
    provider = FakeProvider()
    return AgentGraph(
        planner=provider,
        executor=provider,
        verifier=provider,
        max_iterations=3,
        reflexion_service=reflexion_service,
    )


def _make_state(goal: str = "Deploy to staging") -> GraphState:
    agent_state = AgentState(goal=goal, tenant_ctx=T, goal_id="g-d17")
    state: GraphState = {
        "goal": goal,
        "tenant_ctx": T,
        "iteration": 0,
        "rag_context": "",
        "agent_state": agent_state,
    }
    return state


def _make_memory_record(
    *,
    safe_summary: str = "Always verify permissions before table access",
    confidence: int = 9000,
    evidence_refs: tuple[str, ...] = ("evidence://failure/g0/step2",),
) -> MemoryRecord:
    """Build a minimal valid MemoryRecord for test assertions."""
    from datetime import UTC, datetime

    return MemoryRecord(
        memory_id="mem-d17-001",
        tenant_id="d17-t1",
        memory_kind="reflexion",
        content_ref="ref://reflexion/001",
        safe_summary=safe_summary,
        source_goal_id="g0",
        source_execution_id="exec0",
        evidence_refs=evidence_refs,
        classification="internal",
        confidence=confidence,
        lifecycle_state="active",
        version=1,
        embedding_model="memory-embedding-v1",
        embedding_dimension=1536,
        embedding=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        idempotency_key="idem-d17-001",
    )


@pytest.mark.asyncio
async def test_reflexion_service_recall_called_during_planning() -> None:
    """ReflexionService.recall() must be invoked in _node_plan with correct
    tenant_id and goal text."""
    mock_service = AsyncMock(spec=ReflexionService)
    record = _make_memory_record()
    mock_service.recall.return_value = (record,)

    graph = _make_graph(reflexion_service=mock_service)
    graph._event_callback = AsyncMock()

    state = _make_state("Deploy to staging")
    await graph._node_plan(state)

    mock_service.recall.assert_awaited_once()
    call_kwargs = mock_service.recall.call_args.kwargs
    assert call_kwargs["tenant_id"] == "d17-t1"
    assert call_kwargs["query"] == "Deploy to staging"


@pytest.mark.asyncio
async def test_reflexion_recall_results_injected_into_planner_prompt() -> None:
    """Structured reflexion records must appear in the planner's user content
    so the LLM can reason about past evidence-backed lessons."""
    record = _make_memory_record(
        safe_summary="Verify table permissions before INSERT",
        confidence=8500,
        evidence_refs=("evidence://g0/step2",),
    )
    mock_service = AsyncMock(spec=ReflexionService)
    mock_service.recall.return_value = (record,)

    graph = _make_graph(reflexion_service=mock_service)
    graph._event_callback = AsyncMock()

    # Capture the CompletionRequest sent to the planner LLM
    original_complete = graph._planner.complete

    captured_requests: list[Any] = []

    async def _capture_complete(req: Any) -> Any:
        captured_requests.append(req)
        return await original_complete(req)

    graph._planner.complete = _capture_complete

    state = _make_state("Insert user records")
    await graph._node_plan(state)

    assert captured_requests, "Planner LLM should have been called"
    user_msg = captured_requests[0].messages[-1].content
    assert "Structured reflexion" in user_msg
    assert "Verify table permissions before INSERT" in user_msg
    assert "8500/10000" in user_msg
    assert "evidence://g0/step2" in user_msg


@pytest.mark.asyncio
async def test_reflexion_recall_empty_results_no_injection() -> None:
    """When ReflexionService returns no records, no reflexion block should be
    injected into the planner prompt."""
    mock_service = AsyncMock(spec=ReflexionService)
    mock_service.recall.return_value = ()

    graph = _make_graph(reflexion_service=mock_service)
    graph._event_callback = AsyncMock()

    captured_requests: list[Any] = []
    original_complete = graph._planner.complete

    async def _capture_complete(req: Any) -> Any:
        captured_requests.append(req)
        return await original_complete(req)

    graph._planner.complete = _capture_complete

    state = _make_state("List repos")
    await graph._node_plan(state)

    assert captured_requests
    user_msg = captured_requests[0].messages[-1].content
    assert "Structured reflexion" not in user_msg


@pytest.mark.asyncio
async def test_reflexion_recall_failure_does_not_block_planning() -> None:
    """If ReflexionService.recall() raises, planning must still succeed
    (graceful degradation)."""
    mock_service = AsyncMock(spec=ReflexionService)
    mock_service.recall.side_effect = RuntimeError("DB unavailable")

    graph = _make_graph(reflexion_service=mock_service)
    graph._event_callback = AsyncMock()

    state = _make_state("Run tests")
    result = await graph._node_plan(state)

    # Planning should complete despite reflexion failure
    assert "agent_state" in result
    assert result["agent_state"].plan is not None


@pytest.mark.asyncio
async def test_no_reflexion_service_skips_recall() -> None:
    """When no ReflexionService is wired, planning proceeds without error."""
    graph = _make_graph(reflexion_service=None)
    graph._event_callback = AsyncMock()

    state = _make_state("Analyze data")
    result = await graph._node_plan(state)

    assert "agent_state" in result
    assert result["agent_state"].plan is not None


@pytest.mark.asyncio
async def test_reflexion_recall_scoped_to_tenant() -> None:
    """Recall must use the tenant_id from the execution context, not a global
    or hardcoded value (tenant isolation)."""
    mock_service = AsyncMock(spec=ReflexionService)
    mock_service.recall.return_value = ()

    graph = _make_graph(reflexion_service=mock_service)
    graph._event_callback = AsyncMock()

    other_tenant = TenantContext(
        tenant_id="other-tenant-42", plan=PlanTier.STARTER, api_key_id="k2"
    )
    agent_state = AgentState(goal="Check logs", tenant_ctx=other_tenant, goal_id="g-other")
    state: GraphState = {
        "goal": "Check logs",
        "tenant_ctx": other_tenant,
        "iteration": 0,
        "rag_context": "",
        "agent_state": agent_state,
    }

    await graph._node_plan(state)

    call_kwargs = mock_service.recall.call_args.kwargs
    assert call_kwargs["tenant_id"] == "other-tenant-42"
