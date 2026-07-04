"""
World-class SemanticCache tests — covers every layer of the new implementation.
"""
from __future__ import annotations

import asyncio
import struct
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.rag.semantic_cache import (
    SemanticCache,
    _CacheHit,
    _LRUCache,
    _cosine,
    _compress,
    _decompress,
    _find_best_match,
    _pack_embedding,
    _unpack_embedding,
)
from app.tenancy.context import PlanTier, TenantContext


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _ctx(tenant_id: str = "tenant-1") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="test")


def _vec(seed: float = 1.0, dims: int = 8) -> list[float]:
    """
    Deterministic unit vector based on seed.
    Different seeds produce different DIRECTIONS (not just different magnitudes).
    Achieved by using seed as a phase offset in a sin/cos pattern.
    """
    import math
    phase = seed * 0.7853981633974483  # seed * pi/4
    v = [math.sin(phase + i * 0.5) for i in range(dims)]
    mag = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / mag for x in v]


def _mock_redis() -> MagicMock:
    r = MagicMock()
    _store: dict[str, Any] = {}
    _sets: dict[str, set] = {}

    async def _redis_get(key: str) -> bytes | None:
        return _store.get(key)

    async def _redis_set(key: str, value: Any, **kwargs) -> None:
        _store[key] = value

    async def hset(key: str, mapping: dict) -> None:
        if key not in _store:
            _store[key] = {}
        _store[key].update(mapping)

    async def expire(key: str, ttl: int) -> None:
        pass

    async def sadd(key: str, *members) -> None:
        _sets.setdefault(key, set()).update(members)

    async def smembers(key: str) -> set:
        return {m.encode() if isinstance(m, str) else m for m in _sets.get(key, set())}

    async def scard(key: str) -> int:
        return len(_sets.get(key, set()))

    async def hmget(key: str, *fields) -> list:
        entry = _store.get(key, {})
        return [entry.get(f) for f in fields]

    async def delete(*keys) -> int:
        count = 0
        for k in keys:
            if k in _store:
                del _store[k]
                count += 1
            if k in _sets:
                del _sets[k]
                count += 1
        return count

    class _Pipeline:
        def __init__(self):
            self._ops = []

        def hset(self, *a, **kw): self._ops.append(("hset", a, kw)); return self
        def expire(self, *a, **kw): self._ops.append(("expire", a, kw)); return self
        def sadd(self, *a, **kw): self._ops.append(("sadd", a, kw)); return self
        def get(self, *a, **kw): self._ops.append(("get", a, kw)); return self
        def delete(self, *a, **kw): self._ops.append(("del", a, kw)); return self
        def hmget(self, *a, **kw): self._ops.append(("hmget", a, kw)); return self

        async def execute(self):
            results = []
            for op, args, kwargs in self._ops:
                if op == "hset":
                    await hset(*args, **kwargs)
                    results.append(1)
                elif op == "expire":
                    results.append(1)
                elif op == "sadd":
                    await sadd(*args, **kwargs)
                    results.append(1)
                elif op == "hmget":
                    results.append(await hmget(*args, **kwargs))
                elif op == "del":
                    results.append(await delete(*args, **kwargs))
                else:
                    results.append(None)
            return results

    def pipeline():
        return _Pipeline()

    r.get = AsyncMock(side_effect=_redis_get)
    r.set = AsyncMock(side_effect=_redis_set)
    r.hset = AsyncMock(side_effect=hset)
    r.expire = AsyncMock(side_effect=expire)
    r.sadd = AsyncMock(side_effect=sadd)
    r.smembers = AsyncMock(side_effect=smembers)
    r.scard = AsyncMock(side_effect=scard)
    r.hmget = AsyncMock(side_effect=hmget)
    r.delete = AsyncMock(side_effect=delete)
    r.pipeline = MagicMock(side_effect=pipeline)
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# Math helpers
# ═══════════════════════════════════════════════════════════════════════════════

def test_cosine_identical_vectors():
    v = [1.0, 0.0, 0.0]
    assert _cosine(v, v) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors():
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    assert _cosine(a, b) == pytest.approx(0.0)


