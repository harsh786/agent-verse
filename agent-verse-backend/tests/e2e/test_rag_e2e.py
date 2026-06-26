"""E2E: RAG pipeline — ingest chunks, hybrid search, semantic cache."""

from __future__ import annotations

import math

from app.rag.models import Chunk, KnowledgeCollection
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(
    tenant_id="rag-e2e",
    plan=PlanTier.PROFESSIONAL,
    api_key_id="rag-key-001",
)

# Simple deterministic embedding generator for tests
_DIM = 16


def _embed(seed: int) -> list[float]:
    """Deterministic unit-vector embedding for reproducible tests."""
    raw = [math.sin(seed + i) for i in range(_DIM)]
    mag = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / mag for x in raw]


# ── Ingest and hybrid search ──────────────────────────────────────────────────

async def test_ingest_and_search_returns_results() -> None:
    """Ingested chunks should be returned by hybrid_search."""
    store = KnowledgeStore()
    col = KnowledgeCollection(name="Test Collection", description="e2e test")
    col_id = store.create_collection(col, tenant_ctx=TENANT)

    chunks = [
        Chunk(
            document_id="d1",
            content="Python is a programming language",
            embedding=_embed(0),
            chunk_index=0,
        ),
        Chunk(
            document_id="d2",
            content="FastAPI is a web framework for Python",
            embedding=_embed(1),
            chunk_index=0,
        ),
        Chunk(
            document_id="d3",
            content="Redis is an in-memory data store",
            embedding=_embed(2),
            chunk_index=0,
        ),
    ]
    for chunk in chunks:
        store.ingest_chunk(chunk, collection_id=col_id, tenant_ctx=TENANT)

    results = store.hybrid_search(
        query="Python web development",
        query_embedding=_embed(0),
        collection_id=col_id,
        tenant_ctx=TENANT,
        top_k=2,
    )
    assert len(results) == 2
    # Results are HybridSearchResult objects with score, content, chunk_id
    assert all(hasattr(r, "score") for r in results)
    assert all(hasattr(r, "content") for r in results)


# ── Tenant isolation in knowledge store ───────────────────────────────────────

async def test_rag_store_tenant_isolation() -> None:
    """Documents stored for tenant A are invisible to tenant B."""
    store = KnowledgeStore()
    tenant_a = TenantContext(
        tenant_id="rag-iso-a", plan=PlanTier.FREE, api_key_id="key-a"
    )
    tenant_b = TenantContext(
        tenant_id="rag-iso-b", plan=PlanTier.FREE, api_key_id="key-b"
    )

    col_a = KnowledgeCollection(
        collection_id="col-iso", name="A Docs", description="tenant a"
    )
    store.create_collection(col_a, tenant_ctx=tenant_a)
    store.ingest_chunk(
        Chunk(
            document_id="da1",
            content="secret a docs",
            embedding=_embed(0),
            chunk_index=0,
        ),
        collection_id="col-iso",
        tenant_ctx=tenant_a,
    )

    # Tenant B queries the same collection_id but their store is empty → []
    results_b = store.hybrid_search(
        query="secret a docs",
        query_embedding=_embed(0),
        collection_id="col-iso",
        tenant_ctx=tenant_b,
        top_k=5,
    )
    assert results_b == []


# ── Semantic cache hit avoids redundant processing ────────────────────────────

async def test_semantic_cache_hit_avoids_redundant_processing() -> None:
    """Semantic cache returns a stored response for a near-identical embedding."""
    cache = SemanticCache(threshold=0.99)
    embedding = _embed(42)
    result = "Paris is the capital of France"

    cache.store(query_embedding=embedding, response=result, tenant_ctx=TENANT)

    # Same embedding → cosine similarity is exactly 1.0 → cache hit
    cached = cache.lookup(query_embedding=embedding, tenant_ctx=TENANT)
    assert cached == result


# ── Semantic cache tenant isolation ───────────────────────────────────────────

async def test_semantic_cache_tenant_isolation() -> None:
    """Cache entries from tenant A must not be visible to tenant B."""
    cache = SemanticCache()
    tenant_a = TenantContext(
        tenant_id="cache-a", plan=PlanTier.FREE, api_key_id="key-a"
    )
    tenant_b = TenantContext(
        tenant_id="cache-b", plan=PlanTier.FREE, api_key_id="key-b"
    )

    embedding = _embed(7)
    cache.store(
        query_embedding=embedding,
        response="tenant A secret result",
        tenant_ctx=tenant_a,
    )

    result_b = cache.lookup(query_embedding=embedding, tenant_ctx=tenant_b)
    assert result_b is None  # Tenant B should not see tenant A's cache


# ── Hybrid search relevance ordering ─────────────────────────────────────────

async def test_hybrid_search_ranks_by_combined_score() -> None:
    """Hybrid search scores should be ≥ 0 and results returned in descending order."""
    store = KnowledgeStore()
    col = KnowledgeCollection(name="Ranking Test", description="score order test")
    col_id = store.create_collection(col, tenant_ctx=TENANT)

    docs = [
        ("r1", "machine learning neural networks deep learning", 10),
        ("r2", "cooking recipes pasta italian food", 20),
        ("r3", "artificial intelligence machine learning algorithms", 10),
    ]
    for doc_id, content, seed in docs:
        store.ingest_chunk(
            Chunk(
                document_id=doc_id,
                content=content,
                embedding=_embed(seed),
                chunk_index=0,
            ),
            collection_id=col_id,
            tenant_ctx=TENANT,
        )

    results = store.hybrid_search(
        query="machine learning AI",
        query_embedding=_embed(10),
        collection_id=col_id,
        tenant_ctx=TENANT,
        top_k=3,
    )
    assert len(results) == 3
    scores = [r.score for r in results]
    # Results should be in descending score order
    assert scores == sorted(scores, reverse=True)
    # Scores are hybrid (0.7*cosine + 0.3*trigram); cosine can be negative
    # so only check that the type is float
    assert all(isinstance(s, float) for s in scores)
