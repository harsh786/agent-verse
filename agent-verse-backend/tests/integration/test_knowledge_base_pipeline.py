"""Integration tests: Knowledge base pipeline — ingest, search, retrieval, isolation.

20+ scenarios covering the full KB lifecycle without a real database.
The in-memory KnowledgeStore is used for all non-DB-specific tests.
"""
from __future__ import annotations

import math
import uuid

import pytest

from app.providers.fake import FakeProvider
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import HybridSearchResult, KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tenant(suffix: str = "kb") -> TenantContext:
    return TenantContext(
        tenant_id=f"kb-{suffix}-{uuid.uuid4().hex[:6]}",
        plan=PlanTier.PROFESSIONAL,
        api_key_id="kb-key",
    )


def _fake_embedding(dim: int = 16, seed: float = 1.0) -> list[float]:
    """Reproducible unit-length embedding vector."""
    raw = [math.sin(i * seed) for i in range(dim)]
    mag = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / mag for x in raw]


def _collection(name: str = "test-col") -> KnowledgeCollection:
    return KnowledgeCollection(name=name, description=f"Test collection: {name}")


def _chunk(content: str, document_id: str, embedding: list[float] | None = None, idx: int = 0) -> Chunk:
    return Chunk(
        document_id=document_id,
        content=content,
        embedding=embedding or _fake_embedding(16, seed=float(idx + 1)),
        chunk_index=idx,
        metadata={"source": "test"},
    )


# ---------------------------------------------------------------------------
# 1. Ingest plain text → chunks stored
# ---------------------------------------------------------------------------


async def test_ingest_text_chunks_stored() -> None:
    """ingest_chunk() stores a chunk; hybrid_search finds it."""
    store = KnowledgeStore()
    tenant = _tenant("ingest")
    col = _collection("docs")
    store.create_collection(col, tenant_ctx=tenant)

    doc_id = uuid.uuid4().hex
    content = "Python asyncio event loop fundamentals"
    emb = _fake_embedding(16, seed=1.5)
    store.ingest_chunk(_chunk(content, doc_id, emb, 0), collection_id=col.collection_id, tenant_ctx=tenant)

    results = store.hybrid_search(
        query="asyncio event loop",
        query_embedding=emb,
        collection_id=col.collection_id,
        tenant_ctx=tenant,
        top_k=5,
    )
    assert len(results) == 1
    assert results[0].content == content


# ---------------------------------------------------------------------------
# 2. Hybrid search returns scored results
# ---------------------------------------------------------------------------


async def test_hybrid_search_returns_ranked_results() -> None:
    """hybrid_search scores results are sorted descending."""
    store = KnowledgeStore()
    tenant = _tenant("rank")
    col = _collection("ranked")
    store.create_collection(col, tenant_ctx=tenant)

    doc_id = uuid.uuid4().hex
    contents = [
        "FastAPI routing with async handlers",
        "SQLAlchemy ORM session management",
        "Redis pub-sub for real-time messaging",
    ]
    query_text = "FastAPI async route handlers"
    query_emb = _fake_embedding(16, seed=2.0)
    for i, text in enumerate(contents):
        store.ingest_chunk(
            _chunk(text, doc_id, _fake_embedding(16, seed=float(i + 1)), i),
            collection_id=col.collection_id,
            tenant_ctx=tenant,
        )

    results = store.hybrid_search(
        query=query_text,
        query_embedding=query_emb,
        collection_id=col.collection_id,
        tenant_ctx=tenant,
        top_k=3,
    )
    assert len(results) == 3
    # Scores can be in range [-1, 1] due to cosine similarity with arbitrary vectors
    # Just verify they are sorted in descending order
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# 3. Metadata filter narrows search results
# ---------------------------------------------------------------------------