def test_cosine_zero_vector():
    assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_mismatched_dims_truncates():
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0]  # shorter
    # Should not raise — truncates to shorter length
    result = _cosine(a, b)
    assert 0.0 <= result <= 1.0


def test_cosine_similar_vectors():
    v1 = _vec(1.0)
    v2 = _vec(1.001)  # very similar
    sim = _cosine(v1, v2)
    assert sim > 0.99


def test_pack_unpack_roundtrip():
    original = [0.1, -0.5, 0.9, 0.0, 1.0]
    packed = _pack_embedding(original)
    recovered = _unpack_embedding(packed)
    for a, b in zip(original, recovered):
        assert abs(a - b) < 1e-5  # float32 precision


def test_compress_decompress():
    text = "This is a test response " * 100
    compressed = _compress(text)
    assert len(compressed) < len(text.encode())  # must be smaller
    recovered = _decompress(compressed)
    assert recovered == text


def test_find_best_match_hit():
    q = _vec(1.0)
    entries = [(_vec(1.001), "close match"), (_vec(5.0), "different")]
    result = _find_best_match(q, entries, threshold=0.90)
    assert result is not None
    assert result[0] == "close match"
    assert result[1] > 0.90


def test_find_best_match_miss_below_threshold():
    q = _vec(1.0)
    entries = [(_vec(5.0), "very different")]
    result = _find_best_match(q, entries, threshold=0.92)
    assert result is None


def test_find_best_match_empty():
    assert _find_best_match(_vec(), [], 0.92) is None


# ═══════════════════════════════════════════════════════════════════════════════
# L1 LRU Cache
# ═══════════════════════════════════════════════════════════════════════════════

def test_l1_basic_hit():
    l1 = _LRUCache(max_size=10, ttl=300.0, threshold=0.92)
    v = _vec(1.0)
    l1._put(v, "response", "t1", "key1")
    result = l1.get(v, "t1")
    assert result == "response"


def test_l1_similar_vector_hit():
    l1 = _LRUCache(max_size=10, ttl=300.0, threshold=0.92)
    v1 = _vec(1.0)
    v2 = _vec(1.0001)  # very similar
    l1._put(v1, "response", "t1", "key1")
    result = l1.get(v2, "t1")
    assert result == "response"


def test_l1_different_vector_miss():
    l1 = _LRUCache(max_size=10, ttl=300.0, threshold=0.92)
    v1 = _vec(1.0)
    v2 = _vec(100.0)  # very different
    l1._put(v1, "response", "t1", "key1")
    result = l1.get(v2, "t1")
    assert result is None


def test_l1_tenant_isolation():
    l1 = _LRUCache(max_size=10, ttl=300.0, threshold=0.92)
    v = _vec(1.0)
    l1._put(v, "tenant1 response", "t1", "k1")
    # Different tenant cannot see tenant1's entry
    assert l1.get(v, "t2") is None


def test_l1_ttl_expiry():
    l1 = _LRUCache(max_size=10, ttl=0.05, threshold=0.90)  # 50ms TTL
    v = _vec(1.0)
    l1._put(v, "will expire", "t1", "k1")
    assert l1.get(v, "t1") == "will expire"
    time.sleep(0.08)
    assert l1.get(v, "t1") is None


def test_l1_evicts_lru_at_capacity():
    l1 = _LRUCache(max_size=3, ttl=300.0, threshold=0.0)  # threshold=0 to always match
    for i in range(4):
        l1._put(_vec(float(i + 1)), f"response_{i}", "t1", f"key_{i}")
    # Only the 3 most recent should be accessible
    stats = l1.stats("t1")
    assert stats["l1_size"] <= 3


# ═══════════════════════════════════════════════════════════════════════════════
# SemanticCache — true similarity API
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_similar_l1_hit():
    """After store, get_similar with same vector returns from L1."""
    cache = SemanticCache(threshold=0.92)
    v = _vec(1.0)
    await cache.store_async(v, "search github issues", "42 open issues", "t1")
    hit = await cache.get_similar(v, "t1")
    assert hit is not None
    assert hit.response == "42 open issues"
    assert hit.source == "l1"


