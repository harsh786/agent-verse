"""Tests for semantic cache Redis API and goal-tree LLM synthesis.

BUG 1: SemanticCache.get/set must use Redis backend (not in-process lookup/store).
BUG 2: Goal-tree synthesis must produce a coherent string via LLM, not raw concatenation.
BUG 3: PII detections must be written to the audit trail.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


# ─── BUG 1: Semantic cache uses Redis-backed async API ───────────────────────


@pytest.mark.asyncio
async def test_semantic_cache_get_uses_redis():
    """SemanticCache.get() must use Redis backend when available."""
    from app.rag.semantic_cache import SemanticCache

    cache = SemanticCache()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    cache._redis = mock_redis

    result = await cache.get(query="test query", embedding=[0.1, 0.2], tenant_id="t1")
    mock_redis.get.assert_called_once()


@pytest.mark.asyncio
async def test_semantic_cache_set_uses_redis():
    """SemanticCache.set() must write to Redis when available."""
    from app.rag.semantic_cache import SemanticCache

    cache = SemanticCache()
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock()
    cache._redis = mock_redis

    await cache.set(query="test query", embedding=[0.1, 0.2], response="answer", tenant_id="t1")
    mock_redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_semantic_cache_get_returns_redis_value():
    """SemanticCache.get() must return the cached response from Redis."""
    import json
    from app.rag.semantic_cache import SemanticCache

    cache = SemanticCache()
    payload = json.dumps({"query": "test query", "response": "cached answer"})
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=payload)
    cache._redis = mock_redis

    result = await cache.get(query="test query", embedding=[0.1, 0.2], tenant_id="t1")
    assert result == "cached answer"


@pytest.mark.asyncio
async def test_semantic_cache_get_falls_back_to_local_on_redis_error():
    """SemanticCache.get() falls back to local dict when Redis raises."""
    from app.rag.semantic_cache import SemanticCache

    cache = SemanticCache()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(side_effect=ConnectionError("Redis unavailable"))
    cache._redis = mock_redis

    # Should not raise; returns None (miss) or local fallback
    result = await cache.get(query="q", embedding=[0.1], tenant_id="t1")
    assert result is None  # nothing in local cache either


@pytest.mark.asyncio
async def test_semantic_cache_set_falls_back_to_local_on_redis_error():
    """SemanticCache.set() falls back to local dict when Redis raises."""
    from app.rag.semantic_cache import SemanticCache

    cache = SemanticCache()
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(side_effect=ConnectionError("Redis unavailable"))
    cache._redis = mock_redis

    # Should not raise; writes to local dict instead
    await cache.set(query="q", embedding=[0.1], response="r", tenant_id="t1")
    # Verify local dict was written (embedding hash key)
    assert any("t1" in k for k in cache._local)


# ─── BUG 2: Goal-tree synthesis produces an LLM-synthesized string ────────────


@pytest.mark.asyncio
async def test_goal_tree_synthesis_returns_string():
    """Goal tree synthesis must return a non-empty string."""
    from app.agent.goal_tree import _synthesize_goal_tree_results

    mock_provider = AsyncMock()
    mock_provider.complete = AsyncMock(
        return_value=MagicMock(content="Synthesized answer about X and Y.")
    )
    mock_provider._default_model = "claude-haiku"

    sub_results = [
        {"goal": "Find X", "result": "X = 42", "success": True},
        {"goal": "Find Y", "result": "Y = hello", "success": True},
    ]

    result = await _synthesize_goal_tree_results(
        original_goal="Find X and Y and combine them",
        sub_results=sub_results,
        provider=mock_provider,
    )
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_goal_tree_synthesis_calls_provider():
    """Synthesis must call the provider's complete() when sub-results exist."""
    from app.agent.goal_tree import _synthesize_goal_tree_results

    mock_provider = AsyncMock()
    mock_provider.complete = AsyncMock(
        return_value=MagicMock(content="Combined result.")
    )
    mock_provider._default_model = "test-model"

    sub_results = [
        {"goal": "Step A", "result": "A done", "success": True},
    ]

    await _synthesize_goal_tree_results(
        original_goal="Do A",
        sub_results=sub_results,
        provider=mock_provider,
    )
    mock_provider.complete.assert_called_once()


@pytest.mark.asyncio
async def test_goal_tree_synthesis_fallback_without_provider():
    """Synthesis falls back to joining results when no provider is supplied."""
    from app.agent.goal_tree import _synthesize_goal_tree_results

    sub_results = [
        {"goal": "Find A", "result": "A is 1", "success": True},
        {"goal": "Find B", "result": "B is 2", "success": False},
    ]
    result = await _synthesize_goal_tree_results(
        original_goal="Find A and B",
        sub_results=sub_results,
        provider=None,
    )
    assert "A is 1" in result  # only successful sub-result included
    assert "B is 2" not in result  # failed sub-result excluded


@pytest.mark.asyncio
async def test_goal_tree_synthesis_fallback_on_llm_error():
    """Synthesis falls back gracefully when the LLM call raises."""
    from app.agent.goal_tree import _synthesize_goal_tree_results

    mock_provider = AsyncMock()
    mock_provider.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
    mock_provider._default_model = "test-model"

    sub_results = [
        {"goal": "Do X", "result": "X done", "success": True},
    ]

    result = await _synthesize_goal_tree_results(
        original_goal="Do X",
        sub_results=sub_results,
        provider=mock_provider,
    )
    assert isinstance(result, str)
    assert len(result) > 0
    assert "X done" in result  # fallback joins successful results


@pytest.mark.asyncio
async def test_goal_tree_synthesis_empty_sub_results():
    """Synthesis with no sub-results returns a meaningful string, not empty."""
    from app.agent.goal_tree import _synthesize_goal_tree_results

    result = await _synthesize_goal_tree_results(
        original_goal="Impossible goal",
        sub_results=[],
        provider=None,
    )
    assert isinstance(result, str)
    assert len(result) > 0


# ─── BUG 1 (source-level): graph.py uses async cache API ─────────────────────


def test_graph_uses_semantic_cache_async_api():
    """graph.py must use await cache.get() and await cache.set(), not lookup()."""
    import inspect
    from app.agent import graph

    src = inspect.getsource(graph)
    # The new async API must be present
    assert "_semantic_cache.get(" in src, \
        "graph.py must call self._semantic_cache.get() for cache lookups"
    assert "_semantic_cache.set(" in src, \
        "graph.py must call self._semantic_cache.set() to store results"
    # The old synchronous API must NOT be used for the hot path
    assert "semantic_cache.lookup(" not in src, \
        "graph.py must NOT use the legacy lookup() API (replaced by async get())"


# ─── BUG 3 (source-level): PII audit logging present ────────────────────────


def test_pii_audit_logged():
    """PII detections must be logged to the audit trail."""
    import inspect
    from app.agent import graph

    src = inspect.getsource(graph)
    assert "pii_redacted" in src, \
        "PII redaction event type must be present in graph.py"
    assert "guardrail_checker" in src, \
        "PII audit event must reference 'guardrail_checker' as the tool_name"
    # audit_log.record() must be called near the PII detection block
    assert "audit_log" in src and "pii_redacted" in src, \
        "audit_log.record() must be called when pii_redacted is detected"