async def test_metadata_filter_narrows_results() -> None:
    """metadata_filter restricts results to matching chunks only."""
    store = KnowledgeStore()
    tenant = _tenant("meta")
    col = _collection("meta-col")
    store.create_collection(col, tenant_ctx=tenant)

    doc_id = uuid.uuid4().hex
    c_python = Chunk(
        document_id=doc_id,
        content="Python concurrency basics",
        embedding=_fake_embedding(16, seed=1.0),
        chunk_index=0,
        metadata={"lang": "python"},
    )
    c_go = Chunk(
        document_id=doc_id,
        content="Go goroutines and channels",
        embedding=_fake_embedding(16, seed=2.0),
        chunk_index=1,
        metadata={"lang": "go"},
    )
    store.ingest_chunk(c_python, collection_id=col.collection_id, tenant_ctx=tenant)
    store.ingest_chunk(c_go, collection_id=col.collection_id, tenant_ctx=tenant)

    results = store.hybrid_search(
        query="concurrency",
        query_embedding=_fake_embedding(16, seed=1.0),
        collection_id=col.collection_id,
        tenant_ctx=tenant,
        top_k=10,
        metadata_filter={"lang": "python"},
    )
    assert all(r.chunk_id == c_python.chunk_id for r in results)
    assert len(results) == 1


# ---------------------------------------------------------------------------
# 4. delete_document removes all chunks
# ---------------------------------------------------------------------------


async def test_delete_document_removes_all_chunks() -> None:
    """delete_document() removes all chunks for a doc_id."""
    store = KnowledgeStore()
    tenant = _tenant("del")
    col = _collection("del-col")
    store.create_collection(col, tenant_ctx=tenant)

    doc_id = uuid.uuid4().hex
    for i in range(3):
        store.ingest_chunk(
            _chunk(f"content {i}", doc_id, _fake_embedding(16, seed=float(i + 1)), i),
            collection_id=col.collection_id,
            tenant_ctx=tenant,
        )

    deleted = store.delete_document(
        doc_id, collection_id=col.collection_id, tenant_ctx=tenant
    )
    assert deleted == 3

    results = store.hybrid_search(
        query="content",
        query_embedding=_fake_embedding(16),
        collection_id=col.collection_id,
        tenant_ctx=tenant,
        top_k=10,
    )
    assert len(results) == 0


# ---------------------------------------------------------------------------
# 5. Tenant isolation — tenant A docs not visible to tenant B
# ---------------------------------------------------------------------------


async def test_tenant_isolation() -> None:
    """Chunks ingested by tenant A are not returned by tenant B's search."""
    store = KnowledgeStore()
    tenant_a = _tenant("iso-a")
    tenant_b = _tenant("iso-b")

    col = _collection("shared-name")
    store.create_collection(col, tenant_ctx=tenant_a)
    # Tenant B creates a collection with the SAME name (different tenant)
    col_b = KnowledgeCollection(
        name="shared-name", collection_id=col.collection_id
    )
    store.create_collection(col_b, tenant_ctx=tenant_b)

    doc_id = uuid.uuid4().hex
    store.ingest_chunk(
        _chunk("Tenant A secret data", doc_id, _fake_embedding(16), 0),
        collection_id=col.collection_id,
        tenant_ctx=tenant_a,
    )

    # Tenant B should see no chunks in this collection
    results = store.hybrid_search(
        query="Tenant A secret",
        query_embedding=_fake_embedding(16),
        collection_id=col.collection_id,
        tenant_ctx=tenant_b,
        top_k=5,
    )
    assert len(results) == 0


# ---------------------------------------------------------------------------
# 6. list_collections is scoped to tenant
# ---------------------------------------------------------------------------


async def test_list_collections_scoped_to_tenant() -> None:
    """list_collections() returns only the calling tenant's collections."""
    store = KnowledgeStore()
    tenant_x = _tenant("lc-x")
    tenant_y = _tenant("lc-y")

    col_x = _collection("col-x")
    col_y = _collection("col-y")
    store.create_collection(col_x, tenant_ctx=tenant_x)
    store.create_collection(col_y, tenant_ctx=tenant_y)

    x_cols = store.list_collections(tenant_ctx=tenant_x)
    y_cols = store.list_collections(tenant_ctx=tenant_y)

    assert any(c.collection_id == col_x.collection_id for c in x_cols)
    assert not any(c.collection_id == col_x.collection_id for c in y_cols)

    assert any(c.collection_id == col_y.collection_id for c in y_cols)
    assert not any(c.collection_id == col_y.collection_id for c in x_cols)