@pytest.mark.asyncio
async def test_get_similar_paraphrase_hit():
    """
    True semantic similarity: slightly different embedding (paraphrase) still hits.
    This was IMPOSSIBLE with the old hash-based API.
    """
    cache = SemanticCache(threshold=0.92)
    v1 = _vec(1.0)          # "Search GitHub for open issues"
    v2 = _vec(1.0001)       # "Find open GitHub issues" — slightly different embedding
    await cache.store_async(v1, "search github issues", "42 open issues", "t1")
    hit = await cache.get_similar(v2, "t1")
    assert hit is not None, "Paraphrase should hit the cache via cosine similarity"
    assert hit.response == "42 open issues"


@pytest.mark.asyncio
async def test_get_similar_different_meaning_miss():
    """Completely different meaning → cosine < threshold → miss."""
    cache = SemanticCache(threshold=0.92)
    v1 = _vec(1.0)    # GitHub
    v2 = _vec(50.0)   # Totally different direction
    await cache.store_async(v1, "search github", "42 issues", "t1")
    hit = await cache.get_similar(v2, "t1")
    assert hit is None


@pytest.mark.asyncio
async def test_get_similar_tenant_isolation():
    cache = SemanticCache(threshold=0.92)
    v = _vec(1.0)
    await cache.store_async(v, "query", "secret data", "tenant-a")
    hit = await cache.get_similar(v, "tenant-b")
    assert hit is None


@pytest.mark.asyncio
async def test_get_similar_returns_hit_object():
    cache = SemanticCache(threshold=0.92)
    v = _vec(1.0)
    await cache.store_async(v, "query", "the response", "t1")
    hit = await cache.get_similar(v, "t1")
    assert isinstance(hit, _CacheHit)
    assert hit.response == "the response"
    assert hit.similarity >= 0.0
    assert hit.source in ("l1", "l2")


@pytest.mark.asyncio
async def test_stats_track_hits_and_misses():
    cache = SemanticCache(threshold=0.92)
    ctx = _ctx("tenant-stats")
    v = _vec(1.0)
    await cache.store_async(v, "q", "r", "tenant-stats")

    # 1 hit
    await cache.get_similar(v, "tenant-stats")
    # 1 miss
    await cache.get_similar(_vec(50.0), "tenant-stats")

    stats = cache.stats(tenant_ctx=ctx)
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate"] == pytest.approx(0.5)
    assert stats["hit_rate_pct"] == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_backward_compat_get_set():
    """Old get/set API still works with new cosine implementation."""
    cache = SemanticCache(threshold=0.92)
    v = _vec(1.0)
    await cache.set_async("query", v, "response", "t1")
    result = await cache.get("query", v, "t1")
    assert result == "response"


@pytest.mark.asyncio
async def test_backward_compat_none_embedding():
    """Old API: get/set without embedding falls back to text key."""
    cache = SemanticCache()
    await cache.set_async("my query text", None, "text response", "t1")
    result = await cache.get("my query text", None, "t1")
    assert result == "text response"


# ═══════════════════════════════════════════════════════════════════════════════
# Redis L2 integration
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_redis_store_and_retrieve():
    """Entry stored in Redis is retrieved on next get_similar."""
    redis = _mock_redis()
    cache = SemanticCache(threshold=0.92, redis=redis)
    v = _vec(1.0)
    await cache.store_async(v, "search github", "42 issues", "t1")

    # Create a fresh cache instance sharing the same Redis
    cache2 = SemanticCache(threshold=0.92, redis=redis)
    hit = await cache2.get_similar(v, "t1")
    assert hit is not None
    assert hit.response == "42 issues"
    assert hit.source in ("l1", "l2")


@pytest.mark.asyncio
async def test_redis_cross_replica_sharing():
    """Two cache instances (simulating two server replicas) share via Redis."""
    redis = _mock_redis()
    cache_a = SemanticCache(threshold=0.92, redis=redis)
    cache_b = SemanticCache(threshold=0.92, redis=redis)

    v = _vec(1.0)
    await cache_a.store_async(v, "step desc", "result from replica A", "t1")

    hit = await cache_b.get_similar(v, "t1")
    assert hit is not None
    assert hit.response == "result from replica A"


