"""KB-28: cache warming stores no empty placeholder answers.

Prefetch warming stored ``response=""`` rows in L1, Redis and the pgvector
``semantic_cache_entries`` table; they were filtered on read but took space.
"""

from __future__ import annotations

import fakeredis.aioredis

from app.rag.semantic_cache import SemanticCache
from app.rag.vector_cache_backend import InMemoryCacheBackend


async def test_warming_with_queries_stores_no_entries() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    backend = InMemoryCacheBackend()
    cache = SemanticCache(redis=redis, backend=backend)

    stored = await cache.warm(
        queries=["find open tickets", "list projects"],
        embeddings=[[0.1] * 8, [0.2] * 8],
        tenant_id="t1",
    )

    assert stored == 0
    assert cache._l1._store.get("t1", {}) == {}
    assert await redis.scard("scv2:idx:t1") == 0
    assert (await backend.stats("t1")).get("entries", 0) == 0


async def test_an_empty_response_is_never_stored() -> None:
    cache = SemanticCache()
    await cache.store_async([0.3] * 8, "q", "", "t1")
    assert cache._l1._store.get("t1", {}) == {}