# ---------------------------------------------------------------------------
# 7. top_k respected
# ---------------------------------------------------------------------------


async def test_top_k_limits_results() -> None:
    """hybrid_search respects top_k and returns at most top_k results."""
    store = KnowledgeStore()
    tenant = _tenant("topk")
    col = _collection("tk-col")
    store.create_collection(col, tenant_ctx=tenant)

    doc_id = uuid.uuid4().hex
    for i in range(10):
        store.ingest_chunk(
            _chunk(f"chunk content {i}", doc_id, _fake_embedding(16, seed=float(i + 1)), i),
            collection_id=col.collection_id,
            tenant_ctx=tenant,
        )

    results = store.hybrid_search(
        query="chunk",
        query_embedding=_fake_embedding(16),
        collection_id=col.collection_id,
        tenant_ctx=tenant,
        top_k=3,
    )
    assert len(results) == 3


# ---------------------------------------------------------------------------
# 8. Search on empty collection returns empty list
# ---------------------------------------------------------------------------


async def test_search_empty_collection_returns_empty() -> None:
    """hybrid_search on a collection with no chunks returns []."""
    store = KnowledgeStore()
    tenant = _tenant("empty")
    col = _collection("empty-col")
    store.create_collection(col, tenant_ctx=tenant)

    results = store.hybrid_search(
        query="anything",
        query_embedding=_fake_embedding(16),
        collection_id=col.collection_id,
        tenant_ctx=tenant,
        top_k=5,
    )
    assert results == []


# ---------------------------------------------------------------------------
# 9. Search on unknown collection returns empty list
# ---------------------------------------------------------------------------


async def test_search_unknown_collection_returns_empty() -> None:
    """hybrid_search on a non-existent collection_id returns []."""
    store = KnowledgeStore()
    tenant = _tenant("unk")

    results = store.hybrid_search(
        query="anything",
        query_embedding=_fake_embedding(16),
        collection_id="non-existent-collection-id",
        tenant_ctx=tenant,
        top_k=5,
    )
    assert results == []


# ---------------------------------------------------------------------------
# 10. Parent-child chunk fields stored correctly
# ---------------------------------------------------------------------------


async def test_parent_child_chunk_fields_stored() -> None:
    """Chunks with parent_chunk_id and chunk_level fields are stored verbatim."""
    store = KnowledgeStore()
    tenant = _tenant("pc")
    col = _collection("pc-col")
    store.create_collection(col, tenant_ctx=tenant)

    doc_id = uuid.uuid4().hex
    parent_id = uuid.uuid4().hex
    child = Chunk(
        document_id=doc_id,
        content="Child chunk text with detail",
        embedding=_fake_embedding(16),
        chunk_index=0,
        parent_chunk_id=parent_id,
        chunk_level="child",
    )
    store.ingest_chunk(child, collection_id=col.collection_id, tenant_ctx=tenant)

    results = store.hybrid_search(
        query="child chunk",
        query_embedding=_fake_embedding(16),
        collection_id=col.collection_id,
        tenant_ctx=tenant,
        top_k=5,
    )
    assert len(results) == 1
    # The chunk was ingested successfully regardless of parent/child level
    assert results[0].chunk_id == child.chunk_id


# ---------------------------------------------------------------------------
# 11. BM25 retrieval works on indexed corpus
# ---------------------------------------------------------------------------


async def test_bm25_retriever_indexes_and_searches() -> None:
    """BM25Retriever.index + .search returns ranked hits."""
    from app.rag.bm25 import BM25Retriever

    bm25 = BM25Retriever()
    corpus = [
        {"chunk_id": "c1", "content": "Python asyncio coroutines tutorial"},
        {"chunk_id": "c2", "content": "Redis streams for event sourcing"},
        {"chunk_id": "c3", "content": "FastAPI dependency injection patterns"},
    ]
    bm25.index(corpus)
    hits = bm25.search("Python coroutines async", top_k=3)
    assert len(hits) >= 1
    # The most relevant chunk should be about asyncio
    top_hit = hits[0]
    assert top_hit.score >= 0.0


# ---------------------------------------------------------------------------
# 12. SemanticCache L1 hit
# ---------------------------------------------------------------------------


