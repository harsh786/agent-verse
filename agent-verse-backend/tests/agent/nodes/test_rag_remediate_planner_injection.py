"""Regression test: RAG-remediated context must reach the next planning pass.

``RAGMixin._node_rag_remediate`` re-retrieves context after a verifier-detected
context gap and stores it in ``agent_state.context["remediation_context"]``
(see its docstring: "3. Inject as [Remediation context: iteration N] into next
plan"). ``RoutingMixin._route`` wires a dedicated "rag_remediate" -> "plan"
edge (app/agent/graph.py) specifically so this happens before the next
planning pass.

Before the fix, ``PlannerMixin._node_plan`` never read that key back out of
``agent_state.context`` — the remediation retrieval ran (spending latency and
retrieval cost) but its result was silently dropped, so the planner replanned
with the exact same (insufficient) context that caused the gap, defeating the
whole remediation path. A real-world symptom: a goal that fails verification
with "insufficient context about X" burns a remediation round that fetches
the missing fact, then immediately fails again with the same feedback because
the planner never saw the fetched fact.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.graph import AgentGraph, GraphState
from app.agent.state import AgentState
from app.providers.fake import FakeProvider
from app.rag.agentic.retriever_tool import RetrievalResult
from app.tenancy.context import PlanTier, TenantContext


def _tenant() -> TenantContext:
    return TenantContext(tenant_id="test-tenant", plan=PlanTier.FREE, api_key_id="test-key")


def _make_graph(planner: FakeProvider) -> AgentGraph:
    g = AgentGraph(planner=planner, executor=FakeProvider(), verifier=FakeProvider())
    # RetrieverTool() requires a non-None gateway at construction time even
    # though .retrieve() itself is patched out below.
    g._retrieval_gateway = MagicMock()
    g._agent_collection_ids = ["col-1"]
    return g


@pytest.mark.asyncio
async def test_remediated_context_is_injected_into_next_plan_prompt():
    planner = FakeProvider(responses=['{"steps": ["do the thing"]}'])
    g = _make_graph(planner)
    tenant_ctx = _tenant()

    agent_state = AgentState(goal="What is the capital of Freedonia?", tenant_ctx=tenant_ctx)
    agent_state.verification_feedback = "insufficient information about the topic"
    agent_state.context["allow_rag_remediation"] = True

    state: GraphState = {
        "goal": agent_state.goal,
        "tenant_ctx": tenant_ctx,
        "agent_state": agent_state,
        "iteration": 1,
    }

    fake_result = RetrievalResult(
        query="capital of Freedonia",
        source="knowledge_base",
        strategy_used="hybrid",
        confidence=0.9,
        context_text="Freedonia's capital is Fredonia City.",
    )

    with patch(
        "app.rag.agentic.retriever_tool.RetrieverTool.retrieve",
        new=AsyncMock(return_value=fake_result),
    ):
        await g._node_rag_remediate(state)

    # Sanity: the remediation node did its job and stashed the new context.
    assert "Fredonia City" in agent_state.context.get("remediation_context", "")

    await g._node_plan(state)

    assert planner.call_history, "planner should have been called"
    sent_prompt = planner.call_history[-1].messages[-1].content
    assert "Fredonia City" in sent_prompt, (
        "remediated RAG context must be forwarded into the next planner prompt "
        "— it was retrieved specifically to fill the gap that failed "
        "verification, so silently dropping it makes the remediation round a "
        "no-op that just repeats the same failing plan"
    )


@pytest.mark.asyncio
async def test_remediation_context_is_consumed_once_not_leaked_to_later_plans():
    """The remediation context must not leak into unrelated later replans."""
    planner = FakeProvider(responses=['{"steps": ["do the thing"]}'] * 2)
    g = _make_graph(planner)
    tenant_ctx = _tenant()

    agent_state = AgentState(goal="What is the capital of Freedonia?", tenant_ctx=tenant_ctx)
    agent_state.context["allow_rag_remediation"] = True
    agent_state.context["remediation_context"] = (
        "[Remediation context — iteration 1]\nFreedonia's capital is Fredonia City."
    )

    state: GraphState = {
        "goal": agent_state.goal,
        "tenant_ctx": tenant_ctx,
        "agent_state": agent_state,
        "iteration": 1,
    }

    await g._node_plan(state)
    first_prompt = planner.call_history[-1].messages[-1].content
    assert "Fredonia City" in first_prompt

    # A later, unrelated replan (no new remediation this round) must not still
    # be carrying the old remediation text.
    await g._node_plan(state)
    second_prompt = planner.call_history[-1].messages[-1].content
    assert "Fredonia City" not in second_prompt
