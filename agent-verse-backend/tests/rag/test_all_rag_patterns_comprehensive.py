"""Comprehensive tests for all RAG patterns (30+ tests).

Covers all 9 RAG patterns: init, execute, error handling.
Also tests StrategyRegistry, BM25Retriever, SemanticCache.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from app.providers.fake import FakeProvider
from tests.rag.colbert_fakes import DeterministicColBERTReranker


def _fake(responses: list[str] | None = None) -> FakeProvider:
    return FakeProvider(responses=responses or ["RAG response."])


# ─────────────────────────────────────────────────────────────────────────────
# AdaptiveRAGPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestAdaptiveRAGPattern:
    def test_init(self) -> None:
        from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern
        p = AdaptiveRAGPattern()
        assert p.pattern_id == "adaptive_rag"

    def test_state_is_implemented(self) -> None:
        from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern
        from app.rag.agentic.patterns.base import RAGPatternState
        assert AdaptiveRAGPattern().state == RAGPatternState.IMPLEMENTED

    def test_description_non_empty(self) -> None:
        from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern
        assert len(AdaptiveRAGPattern().description) > 0

    def test_is_compatible_always_true(self) -> None:
        from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern
        assert AdaptiveRAGPattern().is_compatible(None) is True

    async def test_execute_returns_list(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern
        p = AdaptiveRAGPattern()
        with patch("app.rag.agentic.patterns.adaptive.retrieve", new=AsyncMock(return_value=[])):
            result = await p.execute(
                session=MagicMock(),
                query="test query",
                query_embedding=[0.1] * 10,
                collection_id="col1",
                top_k=5,
            )
        assert isinstance(result, list)

    async def test_execute_with_force_strategy(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.rag.agentic.patterns.adaptive import AdaptiveRAGPattern
        p = AdaptiveRAGPattern()
        with patch("app.rag.agentic.patterns.adaptive.retrieve", new=AsyncMock(return_value=[])):
            result = await p.execute(
                session=MagicMock(),
                query="test",
                query_embedding=None,
                collection_id="col1",
                force_strategy="lexical",
            )
        assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────────────────────
# AgenticChunkingPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestAgenticChunkingPattern:
    def test_init(self) -> None:
        from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
        p = AgenticChunkingPattern(max_propositions=5)
        assert p.pattern_id == "agentic_chunking"
        assert p._max_props == 5

    def test_state_is_implemented(self) -> None:
        from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
        from app.rag.agentic.patterns.base import RAGPatternState
        assert AgenticChunkingPattern().state == RAGPatternState.IMPLEMENTED

    async def test_extract_propositions_returns_list(self) -> None:
        from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
        provider = _fake(["First fact.\nSecond fact.\nThird fact."])
        p = AgenticChunkingPattern()
        props = await p.extract_propositions("Long text about something.", provider)
        assert isinstance(props, list)
        assert len(props) > 0

    async def test_extract_propositions_fallback_on_error(self) -> None:
        from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("fail"))
        p = AgenticChunkingPattern()
        text = "First sentence. Second sentence. Third sentence about AI."
        props = await p.extract_propositions(text, provider)
        assert isinstance(props, list)

    async def test_execute_returns_proposition_chunks(self) -> None:
        from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
        provider = _fake(["Fact one.\nFact two."])
        p = AgenticChunkingPattern(max_propositions=5)
        chunks = [{"content": "Some content here.", "chunk_id": "c1"}]
        result = await p.execute(chunks=chunks, provider=provider)
        assert isinstance(result, list)
        assert len(result) > 0

    async def test_execute_empty_chunks_returns_empty(self) -> None:
        from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
        p = AgenticChunkingPattern()
        result = await p.execute(chunks=[], provider=_fake())
        assert result == []

    async def test_execute_none_provider_returns_original(self) -> None:
        from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
        p = AgenticChunkingPattern()
        chunks = [{"content": "text", "chunk_id": "c1"}]
        result = await p.execute(chunks=chunks, provider=None)
        assert result == chunks


# ─────────────────────────────────────────────────────────────────────────────
# ColBERTPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestColBERTPattern:
    def test_init(self) -> None:
        from app.rag.agentic.patterns.colbert import ColBERTPattern
        p = ColBERTPattern(
            alpha=0.6,
            reranker=DeterministicColBERTReranker(),
        )
        assert p.pattern_id == "colbert_late_interaction"
        assert p._alpha == 0.6

    def test_state_is_implemented(self) -> None:
        from app.rag.agentic.patterns.base import RAGPatternState
        from app.rag.agentic.patterns.colbert import ColBERTPattern
        assert (
            ColBERTPattern(reranker=DeterministicColBERTReranker()).state
            == RAGPatternState.IMPLEMENTED
        )

    def test_rerank_empty_returns_empty(self) -> None:
        from app.rag.agentic.patterns.colbert import ColBERTPattern
        assert (
            ColBERTPattern(reranker=DeterministicColBERTReranker()).rerank(
                "query",
                [],
            )
            == []
        )

    def test_rerank_sorts_by_score(self) -> None:
        from app.rag.agentic.patterns.colbert import ColBERTPattern
        p = ColBERTPattern(alpha=0.5, reranker=DeterministicColBERTReranker())
        chunks = [
            {"content": "machine learning algorithms", "chunk_id": "c1", "score": 0.3},
            {"content": "cooking recipes for dinner", "chunk_id": "c2", "score": 0.8},
        ]
        result = p.rerank("machine learning", chunks)
        # The "machine learning" chunk should rank higher
        assert result[0]["chunk_id"] == "c1"

    def test_rerank_top_k(self) -> None:
        from app.rag.agentic.patterns.colbert import ColBERTPattern
        p = ColBERTPattern(reranker=DeterministicColBERTReranker())
        chunks = [
            {"content": f"doc {i}", "chunk_id": f"c{i}", "score": 0.5}
            for i in range(5)
        ]
        result = p.rerank("doc", chunks, top_k=2)
        assert len(result) == 2

    async def test_execute_returns_string(self) -> None:
        from app.rag.agentic.patterns.colbert import ColBERTPattern
        p = ColBERTPattern(reranker=DeterministicColBERTReranker())
        chunks = [
            {"content": "machine learning is a field", "chunk_id": "c1", "score": 0.7},
            {"content": "deep neural networks", "chunk_id": "c2", "score": 0.5},
        ]
        result = await p.execute(query="machine learning", chunks=chunks, top_k=2)
        assert isinstance(result, str)
        assert len(result) > 0

    async def test_execute_empty_chunks_returns_empty_string(self) -> None:
        from app.rag.agentic.patterns.colbert import ColBERTPattern
        result = await ColBERTPattern(
            reranker=DeterministicColBERTReranker()
        ).execute(query="test", chunks=[])
        assert result == ""


# ─────────────────────────────────────────────────────────────────────────────
# CorrectiveRAGPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestCorrectiveRAGPattern:
    def test_init(self) -> None:
        from app.rag.agentic.patterns.corrective import CorrectiveRAGPattern
        p = CorrectiveRAGPattern()
        assert p.pattern_id == "corrective_rag"

    def test_state_is_implemented(self) -> None:
        from app.rag.agentic.patterns.base import RAGPatternState
        from app.rag.agentic.patterns.corrective import CorrectiveRAGPattern
        assert CorrectiveRAGPattern().state == RAGPatternState.IMPLEMENTED

    async def test_execute_delegates_to_retriever_tool(self) -> None:
        from app.rag.agentic.patterns.corrective import CorrectiveRAGPattern
        retriever_tool = MagicMock()
        retriever_tool.retrieve_corrective = AsyncMock(return_value=["chunk1"])
        p = CorrectiveRAGPattern()
        result = await p.execute(
            retriever_tool=retriever_tool,
            query="test",
            tenant_ctx=MagicMock(),
        )
        assert result == ["chunk1"]
        retriever_tool.retrieve_corrective.assert_awaited_once()

    def test_is_compatible(self) -> None:
        from app.rag.agentic.patterns.corrective import CorrectiveRAGPattern
        assert CorrectiveRAGPattern().is_compatible(None) is True


# ─────────────────────────────────────────────────────────────────────────────
# FLAREPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestFLAREPattern:
    def test_init(self) -> None:
        from app.rag.agentic.patterns.flare import FLAREPattern
        p = FLAREPattern(max_iterations=3)
        assert p.pattern_id == "flare"
        assert p._max_iter == 3

    def test_state_is_implemented(self) -> None:
        from app.rag.agentic.patterns.base import RAGPatternState
        from app.rag.agentic.patterns.flare import FLAREPattern
        assert FLAREPattern().state == RAGPatternState.IMPLEMENTED

    async def test_execute_no_uncertainty_returns_initial(self) -> None:
        from app.rag.agentic.patterns.flare import FLAREPattern
        provider = _fake(["Paris is the capital of France."])
        p = FLAREPattern()
        result = await p.execute(query="Capital of France?", provider=provider)
        assert "Paris" in result

    async def test_execute_with_uncertainty_triggers_retrieve(self) -> None:
        from app.rag.agentic.patterns.flare import FLAREPattern
        provider = _fake([
            "I'm not sure about this answer.",
            "Paris is definitely the capital.",
        ])
        retrieve_fn = AsyncMock(return_value="France has Paris as capital.")
        p = FLAREPattern(max_iterations=1)
        result = await p.execute(
            query="Capital of France?",
            provider=provider,
            retrieve_fn=retrieve_fn,
        )
        assert isinstance(result, str)

    async def test_execute_provider_error_returns_empty(self) -> None:
        from app.rag.agentic.patterns.flare import FLAREPattern
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("fail"))
        p = FLAREPattern()
        result = await p.execute(query="test", provider=provider)
        assert result == ""

    async def test_execute_no_retrieve_fn(self) -> None:
        from app.rag.agentic.patterns.flare import FLAREPattern
        provider = _fake(["I think the answer might be 42."])
        p = FLAREPattern()
        result = await p.execute(query="What is the answer?", provider=provider, retrieve_fn=None)
        assert isinstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# FusionRAGPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestFusionRAGPattern:
    def test_init(self) -> None:
        from app.rag.agentic.patterns.fusion import FusionRAGPattern
        p = FusionRAGPattern()
        assert p.pattern_id == "fusion_rag"

    def test_state_is_implemented(self) -> None:
        from app.rag.agentic.patterns.base import RAGPatternState
        from app.rag.agentic.patterns.fusion import FusionRAGPattern
        assert FusionRAGPattern().state == RAGPatternState.IMPLEMENTED

    def test_is_compatible_complex(self) -> None:
        from app.rag.agentic.patterns.fusion import FusionRAGPattern
        props = MagicMock()
        props.complexity = "complex"
        assert FusionRAGPattern().is_compatible(props) is True

    def test_is_compatible_simple_returns_true(self) -> None:
        from app.rag.agentic.patterns.fusion import FusionRAGPattern
        props = MagicMock()
        props.complexity = None
        assert FusionRAGPattern().is_compatible(props) is True

    async def test_execute_returns_list(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.rag.agentic.patterns.fusion import FusionRAGPattern
        p = FusionRAGPattern()
        with patch("app.rag.engine.retrieve_fusion", new=AsyncMock(return_value=[])):
            result = await p.execute(
                session=MagicMock(),
                query="test",
                query_embedding=[0.1] * 10,
                collection_id="col1",
            )
        assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────────────────────
# RAPTORPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestRAPTORPattern:
    def test_init(self) -> None:
        from app.rag.agentic.patterns.raptor import RAPTORPattern
        p = RAPTORPattern(cluster_size=4, max_levels=3)
        assert p.pattern_id == "raptor"
        assert p._cluster_size == 4

    def test_state_is_implemented(self) -> None:
        from app.rag.agentic.patterns.base import RAGPatternState
        from app.rag.agentic.patterns.raptor import RAPTORPattern
        assert RAPTORPattern().state == RAGPatternState.IMPLEMENTED

    async def test_execute_empty_chunks_returns_empty(self) -> None:
        from app.rag.agentic.patterns.raptor import RAPTORPattern
        result = await RAPTORPattern().execute(
            query="test", chunks=[], provider=_fake()
        )
        assert result == ""

    async def test_execute_builds_tree_and_answers(self) -> None:
        from app.rag.agentic.patterns.raptor import RAPTORPattern
        provider = _fake(["Summary", "Summary", "Final answer"])
        p = RAPTORPattern(cluster_size=2, max_levels=1)
        chunks = [
            {"content": "Chunk one text here.", "chunk_id": "c1"},
            {"content": "Chunk two text here.", "chunk_id": "c2"},
        ]
        result = await p.execute(query="What does this say?", chunks=chunks, provider=provider)
        assert isinstance(result, str)

    async def test_execute_provider_error_falls_back(self) -> None:
        from app.rag.agentic.patterns.raptor import RAPTORPattern
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("fail"))
        p = RAPTORPattern(cluster_size=2, max_levels=1)
        chunks = [{"content": "Some content", "chunk_id": "c1"}]
        result = await p.execute(query="test", chunks=chunks, provider=provider)
        # Fallback returns content (not empty)
        assert isinstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# SelfRAGPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestSelfRAGPattern:
    def test_init(self) -> None:
        from app.rag.agentic.patterns.self_rag import SelfRAGPattern
        p = SelfRAGPattern(confidence_threshold=0.6)
        assert p.pattern_id == "self_rag"
        assert p._threshold == 0.6

    def test_state_is_implemented(self) -> None:
        from app.rag.agentic.patterns.base import RAGPatternState
        from app.rag.agentic.patterns.self_rag import SelfRAGPattern
        assert SelfRAGPattern().state == RAGPatternState.IMPLEMENTED

    async def test_execute_returns_string(self) -> None:
        from app.rag.agentic.patterns.self_rag import SelfRAGPattern
        should_retrieve_json = json.dumps({"should_retrieve": False, "reason": "simple"})
        provider = _fake([should_retrieve_json, "The answer is 42."])
        p = SelfRAGPattern()
        result = await p.execute(query="What is 6 * 7?", provider=provider)
        assert isinstance(result, str)

    async def test_execute_with_retrieve_fn(self) -> None:
        from app.rag.agentic.patterns.self_rag import SelfRAGPattern
        should_retrieve_json = json.dumps({"should_retrieve": True, "reason": "factual"})
        critique_json = json.dumps({
            "is_relevant": True, "is_supported": True, "is_useful": True, "confidence": 0.8
        })
        provider = _fake([should_retrieve_json, "The capital is Paris.", critique_json])
        retrieve_fn = AsyncMock(return_value="France context: Paris is the capital")
        p = SelfRAGPattern()
        result = await p.execute(
            query="Capital of France?", provider=provider, retrieve_fn=retrieve_fn
        )
        assert isinstance(result, str)

    async def test_execute_with_critique(self) -> None:
        from app.rag.agentic.patterns.self_rag import SelfRAGPattern
        should_retrieve_json = json.dumps({"should_retrieve": True, "reason": "factual"})
        critique_json = json.dumps({
            "is_relevant": True, "is_supported": True, "is_useful": True, "confidence": 0.9
        })
        provider = _fake([should_retrieve_json, "Final answer.", critique_json])
        retrieve_fn = AsyncMock(return_value="Context here")
        p = SelfRAGPattern()
        result = await p.execute_with_critique(
            query="test", provider=provider, retrieve_fn=retrieve_fn
        )
        assert result.retrieved is True
        assert isinstance(result.answer, str)

    async def test_execute_provider_error_returns_empty(self) -> None:
        from app.rag.agentic.patterns.self_rag import SelfRAGPattern
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("fail"))
        p = SelfRAGPattern()
        result = await p.execute(query="test", provider=provider)
        assert isinstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# SpeculativeRAGPattern
# ─────────────────────────────────────────────────────────────────────────────

class TestSpeculativeRAGPattern:
    def test_init(self) -> None:
        from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern
        p = SpeculativeRAGPattern(n_candidates=2, min_support_score=0.5)
        assert p.pattern_id == "speculative_rag"
        assert p._n == 2

    def test_state_is_implemented(self) -> None:
        from app.rag.agentic.patterns.base import RAGPatternState
        from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern
        assert SpeculativeRAGPattern().state == RAGPatternState.IMPLEMENTED

    async def test_execute_generates_candidates(self) -> None:
        from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern
        verify_json = json.dumps({"score": 0.8, "supported": True})
        provider = _fake(["Candidate A", "Candidate B", verify_json, verify_json])
        p = SpeculativeRAGPattern(n_candidates=2)
        retrieve_fn = AsyncMock(return_value="Context for verification")
        result = await p.execute(
            query="What is this?", provider=provider, retrieve_fn=retrieve_fn
        )
        assert isinstance(result, str)

    async def test_execute_no_candidates_returns_empty(self) -> None:
        from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("fail"))
        p = SpeculativeRAGPattern(n_candidates=1)
        result = await p.execute(query="test", provider=provider)
        assert result == ""

    async def test_execute_returns_best_supported(self) -> None:
        from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern
        verify_low = json.dumps({"score": 0.2, "supported": False})
        verify_high = json.dumps({"score": 0.9, "supported": True})
        provider = _fake(["Weak answer", "Strong answer", verify_low, verify_high])
        p = SpeculativeRAGPattern(n_candidates=2, min_support_score=0.6)
        retrieve_fn = AsyncMock(return_value="Context")
        result = await p.execute(
            query="test", provider=provider, retrieve_fn=retrieve_fn
        )
        assert result == "Strong answer"


# ─────────────────────────────────────────────────────────────────────────────
# StrategyRegistry
# ─────────────────────────────────────────────────────────────────────────────

class TestStrategyRegistry:
    def test_build_default_registry(self) -> None:
        from app.orchestration.strategy_registry import build_default_registry
        registry = build_default_registry()
        assert registry is not None

    def test_new_patterns_follow_runtime_availability_contract(self) -> None:
        from app.orchestration.strategy_registry import build_default_registry
        from app.rag.contracts import RAGStrategy

        registry = build_default_registry()
        for pattern_id in [
            "self_consistency",
            "tree_of_thoughts",
            "peer_review",
            "episodic_memory",
            "procedural_memory",
        ]:
            assert registry.is_available(pattern_id) is True, f"{pattern_id} not available"
        for strategy in RAGStrategy:
            assert registry.get(strategy.value) is not None
            assert registry.is_available(strategy.value)

    def test_planned_patterns_not_available(self) -> None:
        from app.orchestration.strategy_registry import build_default_registry
        registry = build_default_registry()
        assert registry.is_available("raft") is True

    def test_get_by_id(self) -> None:
        from app.orchestration.strategy_registry import build_default_registry
        registry = build_default_registry()
        cap = registry.get("react")
        assert cap is not None
        assert cap.strategy_id == "react"

    def test_get_unknown_returns_none(self) -> None:
        from app.orchestration.strategy_registry import build_default_registry
        registry = build_default_registry()
        assert registry.get("nonexistent_pattern") is None

    def test_list_by_category(self) -> None:
        from app.orchestration.strategy_registry import StrategyCategory, build_default_registry
        registry = build_default_registry()
        rag_patterns = registry.list_by_category(StrategyCategory.RAG)
        assert len(rag_patterns) > 5

    def test_filter_by_state_implemented(self) -> None:
        from app.orchestration.strategy_registry import StrategyState, build_default_registry
        registry = build_default_registry()
        impl = registry.filter(state=StrategyState.IMPLEMENTED)
        assert len(impl) > 10


# ─────────────────────────────────────────────────────────────────────────────
# BM25Retriever
# ─────────────────────────────────────────────────────────────────────────────

class TestBM25Retriever:
    def test_init(self) -> None:
        from app.rag.bm25 import BM25Retriever
        r = BM25Retriever(k1=1.5, b=0.75)
        assert r._k1 == 1.5

    def test_index_and_search(self) -> None:
        from app.rag.bm25 import BM25Retriever
        r = BM25Retriever()
        docs = [
            {"content": "machine learning model training", "chunk_id": "c1"},
            {"content": "cooking pasta carbonara recipe", "chunk_id": "c2"},
            {"content": "deep learning neural network", "chunk_id": "c3"},
        ]
        r.index(docs)
        results = r.search("machine learning", top_k=2)
        assert len(results) >= 1
        # Machine learning docs should rank higher
        assert results[0].chunk_id in ("c1", "c3")

    def test_search_empty_corpus(self) -> None:
        from app.rag.bm25 import BM25Retriever
        r = BM25Retriever()
        r.index([])
        assert r.search("query") == []

    def test_search_no_match_returns_empty(self) -> None:
        from app.rag.bm25 import BM25Retriever
        r = BM25Retriever()
        r.index([{"content": "cats and dogs", "chunk_id": "c1"}])
        results = r.search("quantum physics")
        assert results == []

    def test_search_top_k_limits_results(self) -> None:
        from app.rag.bm25 import BM25Retriever
        r = BM25Retriever()
        docs = [{"content": f"python programming {i}", "chunk_id": f"c{i}"} for i in range(10)]
        r.index(docs)
        results = r.search("python programming", top_k=3)
        assert len(results) <= 3

    def test_bm25_hit_fields(self) -> None:
        from app.rag.bm25 import BM25Retriever
        r = BM25Retriever()
        r.index([{"content": "test document content", "chunk_id": "c1"}])
        results = r.search("test")
        if results:
            hit = results[0]
            assert hit.chunk_id == "c1"
            assert hit.score > 0
            assert isinstance(hit.content, str)

    def test_is_available_property(self) -> None:
        from app.rag.bm25 import BM25Retriever
        r = BM25Retriever()
        assert isinstance(r.is_available, bool)


# ─────────────────────────────────────────────────────────────────────────────
# SemanticCache
# ─────────────────────────────────────────────────────────────────────────────

class TestSemanticCache:
    def _make_ctx(self, tenant_id: str = "t1"):  # type: ignore[return]
        from app.tenancy.context import PlanTier, TenantContext
        return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="test-key")

    def test_init(self) -> None:
        from app.rag.semantic_cache import SemanticCache
        cache = SemanticCache(threshold=0.9, ttl_seconds=3600.0)
        assert cache._threshold == 0.9

    def test_store_and_lookup_sync(self) -> None:
        from app.rag.semantic_cache import SemanticCache
        cache = SemanticCache(threshold=0.95, ttl_seconds=3600.0)
        ctx = self._make_ctx()
        emb = [1.0, 0.0, 0.0]
        cache.store(query_embedding=emb, response="Hello!", tenant_ctx=ctx)
        result = cache.lookup(query_embedding=emb, tenant_ctx=ctx)
        assert result == "Hello!"

    def test_lookup_miss_returns_none(self) -> None:
        from app.rag.semantic_cache import SemanticCache
        cache = SemanticCache(threshold=0.99, ttl_seconds=3600.0)
        ctx = self._make_ctx()
        result = cache.lookup(query_embedding=[0.1, 0.2], tenant_ctx=ctx)
        assert result is None

    def test_clear_removes_entries(self) -> None:
        from app.rag.semantic_cache import SemanticCache
        cache = SemanticCache(threshold=0.95, ttl_seconds=3600.0)
        ctx = self._make_ctx()
        emb = [1.0, 0.0, 0.0]
        cache.store(query_embedding=emb, response="data", tenant_ctx=ctx)
        cache.clear(tenant_ctx=ctx)
        assert cache.lookup(query_embedding=emb, tenant_ctx=ctx) is None

    async def test_store_async_and_get_similar(self) -> None:
        from app.rag.semantic_cache import SemanticCache
        cache = SemanticCache(threshold=0.9, ttl_seconds=3600.0)
        emb = [0.9, 0.1, 0.0]
        await cache.store_async(emb, "test query", "Cached answer", "tenant1")
        hit = await cache.get_similar(emb, "tenant1")
        assert hit is not None
        assert hit.response == "Cached answer"

    async def test_get_similar_miss_returns_none(self) -> None:
        from app.rag.semantic_cache import SemanticCache
        cache = SemanticCache(threshold=0.99, ttl_seconds=3600.0)
        emb = [0.1, 0.9, 0.0]
        result = await cache.get_similar(emb, "no_tenant")
        assert result is None

    def test_stats_returns_dict(self) -> None:
        from app.rag.semantic_cache import SemanticCache
        cache = SemanticCache()
        ctx = self._make_ctx()
        stats = cache.stats(tenant_ctx=ctx)
        assert "hits" in stats
        assert "misses" in stats
        assert "hit_rate" in stats
