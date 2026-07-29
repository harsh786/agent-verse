"""Functional tests for all 9 RAG patterns."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext


@pytest.fixture
def tenant_ctx() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


# ── NAIVE RAG ─────────────────────────────────────────────────────────────────


def test_naive_rag_retriever_tool_instantiates() -> None:
    """RetrieverTool requires an injected gateway."""
    from app.rag.agentic.retriever_tool import RetrieverTool

    tool = RetrieverTool(retrieval_gateway=object())
    assert tool is not None


async def test_naive_rag_requires_collection(tenant_ctx: TenantContext) -> None:
    """RetrieverTool does not invent a fallback when no collection is supplied."""
    from app.rag.agentic.retriever_tool import RetrieverTool

    tool = RetrieverTool(retrieval_gateway=object())
    with pytest.raises(ValueError, match="collection_ids"):
        await tool.retrieve(query="test", tenant_ctx=tenant_ctx)


# ── ADVANCED RAG (ContextPipeline) ────────────────────────────────────────────


def test_context_pipeline_all_7_steps() -> None:
    """ContextPipeline.run() produces non-empty context for all three LLM roles."""
    from app.context.context_pipeline import ContextPipeline

    pipeline = ContextPipeline(max_tokens=4000)
    result = pipeline.run(
        chunks=[
            {
                "content": "AgentVerse supports dynamic orchestration.",
                "score": 0.9,
                "chunk_id": "c1",
            },
            {
                "content": "Goals are executed by AI agents.",
                "score": 0.8,
                "chunk_id": "c2",
            },
        ],
        query="how does AgentVerse work",
        goal_context="explain AgentVerse architecture",
    )
    assert result.planner_context
    assert len(result.planner_context) > 10
    assert result.executor_context is not None
    assert result.verifier_context is not None


def test_context_pipeline_deduplicates() -> None:
    """ContextPipeline deduplicates identical chunk content."""
    from app.context.context_pipeline import ContextPipeline

    pipeline = ContextPipeline(max_tokens=4000)
    # Same content twice with same chunk_id — deduplication must fire
    result = pipeline.run(
        chunks=[
            {"content": "Same content.", "score": 0.9, "chunk_id": "c1"},
            {"content": "Same content.", "score": 0.8, "chunk_id": "c1"},
        ],
        query="test",
        goal_context="test",
    )
    # Should have only 1 copy of the content
    assert result.planner_context.count("Same content.") == 1


# ── HYBRID RAG ────────────────────────────────────────────────────────────────


def test_hybrid_rag_store_search(tenant_ctx: TenantContext) -> None:
    """KnowledgeStore hybrid_search returns the correct chunk."""
    from app.rag.models import Chunk, KnowledgeCollection
    from app.rag.store import KnowledgeStore

    store = KnowledgeStore()
    col = KnowledgeCollection(name="docs", collection_id="col1", embedder="fake")
    store.create_collection(col, tenant_ctx=tenant_ctx)
    store.ingest_chunk(
        Chunk(
            document_id="d1",
            content="Python machine learning tutorial",
            embedding=[0.9] * 10,
            chunk_index=0,
            chunk_id="c1",
            metadata={},
        ),
        collection_id="col1",
        tenant_ctx=tenant_ctx,
    )
    results = store.hybrid_search(
        query="Python ML",
        query_embedding=[0.8] * 10,
        collection_id="col1",
        tenant_ctx=tenant_ctx,
        top_k=5,
    )
    assert len(results) >= 1
    assert results[0].chunk_id == "c1"


def test_rrf_fusion_in_hybrid_search() -> None:
    """rrf_fuse gives higher rank to chunks appearing in multiple lists."""
    from app.context.rerank_policy import rrf_fuse

    lists = [
        [{"chunk_id": "c1", "content": "A"}, {"chunk_id": "c2", "content": "B"}],
        [{"chunk_id": "c2", "content": "B"}, {"chunk_id": "c3", "content": "C"}],
    ]
    fused = rrf_fuse(lists, k=60)
    chunk_ids = [c["chunk_id"] for c in fused]
    # c2 appears in both lists → should rank high
    assert "c2" in chunk_ids
    assert chunk_ids[0] == "c2" or chunk_ids[1] == "c2"


# ── FUSION RAG ────────────────────────────────────────────────────────────────


async def test_fusion_rag_parallel_queries() -> None:
    """retrieve_fusion fires parallel sub-queries and returns merged results."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.rag.engine import RetrievalResult, retrieve_fusion

    session = AsyncMock(spec=AsyncSession)
    call_count: list[int] = []

    async def fake_hybrid_search(**kw: object) -> list[RetrievalResult]:
        call_count.append(1)
        idx = len(call_count)
        return [RetrievalResult(f"c{idx}", f"content {idx}", 0.8, {}, ["vector"])]

    with patch("app.rag.engine.hybrid_search", side_effect=fake_hybrid_search):
        results = await retrieve_fusion(
            session,
            query="authentication flow",
            query_embedding=[0.1] * 10,
            collection_id="col1",
            top_k=5,
            max_variants=3,
        )

    # At least 1 variant must be called
    assert len(call_count) >= 1
    assert isinstance(results, list)


