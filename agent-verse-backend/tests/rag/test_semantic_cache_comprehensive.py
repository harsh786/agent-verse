"""Comprehensive tests for app/rag/semantic_cache.py — targeting 90%+ coverage."""
from __future__ import annotations

import math
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.rag.semantic_cache import SemanticCache, _cosine
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="sc-t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
_CTX2 = TenantContext(tenant_id="sc-t2", plan=PlanTier.FREE, api_key_id="k2")


def _unit_vec(dim: int = 8, idx: int = 0) -> list[float]:
    v = [0.0] * dim
    v[idx % dim] = 1.0
    return v


class TestCosineFunction:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert _cosine(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector_returns_zero(self):
        """Covers line 24: both mag == 0 returns 0.0."""
        zero = [0.0, 0.0, 0.0]
        assert _cosine(zero, [1.0, 0.0, 0.0]) == 0.0
        assert _cosine([1.0, 0.0, 0.0], zero) == 0.0

    def test_normalized_vectors(self):
        mag = math.sqrt(2)
        v = [1.0 / mag, 1.0 / mag]
        assert _cosine(v, v) == pytest.approx(1.0)


class TestSemanticCacheHashAndKey:
    def test_hash_embedding_is_deterministic(self):
        """Covers line 68: _hash_embedding produces stable hash."""
        cache = SemanticCache()
        emb = [0.1, 0.2, 0.3, 0.4]
        h1 = cache._hash_embedding(emb)
        h2 = cache._hash_embedding(emb)
        assert h1 == h2

    def test_hash_embedding_different_for_different_vectors(self):
        cache = SemanticCache()
        h1 = cache._hash_embedding([0.1, 0.2])
        h2 = cache._hash_embedding([0.3, 0.4])
        assert h1 != h2

    def test_hash_embedding_length(self):
        """Hash should be 32 chars (hexdigest[:32])."""
        cache = SemanticCache()
        h = cache._hash_embedding([1.0, 2.0, 3.0])
        assert len(h) == 32

    def test_cache_key_format(self):
        """Covers lines 72-75: _cache_key returns correct format."""
        cache = SemanticCache()
        key = cache._cache_key("tenant-abc", "hashxyz")
        assert key == "semantic_cache:tenant-abc:hashxyz"

    def test_cache_key_includes_prefix(self):
        cache = SemanticCache()
        key = cache._cache_key("t1", "h1")
        assert key.startswith(SemanticCache._CACHE_PREFIX)


class TestSemanticCacheGetSet:
    """Tests for async get/set API (lines 79-128)."""

    @pytest.mark.asyncio
    async def test_get_without_embedding_returns_none_when_empty(self):
        """Covers branch: embedding is None, local dict lookup."""
        cache = SemanticCache()
        result = await cache.get("my query", None, "tenant-x")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_and_get_without_embedding(self):
        """Covers lines 111, 88: no-embedding path uses query prefix as key."""
        cache = SemanticCache()
        await cache.set("list all issues", None, "42 open issues", "t1")
        result = await cache.get("list all issues", None, "t1")
        assert result == "42 open issues"

    @pytest.mark.asyncio
    async def test_get_with_embedding_no_redis(self):
        """Covers lines 90-102: embedding provided, no Redis → local dict."""
        cache = SemanticCache()
        emb = [1.0, 0.0, 0.0]
        result = await cache.get("query", emb, "t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_and_get_with_embedding_no_redis(self):
        """Covers lines 108-128: set stores in local dict when no Redis."""
        cache = SemanticCache()
        emb = [1.0, 0.0, 0.0]
        await cache.set("fetch repos", emb, "response: 12 repos", "t1")
        result = await cache.get("fetch repos", emb, "t1")
        assert result == "response: 12 repos"

    @pytest.mark.asyncio
    async def test_get_embedding_tenant_isolation(self):
        """Different tenant should not see another's cached result."""
        cache = SemanticCache()
        emb = [0.5, 0.5]
        await cache.set("query", emb, "secret response", "tenant-a")
        result = await cache.get("query", emb, "tenant-b")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_and_get_with_redis_success(self):
        """Covers lines 93-99, 117-124: Redis-backed get/set."""
        mock_redis = AsyncMock()
        import json
        cached_data = json.dumps({"query": "test query", "response": "cached answer"})
        mock_redis.get = AsyncMock(return_value=cached_data)
        mock_redis.set = AsyncMock(return_value=True)

        cache = SemanticCache(redis=mock_redis)
        emb = [0.1, 0.2, 0.3]
        # Test set
        await cache.set("test query", emb, "cached answer", "t1")
        mock_redis.set.assert_called_once()

        # Test get
        result = await cache.get("test query", emb, "t1")
        assert result == "cached answer"

    @pytest.mark.asyncio
    async def test_get_with_redis_miss_falls_to_local(self):
        """When Redis returns None, falls through to local dict."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        cache = SemanticCache(redis=mock_redis)
        emb = [0.1, 0.2]
        result = await cache.get("query", emb, "t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_with_redis_error_falls_to_local(self):
        """When Redis raises, falls through to local dict without raising."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=Exception("Redis connection error"))

        cache = SemanticCache(redis=mock_redis)
        emb = [0.1, 0.2]
        # Should not raise
        result = await cache.get("query", emb, "t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_with_redis_error_falls_to_local(self):
        """When Redis set raises, falls through to store in local dict."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=Exception("Redis write error"))

        cache = SemanticCache(redis=mock_redis)
        emb = [0.3, 0.4]
        await cache.set("query fallback", emb, "fallback response", "t1")
        # Should be in local dict
        emb_hash = cache._hash_embedding(emb)
        assert f"t1:{emb_hash}" in cache._local


class TestSemanticCacheCosineSimilarity:
    def test_store_and_lookup_hit(self):
        cache = SemanticCache(threshold=0.9)
        v = _unit_vec()
        cache.store(query_embedding=v, response="cached result", tenant_ctx=_CTX)
        result = cache.lookup(query_embedding=v, tenant_ctx=_CTX)
        assert result == "cached result"

    def test_lookup_miss_returns_none(self):
        cache = SemanticCache(threshold=0.9)
        result = cache.lookup(query_embedding=_unit_vec(), tenant_ctx=_CTX)
        assert result is None

    def test_lookup_below_threshold_returns_none(self):
        cache = SemanticCache(threshold=0.99)
        v_a = _unit_vec(8, 0)
        v_b = _unit_vec(8, 1)
        cache.store(query_embedding=v_a, response="answer a", tenant_ctx=_CTX)
        result = cache.lookup(query_embedding=v_b, tenant_ctx=_CTX)
        assert result is None

    def test_ttl_expiry(self):
        cache = SemanticCache(threshold=0.9, ttl_seconds=0.05)
        v = [1.0, 0.0, 0.0]
        cache.store(query_embedding=v, response="fresh", tenant_ctx=_CTX)
        assert cache.lookup(query_embedding=v, tenant_ctx=_CTX) == "fresh"
        time.sleep(0.1)
        assert cache.lookup(query_embedding=v, tenant_ctx=_CTX) is None

    def test_stats_track_hits_and_misses(self):
        cache = SemanticCache(threshold=0.99)
        v = [1.0, 0.0, 0.0]
        cache.store(query_embedding=v, response="val", tenant_ctx=_CTX)
        cache.lookup(query_embedding=v, tenant_ctx=_CTX)   # hit
        cache.lookup(query_embedding=[0.0, 1.0, 0.0], tenant_ctx=_CTX)  # miss
        stats = cache.stats(tenant_ctx=_CTX)
        assert stats["hits"] == 1
        assert stats["misses"] == 1

    def test_clear_removes_entries_and_stats(self):
        cache = SemanticCache(threshold=0.9)
        v = [1.0, 0.0]
        cache.store(query_embedding=v, response="x", tenant_ctx=_CTX)
        cache.lookup(query_embedding=v, tenant_ctx=_CTX)
        cache.clear(tenant_ctx=_CTX)
        assert cache.lookup(query_embedding=v, tenant_ctx=_CTX) is None
        stats = cache.stats(tenant_ctx=_CTX)
        assert stats["hits"] == 0

    def test_tenant_isolation(self):
        cache = SemanticCache(threshold=0.9)
        v = _unit_vec()
        cache.store(query_embedding=v, response="secret", tenant_ctx=_CTX)
        assert cache.lookup(query_embedding=v, tenant_ctx=_CTX2) is None

    def test_stats_empty_tenant(self):
        cache = SemanticCache()
        stats = cache.stats(tenant_ctx=_CTX)
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["cached_entries"] == 0