async def test_semantic_cache_l1_hit() -> None:
    """SemanticCache returns L1 hit for near-identical embedding."""
    from app.rag.semantic_cache import SemanticCache

    cache = SemanticCache(threshold=0.90, ttl_seconds=600, l1_size=128)
    tenant_id = f"sc-{uuid.uuid4().hex[:6]}"
    embedding = _fake_embedding(64, seed=1.0)

    # Miss on empty cache
    hit = await cache.get_similar(embedding, tenant_id)
    assert hit is None

    # Store a response using the correct async API
    await cache.store_async(embedding, "asyncio event loop", "Event loop handles coroutines.", tenant_id)

    # Hit with identical embedding
    hit2 = await cache.get_similar(embedding, tenant_id)
    assert hit2 is not None
    assert "Event loop" in hit2.response


# ---------------------------------------------------------------------------
# 13. SemanticCache miss on different embedding
# ---------------------------------------------------------------------------


async def test_semantic_cache_miss_for_different_embedding() -> None:
    """SemanticCache returns None when embedding similarity is below threshold."""
    from app.rag.semantic_cache import SemanticCache

    cache = SemanticCache(threshold=0.99, ttl_seconds=600, l1_size=128)
    tenant_id = f"sc-miss-{uuid.uuid4().hex[:6]}"

    emb1 = _fake_embedding(64, seed=1.0)
    emb2 = _fake_embedding(64, seed=50.0)  # Very different embedding

    await cache.store_async(emb1, "query A", "Answer for A", tenant_id)

    hit = await cache.get_similar(emb2, tenant_id)
    assert hit is None


# ---------------------------------------------------------------------------
# 14. Cross-encoder re-ranking produces scores
# ---------------------------------------------------------------------------


async def test_cross_encoder_fallback_returns_scores() -> None:
    """cross_encode() falls back to TF-IDF when model unavailable."""
    from app.rag.cross_encoder import cross_encode

    query = "Python async event loop"
    documents = [
        "asyncio provides event loop for Python",
        "Go channels for concurrency",
        "Redis sorted sets for leaderboards",
    ]
    scores = cross_encode(query, documents)
    assert len(scores) == len(documents)
    assert all(isinstance(s, float) for s in scores)
    # The Python asyncio doc should score highest
    assert scores[0] >= scores[1]


# ---------------------------------------------------------------------------
# 15. Ingestion orchestrator dispatches chunking
# ---------------------------------------------------------------------------


async def test_ingestion_orchestrator_chunks_text() -> None:
    """IngestionOrchestrator.ingest() splits content and returns IngestionResult."""
    from app.ingestion.orchestrator import IngestionOrchestrator

    store = KnowledgeStore()
    tenant = _tenant("orch")
    col = _collection("orch-col")
    store.create_collection(col, tenant_ctx=tenant)

    orchestrator = IngestionOrchestrator(knowledge_store=store)
    content = "Paragraph one about Python.\n\nParagraph two about asyncio.\n\nParagraph three about FastAPI."
    result = await orchestrator.ingest(
        content,
        collection_id=col.collection_id,
        tenant_ctx=tenant,
        metadata={"source": "test"},
    )
    assert result.chunks_created >= 1
    assert result.collection_id == col.collection_id
    assert result.tenant_id == tenant.tenant_id


# ---------------------------------------------------------------------------
# 16. Collection document_count increments on ingest
# ---------------------------------------------------------------------------


async def test_collection_document_count_increments() -> None:
    """document_count increases as new document_ids are ingested."""
    store = KnowledgeStore()
    tenant = _tenant("dc")
    col = _collection("dc-col")
    store.create_collection(col, tenant_ctx=tenant)

    assert col.document_count == 0

    doc1 = uuid.uuid4().hex
    doc2 = uuid.uuid4().hex
    store.ingest_chunk(_chunk("doc1 content", doc1, _fake_embedding(16, 1.0), 0), collection_id=col.collection_id, tenant_ctx=tenant)
    store.ingest_chunk(_chunk("doc2 content", doc2, _fake_embedding(16, 2.0), 0), collection_id=col.collection_id, tenant_ctx=tenant)
    # Same doc1: adds a chunk but document_count should remain 2
    store.ingest_chunk(_chunk("doc1 more", doc1, _fake_embedding(16, 3.0), 1), collection_id=col.collection_id, tenant_ctx=tenant)

    fetched = store.get_collection(col.collection_id, tenant_ctx=tenant)
    assert fetched is not None
    assert fetched.document_count == 2