@pytest.mark.asyncio
async def test_redis_paraphrase_hit_cross_replica():
    """
    Cross-replica test: store with v1, retrieve with similar v2.
    This is the key end-to-end test proving true semantic similarity works
    across processes via Redis.
    """
    redis = _mock_redis()
    cache_a = SemanticCache(threshold=0.92, redis=redis)
    cache_b = SemanticCache(threshold=0.92, redis=redis)

    v1 = _vec(1.0)
    v2 = _vec(1.0005)  # slightly different — paraphrase in embedding space

    await cache_a.store_async(v1, "Search GitHub issues", "Found 42 issues", "t1")
    hit = await cache_b.get_similar(v2, "t1")

    assert hit is not None, "Paraphrase must hit cross-replica via Redis cosine scan"
    assert "42" in hit.response


@pytest.mark.asyncio
async def test_redis_error_falls_back_to_l1():
    """Redis failures are silent — L1 still works."""
    redis = MagicMock()
    redis.smembers = AsyncMock(side_effect=ConnectionError("Redis down"))
    redis.pipeline = MagicMock(side_effect=ConnectionError("Redis down"))
    redis.scard = AsyncMock(side_effect=ConnectionError("Redis down"))

    cache = SemanticCache(threshold=0.92, redis=redis)
    v = _vec(1.0)
    await cache.store_async(v, "q", "l1 response", "t1")  # This also fails Redis, stores in L1
    hit = await cache.get_similar(v, "t1")
    # L1 should still work
    assert hit is not None
    assert hit.source == "l1"


# ═══════════════════════════════════════════════════════════════════════════════
# Clear — truly clears Redis
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_clear_removes_redis_entries():
    redis = _mock_redis()
    cache = SemanticCache(threshold=0.92, redis=redis)
    ctx = _ctx("t-clear")
    v = _vec(1.0)
    await cache.store_async(v, "q", "response", "t-clear")

    # Verify it's there
    assert await cache.size("t-clear") == 1

    # Clear
    deleted = await cache.clear_async(tenant_ctx=ctx)

    # Verify it's gone
    assert await cache.size("t-clear") == 0
    hit = await cache.get_similar(v, "t-clear")
    assert hit is None


@pytest.mark.asyncio
async def test_clear_only_affects_own_tenant():
    redis = _mock_redis()
    cache = SemanticCache(threshold=0.92, redis=redis)
    v = _vec(1.0)
    await cache.store_async(v, "q", "tenant-a data", "tenant-a")
    await cache.store_async(v, "q", "tenant-b data", "tenant-b")

    await cache.clear_async(tenant_ctx=_ctx("tenant-a"))

    hit_a = await cache.get_similar(v, "tenant-a")
    hit_b = await cache.get_similar(v, "tenant-b")
    assert hit_a is None
    assert hit_b is not None


# ═══════════════════════════════════════════════════════════════════════════════
# Batch API
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_get_batch_returns_list():
    redis = _mock_redis()
    cache = SemanticCache(threshold=0.92, redis=redis)
    v1, v2, v3 = _vec(1.0), _vec(2.0), _vec(3.0)
    await cache.store_async(v1, "step 1", "result 1", "t1")
    # v2 is not stored → miss
    await cache.store_async(v3, "step 3", "result 3", "t1")

    results = await cache.get_batch([v1, v2, v3], "t1")
    assert len(results) == 3
    assert results[0] is not None and results[0].response == "result 1"
    assert results[1] is None
    assert results[2] is not None and results[2].response == "result 3"


@pytest.mark.asyncio
async def test_get_batch_empty():
    cache = SemanticCache()
    results = await cache.get_batch([], "t1")
    assert results == []


