"""Regression: the SemanticCache L2 backend was read but never written.

``get_similar`` queried ``self._backend`` on every lookup, but ``store_async``
wrote only L1 + Redis and ``clear_async`` never cleared the backend — so
``semantic_cache_entries`` stayed empty (every backend lookup a guaranteed miss)
and a "cleared" tenant would have kept being served from it.
"""

from __future__ import annotations

from app.rag.semantic_cache import SemanticCache
from app.rag.vector_cache_backend import InMemoryCacheBackend
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="sc-t1", plan=PlanTier.FREE, api_key_id="k")
_EMB = [0.1, 0.2, 0.3, 0.4]


async def test_store_writes_through_to_backend() -> None:
    backend = InMemoryCacheBackend()
    cache = SemanticCache(backend=backend)
    await cache.store_async(_EMB, "q", "cached answer", "sc-t1")
    hit = await backend.get_similar(_EMB, "sc-t1", threshold=0.99)
    assert hit is not None and hit["response"] == "cached answer"


async def test_another_replica_hits_via_the_backend() -> None:
    backend = InMemoryCacheBackend()  # shared durable L2
    await SemanticCache(backend=backend).store_async(_EMB, "q", "answer", "sc-t1")
    other = SemanticCache(backend=backend)  # empty L1, no Redis
    hit = await other.get_similar(_EMB, "sc-t1")
    assert hit is not None and hit.response == "answer" and hit.source == "l2_ann"


async def test_clear_clears_the_backend() -> None:
    backend = InMemoryCacheBackend()
    cache = SemanticCache(backend=backend)
    await cache.store_async(_EMB, "q", "answer", "sc-t1")
    await cache.clear_async(tenant_ctx=_CTX)
    assert await backend.get_similar(_EMB, "sc-t1", threshold=0.5) is None
    assert await SemanticCache(backend=backend).get_similar(_EMB, "sc-t1") is None