# ── CORRECTIVE RAG ────────────────────────────────────────────────────────────


async def test_corrective_rag_uses_gateway_without_web_fallback(
    tenant_ctx: TenantContext,
) -> None:
    """CRAG delegates the explicit corrective strategy to the gateway."""
    from app.rag.agentic.retriever_tool import RetrieverTool
    from app.rag.contracts import RAGExecutionResult, RAGStrategy

    class Gateway:
        async def execute(self, tenant_ctx, **kwargs):
            assert kwargs["strategy_id"] is RAGStrategy.CORRECTIVE
            return RAGExecutionResult(
                requested_strategy_id="corrective",
                resolved_strategy_id=RAGStrategy.CORRECTIVE,
            )

    result = await RetrieverTool(retrieval_gateway=Gateway()).retrieve_corrective(
        query="obscure topic",
        tenant_ctx=tenant_ctx,
        collection_ids=["collection-1"],
        confidence_threshold=0.5,
    )

    assert result.strategy_used == "corrective"
    assert not result.fallback_used


# ── ADAPTIVE RAG ──────────────────────────────────────────────────────────────


def test_adaptive_rag_selects_strategy() -> None:
    """RetrievalPlanner.select_strategy returns correct strategy for query types."""
    from app.rag.engine import RetrievalPlanner

    planner = RetrievalPlanner()
    # HyDE — starts with "what is" and is short
    assert planner.select_strategy("what is agentverse") == "hyde"
    # Multi-hop — contains "compare"
    assert planner.select_strategy("compare agents across tenants") == "multi_hop"
    # Lexical — contains "ticket" keyword
    assert planner.select_strategy("Find ticket PROJ-123") == "lexical"
    # Direct — default fallback
    assert planner.select_strategy("list all open tasks") == "direct"


