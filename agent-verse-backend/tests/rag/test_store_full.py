"""Full coverage for KnowledgeStore — covers all branches and code paths."""
from __future__ import annotations

import pytest

from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import HybridSearchResult, KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="rag-full-t1", plan=PlanTier.PROFESSIONAL, api_key_id="rf1")


def test_create_and_get_collection() -> None:
    """create_collection returns the ID; get_collection retrieves the same object."""
    store = KnowledgeStore()
    col = KnowledgeCollection(name="test-col", collection_id="c1")
    cid = store.create_collection(col, tenant_ctx=T)
    assert cid == "c1"
    retrieved = store.get_collection("c1", tenant_ctx=T)
    assert retrieved is not None
    assert retrieved.name == "test-col"


def test_list_collections_tenant_isolated() -> None:
    """list_collections returns only collections belonging to the requesting tenant."""
    store = KnowledgeStore()
    T_A = TenantContext(tenant_id="rag-iso-a", plan=PlanTier.FREE, api_key_id="ra")
    T_B = TenantContext(tenant_id="rag-iso-b", plan=PlanTier.FREE, api_key_id="rb")

    col_a = KnowledgeCollection(name="A col", collection_id="ca1")
    col_b = KnowledgeCollection(name="B col", collection_id="cb1")
    store.create_collection(col_a, tenant_ctx=T_A)
    store.create_collection(col_b, tenant_ctx=T_B)

    cols_a = store.list_collections(tenant_ctx=T_A)
    cols_b = store.list_collections(tenant_ctx=T_B)

    assert len(cols_a) == 1
    assert cols_a[0].collection_id == "ca1"
    assert len(cols_b) == 1
    assert cols_b[0].collection_id == "cb1"


def test_ingest_chunk_updates_doc_count() -> None:
    """ingest_chunk tracks unique document IDs in document_count."""
    store = KnowledgeStore()
    col = KnowledgeCollection(name="doc-count", collection_id="dc1")
    store.create_collection(col, tenant_ctx=T)

    for i in range(3):
        chunk = Chunk(
            document_id=f"doc-{i}",
            content=f"content {i}",
            embedding=[0.1] * 768,
            chunk_index=0,
            chunk_id=f"ck{i}",
        )
        store.ingest_chunk(chunk, collection_id="dc1", tenant_ctx=T)

    col_retrieved = store.get_collection("dc1", tenant_ctx=T)
    assert col_retrieved is not None
    assert col_retrieved.document_count == 3


def test_ingest_chunk_unknown_collection_raises() -> None:
    """ingest_chunk raises KeyError for an unknown collection."""
    store = KnowledgeStore()
    chunk = Chunk(document_id="d1", content="x", embedding=[0.1] * 768, chunk_index=0)
    with pytest.raises(KeyError):
        store.ingest_chunk(chunk, collection_id="nonexistent", tenant_ctx=T)


def test_hybrid_search_empty_collection() -> None:
    """hybrid_search returns [] for a collection with no chunks."""
    store = KnowledgeStore()
    col = KnowledgeCollection(name="empty", collection_id="empty1")
    store.create_collection(col, tenant_ctx=T)
    results = store.hybrid_search("query", [0.1] * 768, "empty1", T, top_k=5)
    assert results == []


def test_hybrid_search_nonexistent_collection() -> None:
    """hybrid_search returns [] for a collection that doesn't exist."""
    store = KnowledgeStore()
    results = store.hybrid_search("query", [0.1] * 768, "doesnotexist", T, top_k=5)
    assert results == []


def test_hybrid_search_ranks_by_score() -> None:
    """hybrid_search returns results sorted by descending hybrid score."""
    store = KnowledgeStore()
    col = KnowledgeCollection(name="ranking", collection_id="rank1")
    store.create_collection(col, tenant_ctx=T)

    chunks_data = [
        ("machine learning algorithms", [1.0, 0.0, 0.0] + [0.0] * 765),
        ("cooking recipes pasta", [0.0, 1.0, 0.0] + [0.0] * 765),
        ("machine learning neural networks", [0.9, 0.1, 0.0] + [0.0] * 765),
    ]
    for i, (content, emb) in enumerate(chunks_data):
        chunk = Chunk(
            document_id=f"dr{i}",
            content=content,
            embedding=emb,
            chunk_index=0,
            chunk_id=f"r{i}",
        )
        store.ingest_chunk(chunk, collection_id="rank1", tenant_ctx=T)

    query_emb = [1.0, 0.0, 0.0] + [0.0] * 765
    results = store.hybrid_search("machine learning", query_emb, "rank1", T, top_k=3)
    assert len(results) == 3
    assert isinstance(results[0], HybridSearchResult)
    # Results must be sorted by score (descending)
    assert results[0].score >= results[1].score >= results[2].score


async def test_hybrid_search_db_fallback() -> None:
    """hybrid_search_db falls back to in-memory search when no DB is configured."""
    store = KnowledgeStore()
    col = KnowledgeCollection(name="db-fallback", collection_id="df1")
    store.create_collection(col, tenant_ctx=T)
    chunk = Chunk(
        document_id="d1",
        content="test content about databases",
        embedding=[0.1] * 768,
        chunk_index=0,
    )
    store.ingest_chunk(chunk, collection_id="df1", tenant_ctx=T)

    results = await store.hybrid_search_db("test", [0.1] * 768, "df1", T, top_k=5)
    assert isinstance(results, list)
    assert len(results) == 1