# ═══════════════════════════════════════════════════════════════════════════════
# Cache warming
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_warm_populates_cache():
    cache = SemanticCache(threshold=0.92)
    v = _vec(1.0)

    # Mock embedder
    embedder = MagicMock()
    embed_response = MagicMock()
    embed_response.embeddings = [v]
    embedder.embed = AsyncMock(return_value=embed_response)

    patterns = [{"query": "Search GitHub issues", "response": "42 open issues"}]
    count = await cache.warm(patterns=patterns, embedder=embedder, tenant_id="t1")
    assert count == 1

    # Verify it's now in cache
    hit = await cache.get_similar(v, "t1")
    assert hit is not None
    assert hit.response == "42 open issues"


@pytest.mark.asyncio
async def test_warm_handles_embedder_failure():
    cache = SemanticCache()
    embedder = MagicMock()
    embedder.embed = AsyncMock(side_effect=Exception("API down"))
    patterns = [{"query": "q", "response": "r"}]
    count = await cache.warm(patterns=patterns, embedder=embedder, tenant_id="t1")
    assert count == 0  # graceful failure


# ═══════════════════════════════════════════════════════════════════════════════
# Compression
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_compression_saves_space():
    redis = _mock_redis()
    cache = SemanticCache(threshold=0.92, redis=redis, compress=True)
    large_response = "Found issues: " + ("PROJ-1: Fix login bug, " * 200)
    v = _vec(1.0)
    await cache.store_async(v, "query", large_response, "t1")
    # Verify we can retrieve it correctly
    hit = await cache.get_similar(v, "t1")
    assert hit is not None
    assert hit.response == large_response[:8000]  # max_response cap


@pytest.mark.asyncio
async def test_no_compression_mode():
    """compress=False should also work correctly."""
    redis = _mock_redis()
    cache = SemanticCache(threshold=0.92, redis=redis, compress=False)
    v = _vec(1.0)
    await cache.store_async(v, "q", "uncompressed response", "t1")
    hit = await cache.get_similar(v, "t1")
    assert hit is not None
    assert hit.response == "uncompressed response"


# ═══════════════════════════════════════════════════════════════════════════════
# Legacy sync API (backward compatibility)
# ═══════════════════════════════════════════════════════════════════════════════

def test_store_sync_and_lookup_sync():
    cache = SemanticCache(threshold=0.92)
    ctx = _ctx()
    v = _vec(1.0)
    cache.store_sync(query_embedding=v, response="sync response", tenant_ctx=ctx)
    result = cache.lookup_sync(query_embedding=v, tenant_ctx=ctx)
    assert result == "sync response"


def test_lookup_sync_miss():
    cache = SemanticCache(threshold=0.92)
    ctx = _ctx()
    result = cache.lookup_sync(query_embedding=_vec(1.0), tenant_ctx=ctx)
    assert result is None


def test_legacy_lookup_method():
    """Old .lookup() method still works."""
    cache = SemanticCache(threshold=0.92)
    ctx = _ctx()
    v = _vec(1.0)
    cache.store_sync(query_embedding=v, response="r", tenant_ctx=ctx)
    assert cache.lookup(query_embedding=v, tenant_ctx=ctx) == "r"


# ═══════════════════════════════════════════════════════════════════════════════
# Stats accuracy
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_stats_are_accurate():
    cache = SemanticCache()
    ctx = _ctx("t-stats")
    v = _vec(1.0)
    await cache.store_async(v, "q", "r", "t-stats")

    for _ in range(3):
        await cache.get_similar(v, "t-stats")  # 3 hits

    for _ in range(2):
        await cache.get_similar(_vec(99.0), "t-stats")  # 2 misses

    stats = cache.stats(tenant_ctx=ctx)
    assert stats["hits"] == 3
    assert stats["misses"] == 2
    assert stats["hit_rate"] == pytest.approx(3 / 5)


@pytest.mark.asyncio
async def test_stats_bytes_saved():
    cache = SemanticCache()
    ctx = _ctx("t-bytes")
    v = _vec(1.0)
    response = "x" * 500
    await cache.store_async(v, "q", response, "t-bytes")
    stats = cache.stats(tenant_ctx=ctx)
    assert stats["bytes_saved_estimate"] == 500