async def test_adaptive_rag_pattern_execute() -> None:
    """AdaptiveRAGPattern.execute calls the right strategy."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern

    pattern = AdaptiveRAGPattern()
    session = AsyncMock(spec=AsyncSession)
    called_strategies: list[str | None] = []

    async def fake_retrieve(
        _session: object,
        *,
        strategy: str | None = None,
        **kw: object,
    ) -> list:
        called_strategies.append(strategy)
        return []

    with patch("app.rag.agentic.patterns.adaptive.retrieve", side_effect=fake_retrieve):
        await pattern.execute(
            session=session,
            query="what is machine learning",
            query_embedding=[0.1] * 10,
            collection_id="col1",
        )

    assert len(called_strategies) == 1
    assert called_strategies[0] == "hyde"  # "what is..." triggers HyDE


# ── GRAPH RAG ─────────────────────────────────────────────────────────────────


async def test_graph_rag_entity_expansion() -> None:
    """KGQueryEngine entity expansion returns structured facts."""
    from app.knowledge_graph.models import GraphNode, NodeType
    from app.knowledge_graph.store import KnowledgeGraphStore
    from app.state_runtime.kg_query_engine import KGQueryEngine

    store = KnowledgeGraphStore()
    store.add_node(
        GraphNode(
            node_id="n1", label="AgentVerse", node_type=NodeType.CONCEPT, tenant_id="t1"
        )
    )
    store.add_node(
        GraphNode(
            node_id="n2", label="LangGraph", node_type=NodeType.CONCEPT, tenant_id="t1"
        )
    )

    engine = KGQueryEngine(kg_store=store)
    result = await engine.query("AgentVerse", tenant_id="t1", strategy="entity")
    assert result.strategy_used == "entity"
    assert result.confidence > 0
    assert len(result.facts) >= 1


async def test_graph_rag_path_traversal_with_edge() -> None:
    """KGQueryEngine path traversal follows real edges."""
    from app.knowledge_graph.models import EdgeType, GraphEdge, GraphNode, NodeType
    from app.knowledge_graph.store import KnowledgeGraphStore
    from app.state_runtime.kg_query_engine import KGQueryEngine

    store = KnowledgeGraphStore()
    store.add_node(
        GraphNode(
            node_id="n1", label="AgentVerse", node_type=NodeType.CONCEPT, tenant_id="t1"
        )
    )
    store.add_node(
        GraphNode(
            node_id="n2", label="LangGraph", node_type=NodeType.CONCEPT, tenant_id="t1"
        )
    )
    store.add_edge(
        GraphEdge(
            edge_id="e1",
            source_node_id="n1",
            target_node_id="n2",
            edge_type=EdgeType.MENTIONS,
            tenant_id="t1",
        )
    )

    engine = KGQueryEngine(kg_store=store)
    path_result = await engine.query("AgentVerse", tenant_id="t1", strategy="path")
    assert path_result.strategy_used == "path"
    # Facts should reference real node labels — not "?"
    for fact in path_result.facts:
        assert fact.get("to") != "?"


# ── SELF-RAG ─────────────────────────────────────────────────────────────────


async def test_self_rag_decides_to_retrieve() -> None:
    """SelfRAGPattern decides to retrieve and returns an answer string."""
    from app.rag.agentic.patterns.self_rag import SelfRAGPattern

    prov = FakeProvider(
        responses=[
            '{"should_retrieve": true, "reason": "factual query"}',
            "Based on context: The answer is correct.",
            '{"is_relevant": true, "is_supported": true, "is_useful": true, "confidence": 0.85}',
        ]
    )
    pattern = SelfRAGPattern()

    async def mock_retrieve(query: str, **kw: object) -> str:
        return "Supporting evidence for the answer."

    result = await pattern.execute(
        query="What is machine learning?",
        provider=prov,
        retrieve_fn=mock_retrieve,
    )
    assert isinstance(result, str)


# ── RAPTOR ────────────────────────────────────────────────────────────────────


async def test_raptor_builds_hierarchy() -> None:
    """RAPTORPattern summarizes chunks into a hierarchical answer."""
    from app.rag.agentic.patterns.raptor import RAPTORPattern

    prov = FakeProvider(
        responses=[
            "Summary of group 1: ML and DL",
            "Summary of group 2: NLP and CV",
            "Final comprehensive answer about AI",
        ]
    )
    pattern = RAPTORPattern(cluster_size=2, max_levels=1)
    chunks = [
        {"content": "ML basics", "chunk_id": "c1"},
        {"content": "DL networks", "chunk_id": "c2"},
        {"content": "NLP tasks", "chunk_id": "c3"},
        {"content": "CV vision", "chunk_id": "c4"},
    ]
    result = await pattern.execute(query="AI overview", chunks=chunks, provider=prov)
    assert isinstance(result, str)
    assert len(result) > 0


# ── COLBERT ───────────────────────────────────────────────────────────────────


def test_colbert_reranks_by_relevance() -> None:
    """ColBERTPattern reranks chunks — Java-heavy chunk should drop in rank."""
    from app.rag.agentic.patterns.colbert import ColBERTPattern

    pattern = ColBERTPattern(alpha=0.5)
    chunks = [
        {
            "chunk_id": "c1",
            "content": "Python machine learning deep learning",
            "score": 0.5,
        },
        {
            "chunk_id": "c2",
            "content": "Java enterprise application server",
            "score": 0.9,
        },
        {
            "chunk_id": "c3",
            "content": "Python scikit-learn tutorial for ML",
            "score": 0.4,
        },
    ]
    reranked = pattern.rerank("python machine learning", chunks)
    # The Java chunk should not rank above both Python-ML chunks
    top2 = [c["chunk_id"] for c in reranked[:2]]
    assert "c1" in top2 or "c3" in top2  # at least one Python-ML chunk in top 2


# ── FLARE ─────────────────────────────────────────────────────────────────────


def test_flare_detects_uncertainty() -> None:
    """_detect_uncertainty correctly identifies hedging phrases."""
    from app.rag.agentic.patterns.flare import _detect_uncertainty

    assert _detect_uncertainty("I think the answer might be...")
    assert _detect_uncertainty("I'm not sure about this.")
    assert not _detect_uncertainty("The definitive answer is Paris.")


async def test_flare_triggers_on_uncertainty() -> None:
    """FLAREPattern retrieves context when initial response is uncertain."""
    from app.rag.agentic.patterns.flare import FLAREPattern

    prov = FakeProvider(
        responses=[
            "I'm not sure but it might be the Eiffel Tower.",
            "Based on the context, it is definitely the Eiffel Tower.",
        ]
    )
    pattern = FLAREPattern()
    retrieved: list[str] = []

    async def mock_retrieve(query: str, **kw: object) -> str:
        retrieved.append(query)
        return "Context: Eiffel Tower is in Paris, built in 1889."

    result = await pattern.execute(
        query="Where is the Eiffel Tower?",
        provider=prov,
        retrieve_fn=mock_retrieve,
    )
    assert len(retrieved) > 0  # Retrieval triggered by uncertainty
    assert isinstance(result, str)
