"""MEM-48: a knowledge-graph prefetch failure during planning is surfaced
(memory_degraded + event), never silently suppressed; planning continues."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from app.agent.graph import AgentGraph, GraphState
from app.agent.state import AgentState
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-kg", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _BrokenGraphStore:
    async def aquery_nodes(self, **_kw: Any) -> list[Any]:
        raise ConnectionError("graph store down")


async def test_graph_prefetch_failure_marks_memory_degraded() -> None:
    p = FakeProvider()
    graph = AgentGraph(
        planner=p, executor=p, verifier=p, knowledge_graph_store=_BrokenGraphStore()
    )
    events: list[dict[str, Any]] = []
    graph._event_callback = AsyncMock(side_effect=lambda e: events.append(e))
    agent_state = AgentState(goal="map our services", tenant_ctx=T, goal_id="g1")
    state: GraphState = {
        "goal": agent_state.goal, "tenant_ctx": T, "iteration": 0,
        "rag_context": "", "agent_state": agent_state,
    }
    result = await graph._node_plan(state)
    assert "graph_facts" in agent_state.context.get("memory_degraded", [])
    assert result["agent_state"].plan  # planning continued