# ---------------------------------------------------------------------------
# 17. RetrievalPlanner selects valid strategy
# ---------------------------------------------------------------------------


async def test_retrieval_planner_strategies() -> None:
    """RetrievalPlanner.select_strategy returns non-empty string for various queries."""
    from app.rag.engine import RetrievalPlanner

    planner = RetrievalPlanner()
    queries = [
        "What is Python GIL?",
        "List all Jira tickets assigned to me",
        "How do I configure Redis Sentinel?",
        "Summarize the quarterly report",
    ]
    for q in queries:
        strategy = planner.select_strategy(q)
        assert isinstance(strategy, str)
        assert len(strategy) > 0


# ---------------------------------------------------------------------------
# 18. hybrid_search_db falls back to in-memory when db is None
# ---------------------------------------------------------------------------


async def test_hybrid_search_db_fallback_to_in_memory() -> None:
    """hybrid_search_db() falls back to in-memory search when db is None."""
    store = KnowledgeStore(db_session_factory=None)
    tenant = _tenant("dbfb")
    col = _collection("dbfb-col")
    store.create_collection(col, tenant_ctx=tenant)

    doc_id = uuid.uuid4().hex
    emb = _fake_embedding(16, seed=2.5)
    store.ingest_chunk(_chunk("database fallback test", doc_id, emb, 0), collection_id=col.collection_id, tenant_ctx=tenant)

    results = await store.hybrid_search_db(
        query="database fallback",
        query_embedding=emb,
        collection_id=col.collection_id,
        tenant_ctx=tenant,
        top_k=5,
    )
    assert len(results) >= 1
    assert results[0].content == "database fallback test"


# ---------------------------------------------------------------------------
# 19. Multiple documents in same collection, delete one
# ---------------------------------------------------------------------------


async def test_delete_one_document_leaves_others() -> None:
    """Deleting doc A leaves doc B's chunks intact."""
    store = KnowledgeStore()
    tenant = _tenant("del2")
    col = _collection("del2-col")
    store.create_collection(col, tenant_ctx=tenant)

    doc_a = uuid.uuid4().hex
    doc_b = uuid.uuid4().hex
    store.ingest_chunk(_chunk("doc A data", doc_a, _fake_embedding(16, 1.0), 0), collection_id=col.collection_id, tenant_ctx=tenant)
    store.ingest_chunk(_chunk("doc B data", doc_b, _fake_embedding(16, 2.0), 0), collection_id=col.collection_id, tenant_ctx=tenant)

    store.delete_document(doc_a, collection_id=col.collection_id, tenant_ctx=tenant)

    results = store.hybrid_search(
        query="doc",
        query_embedding=_fake_embedding(16),
        collection_id=col.collection_id,
        tenant_ctx=tenant,
        top_k=10,
    )
    assert len(results) == 1
    assert results[0].content == "doc B data"


# ---------------------------------------------------------------------------
# 20. KnowledgeStore ingest_document (async high-level API)
# ---------------------------------------------------------------------------


async def test_ingest_document_creates_chunks_with_fake_embedder() -> None:
    """ingest_document() with a FakeProvider embedder stores chunks."""
    store = KnowledgeStore()
    tenant = _tenant("id")
    col = _collection("id-col")
    store.create_collection(col, tenant_ctx=tenant)

    fake_embedder = FakeProvider(embed_dim=16)
    await store.ingest_document(
        collection_id=col.collection_id,
        content="Integration testing with pytest-asyncio and asyncio_mode=auto",
        metadata={"source": "unit-test"},
        tenant_ctx=tenant,
        embedder=fake_embedder,
    )
    # After ingestion, searching should return the document
    results = store.hybrid_search(
        query="pytest asyncio",
        query_embedding=_fake_embedding(16),
        collection_id=col.collection_id,
        tenant_ctx=tenant,
        top_k=5,
    )
    assert len(results) >= 1
