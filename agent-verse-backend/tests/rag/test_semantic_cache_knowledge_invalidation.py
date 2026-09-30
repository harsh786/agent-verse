"""KB-05: cached answers do not survive a change to the tenant's knowledge.

Nothing consumed ``knowledge.updated``, so semantic-cache answers built from
deleted / expired / changed knowledge kept being served; each replica's L1 was
never invalidated, and ``DELETE /knowledge/cache`` cleared only the serving pod.

The cache is now namespaced by a per-tenant knowledge generation kept in Redis:
every knowledge write bumps it, and every replica reads it on lookup, so all
layers (L1 on every pod, the Redis L2, the pgvector backend) miss afterwards.
"""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import pytest

from app.rag.semantic_cache import SemanticCache, bump_knowledge_generation
from app.rag.store import KnowledgeStore
from app.rag.vector_cache_backend import InMemoryCacheBackend
from app.tenancy.context import PlanTier, TenantContext

TENANT = "t-kb05"
CTX = TenantContext(tenant_id=TENANT, plan=PlanTier.PROFESSIONAL, api_key_id="k")
EMB = [0.1, 0.2, 0.3, 0.4]


@pytest.fixture
def redis() -> Any:
    return fakeredis.aioredis.FakeRedis()


async def test_invalidation_makes_a_cached_answer_miss_locally() -> None:
    cache = SemanticCache()
    await cache.store_async(EMB, "q", "cached answer", TENANT)
    assert (await cache.get_similar(EMB, TENANT)) is not None

    await cache.invalidate_tenant(TENANT)

    assert await cache.get_similar(EMB, TENANT) is None


async def test_a_bump_from_another_process_invalidates_every_replica(redis: Any) -> None:
    backend = InMemoryCacheBackend()
    replica_a = SemanticCache(redis=redis, backend=backend)
    replica_b = SemanticCache(redis=redis, backend=backend)
    await replica_a.store_async(EMB, "q", "cached answer", TENANT)
    # Warm replica B's own L1 too.
    assert (await replica_b.get_similar(EMB, TENANT)) is not None
    assert (await replica_a.get_similar(EMB, TENANT)) is not None

    # E.g. the Celery worker indexed a document: it only has Redis.
    await bump_knowledge_generation(TENANT, redis=redis)

    assert await replica_a.get_similar(EMB, TENANT) is None
    assert await replica_b.get_similar(EMB, TENANT) is None
    # Other tenants are untouched.
    await replica_a.store_async(EMB, "q", "other", "t-other")
    await bump_knowledge_generation(TENANT, redis=redis)
    assert (await replica_b.get_similar(EMB, "t-other")) is not None


async def test_clearing_the_cache_reaches_other_replicas(redis: Any) -> None:
    replica_a = SemanticCache(redis=redis)
    replica_b = SemanticCache(redis=redis)
    await replica_b.store_async(EMB, "q", "cached answer", TENANT)
    assert (await replica_b.get_similar(EMB, TENANT)) is not None

    await replica_a.clear_async(tenant_ctx=CTX)  # served by pod A

    assert await replica_b.get_similar(EMB, TENANT) is None


async def test_an_unreadable_generation_is_a_miss_not_a_stale_hit(redis: Any) -> None:
    cache = SemanticCache(redis=redis)
    await cache.store_async(EMB, "q", "cached answer", TENANT)

    async def _broken_get(*_a: Any, **_k: Any) -> Any:
        raise ConnectionError("redis down")

    redis.get = _broken_get
    assert await cache.get_similar(EMB, TENANT) is None


async def test_knowledge_writes_notify_the_cache() -> None:
    cache = SemanticCache()
    store = KnowledgeStore()
    store.add_change_listener(cache.invalidate_tenant)
    from app.rag.models import KnowledgeCollection

    store.create_collection(KnowledgeCollection(name="c", collection_id="col"), tenant_ctx=CTX)
    await cache.store_async(EMB, "q", "cached answer", TENANT)

    await store.ingest_document(collection_id="col", content="New fact.", tenant_ctx=CTX)
    assert await cache.get_similar(EMB, TENANT) is None

    await cache.store_async(EMB, "q", "cached again", TENANT)
    doc_id = next(iter({c.document_id for c in store._data[(TENANT, "col")].chunks}))
    await store.delete_document_async(doc_id, collection_id="col", tenant_ctx=CTX)
    assert await cache.get_similar(EMB, TENANT) is None
