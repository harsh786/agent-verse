"""Tests for KnowledgeStore DB persistence — dual-write hybrid pattern.

Verifies:
- sync_from_db() returns 0 with no DB factory (no-op)
- DB failures never break in-memory create_collection() / ingest_chunk()
- hybrid_search_db() falls back to in-memory search when DB not available
- hybrid_search_db() falls back gracefully when DB query fails
"""

from __future__ import annotations

import asyncio

import pytest

from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="rag-db-t1", plan=PlanTier.ENTERPRISE, api_key_id="rk1")


# ── Helper: a session factory whose __aenter__ always raises ──────────────────


def _bad_factory() -> object:
    """Return an async context manager that always raises on __aenter__."""

    class _FailCtx:
        async def __aenter__(self) -> None:
            raise RuntimeError("DB down")

        async def __aexit__(self, *_: object) -> bool:
            return False

    return _FailCtx()


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_knowledge_store_sync_from_db_noop() -> None:
    """sync_from_db() returns 0 immediately when no DB factory is configured."""
    store = KnowledgeStore()
    count = await store.sync_from_db()
    assert count == 0


async def test_knowledge_store_create_collection_with_db_factory_error() -> None:
    """DB failure must not prevent in-memory collection creation."""
    store = KnowledgeStore(db_session_factory=_bad_factory)
    col = KnowledgeCollection(name="test-col", collection_id="col1")
    cid = store.create_collection(col, tenant_ctx=T)

    # Let fire-and-forget DB task run and fail gracefully
    await asyncio.sleep(0)

    assert cid == "col1"
    assert store.get_collection("col1", tenant_ctx=T) is not None


async def test_knowledge_store_ingest_with_db_factory_error() -> None:
    """DB failure must not prevent in-memory ingest or search."""
    store = KnowledgeStore(db_session_factory=_bad_factory)
    col = KnowledgeCollection(name="test", collection_id="col1")
    store.create_collection(col, tenant_ctx=T)

    chunk = Chunk(
        document_id="d1",
        content="hello world vector search",
        embedding=[0.1] * 768,
        chunk_index=0,
        chunk_id="c1",
    )
    store.ingest_chunk(chunk, collection_id="col1", tenant_ctx=T)

    # Let all fire-and-forget tasks run and fail gracefully
    await asyncio.sleep(0)

    # In-memory search still works after DB failures
    results = store.hybrid_search("hello", [0.1] * 768, "col1", T, top_k=5)
    assert len(results) >= 1
    assert results[0].chunk_id == "c1"


async def test_knowledge_store_hybrid_search_db_no_db_falls_back() -> None:
    """hybrid_search_db() without a DB factory falls back to in-memory search."""
    store = KnowledgeStore()  # no db_session_factory
    col = KnowledgeCollection(name="docs", collection_id="col2")
    store.create_collection(col, tenant_ctx=T)

    vec = [1.0] + [0.0] * 767
    chunk = Chunk(
        document_id="d2",
        content="memory leak detection in C++",
        embedding=vec,
        chunk_index=0,
        chunk_id="c2",
    )
    store.ingest_chunk(chunk, collection_id="col2", tenant_ctx=T)

    results = await store.hybrid_search_db("memory leak", vec, "col2", T, top_k=5)
    assert len(results) >= 1
    assert "memory" in results[0].content.lower()


async def test_knowledge_store_hybrid_search_db_fallback_on_error() -> None:
    """hybrid_search_db() falls back to in-memory when DB query fails."""
    store = KnowledgeStore(db_session_factory=_bad_factory)
    col = KnowledgeCollection(name="kb", collection_id="col3")
    store.create_collection(col, tenant_ctx=T)

    # Let the create_collection DB task run and fail
    await asyncio.sleep(0)

    vec = [0.5] * 768
    chunk = Chunk(
        document_id="d3",
        content="python async programming",
        embedding=vec,
        chunk_index=0,
        chunk_id="c3",
    )
    store.ingest_chunk(chunk, collection_id="col3", tenant_ctx=T)

    # Let the ingest DB task run and fail
    await asyncio.sleep(0)

    # hybrid_search_db should fall back to in-memory (DB fails)
    results = await store.hybrid_search_db("async", vec, "col3", T, top_k=5)
    assert len(results) >= 1
    assert results[0].chunk_id == "c3"


async def test_knowledge_store_no_db_ingest_is_synchronous() -> None:
    """Without a DB factory, ingest_chunk() is purely in-memory — no tasks."""
    store = KnowledgeStore()
    col = KnowledgeCollection(name="sync", collection_id="col4")
    store.create_collection(col, tenant_ctx=T)

    chunk = Chunk(
        document_id="d4",
        content="synchronous ingest test",
        embedding=[0.2] * 768,
        chunk_index=0,
        chunk_id="c4",
    )
    store.ingest_chunk(chunk, collection_id="col4", tenant_ctx=T)

    results = store.hybrid_search("ingest", [0.2] * 768, "col4", T, top_k=5)
    assert len(results) == 1
    assert results[0].chunk_id == "c4"
