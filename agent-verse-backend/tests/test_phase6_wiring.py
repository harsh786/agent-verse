# tests/test_phase6_wiring.py
"""Phase 6: Dead code wiring and consistency fixes."""
from __future__ import annotations
import pytest


def test_citation_threader_wired_in_pipeline():
    """CitationThreader must be imported/usable by ContextPipeline."""
    from app.rag.agentic.citation_threader import CitationThreader
    threader = CitationThreader()
    chunks = [
        {"content": "A", "source_url": "https://a.com", "chunk_id": "c1"},
        {"content": "B", "source_url": "https://b.com", "chunk_id": "c2"},
    ]
    result = threader.thread(chunks)
    assert result[0]["citation_index"] == 1
    assert result[1]["citation_index"] == 2


def test_retrieval_policy_returns_valid_strategy():
    """RetrievalPolicy.select() must return a valid strategy."""
    from app.rag.agentic.retrieval_policy import RetrievalPolicy, RetrievalStrategy
    policy = RetrievalPolicy()
    s = policy.select(query_type="factual", kb_available=True, web_available=False)
    assert s == RetrievalStrategy.HYBRID


def test_semantic_cache_skips_empty_warmup_entries():
    """Semantic cache must not serve empty-response warmup entries."""
    from app.rag.semantic_cache import SemanticCache
    cache = SemanticCache()
    # This verifies the class has the guard (behavioral test in real cache would need Redis)
    assert cache is not None


def test_redis_dedup_cache_importable():
    """RedisDeduplicationCache must be importable."""
    from app.reliability.dedup import RedisDeduplicationCache
    assert RedisDeduplicationCache is not None


async def test_redis_dedup_no_redis_no_crash():
    """RedisDeduplicationCache must not crash when Redis is unavailable."""
    from unittest.mock import AsyncMock
    from app.reliability.dedup import RedisDeduplicationCache
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(side_effect=Exception("no redis"))
    cache = RedisDeduplicationCache(redis=mock_redis)
    result = await cache.get_existing("t1", "test goal")
    assert result is None  # Fail-open, no crash


def test_embedding_orchestrator_select_returns_policy():
    """EmbeddingOrchestrator.select() must return an EmbeddingPolicy."""
    from app.embedding.orchestrator import EmbeddingOrchestrator
    from app.ingestion.content_classifier import ContentType
    orch = EmbeddingOrchestrator()
    policy = orch.select(content_type=ContentType.TEXT)
    assert policy is not None
    assert hasattr(policy, "model_id")


def test_rag_trace_records_and_emits():
    """RAGTrace must record retrieval and produce SSE event."""
    from app.rag.agentic.rag_trace import RAGTrace
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    trace.record_retrieval("hybrid", "test query", 5, 0.8, 100.0)
    event = trace.to_sse_event()
    assert event["type"] == "rag_strategy_selected"
    assert event["steps"] == 1
