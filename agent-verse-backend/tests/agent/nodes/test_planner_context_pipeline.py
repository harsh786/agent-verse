"""MEM-18: the planner's ContextPipeline runs without RAG hits, reports errors,
and looks up the semantic cache by similarity (not an exact text key)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.providers.base import EmbedResponse
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="ctx-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


def _graph(planner: FakeProvider, **kw: Any) -> AgentGraph:
    return AgentGraph(
        planner=planner,
        executor=FakeProvider(responses=["done"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        **kw,
    )


def _prompt(planner: FakeProvider) -> str:
    return "\n".join(m.content for m in planner.call_history[0].messages)


async def test_zero_rag_chunks_still_injects_long_term_memory() -> None:
    planner = FakeProvider(responses=['["Step 1: go"]'])
    graph = _graph(planner)
    st = AgentState(goal="deploy the billing service", tenant_ctx=T)
    st.context["_long_term_memory_records"] = [
        {"content": "Billing deploys need the finance on-call to approve", "confidence": 0.9}
    ]
    await graph._node_plan({"agent_state": st, "tenant_ctx": T})
    assert "finance on-call to approve" in _prompt(planner)
    assert "context_pipeline_failed" not in st.context


async def test_raising_pipeline_sets_failure_flag_and_event() -> None:
    planner = FakeProvider(responses=['["Step 1: go"]'])
    events: list[dict[str, Any]] = []

    async def _cb(e: dict[str, Any]) -> None:
        events.append(e)

    graph = _graph(planner)
    graph._event_callback = _cb  # type: ignore[assignment]
    st = AgentState(goal="goal", tenant_ctx=T)
    st.context["_retrieved_chunks"] = [{"content": "x", "score": 0.9, "chunk_id": "c"}]
    with patch(
        "app.context.context_pipeline.ContextPipeline.run", side_effect=RuntimeError("boom")
    ):
        await graph._node_plan({"agent_state": st, "tenant_ctx": T})
    assert st.context["context_pipeline_failed"] is True
    assert "context_pipeline" in st.context["memory_degraded"]
    assert any(
        e.get("type") == "memory_degraded" and e.get("source") == "context_pipeline"
        for e in events
    )


async def test_semantic_cache_is_looked_up_by_goal_embedding() -> None:
    class _Hit:
        response = "Cached answer: the billing runbook is in Confluence"
        similarity = 0.95

    class _Cache:
        def __init__(self) -> None:
            self.embeddings: list[list[float]] = []

        async def get_similar(self, embedding: list[float], tenant_id: str) -> Any:
            self.embeddings.append(embedding)
            return _Hit() if tenant_id == T.tenant_id else None

        def lookup_text(self, query: str, tenant_id: str) -> str | None:
            raise AssertionError("exact-text lookup must not be used with an embedder")

    class _Embedder:
        async def embed(self, request: Any) -> EmbedResponse:
            return EmbedResponse(embeddings=[[0.1, 0.2, 0.3]], model="fake")

    cache = _Cache()
    planner = FakeProvider(responses=['["Step 1: go"]'])
    graph = _graph(planner, semantic_cache=cache, embedder=_Embedder())
    st = AgentState(goal="where is the billing runbook", tenant_ctx=T)
    await graph._node_plan({"agent_state": st, "tenant_ctx": T})

    assert cache.embeddings == [[0.1, 0.2, 0.3]]
    assert "billing runbook is in Confluence" in _prompt(planner)
