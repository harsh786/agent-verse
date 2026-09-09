"""WS-12 [TOP FIX] — KnowledgeStore.exists_by_hash content-hash dedup lookup.

The ingestion pipeline (Stage 3) skips re-indexing a document whose content
hash already exists via ``KnowledgeStore.exists_by_hash``. Before this fix no
implementation existed, so the ``hasattr`` guard always failed and document
level dedup NEVER fired. These tests pin the reusable contract that ingestion,
RPA and OCR agents all call for cross-source dedup.

Contract:
    async def exists_by_hash(
        *, content_hash: str, tenant_id: str, collection_id: str | None = None
    ) -> bool

A hash matches when EITHER a chunk's native per-chunk ``content_hash`` OR its
``doc_content_hash`` metadata equals the given hash. Always RLS/tenant-scoped;
``collection_id`` narrows the search when supplied.
"""

from __future__ import annotations

import pytest

from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext


def _ctx(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, api_key_id="test", plan=PlanTier.FREE)


def _store_with_collection(tenant_id: str, collection_id: str) -> KnowledgeStore:
    store = KnowledgeStore()
    store.create_collection(
        KnowledgeCollection(name="C", collection_id=collection_id),
        tenant_ctx=_ctx(tenant_id),
    )
    return store


@pytest.mark.asyncio
async def test_absent_hash_returns_false() -> None:
    store = _store_with_collection("t1", "c1")
    assert (
        await store.exists_by_hash(
            content_hash="deadbeef", tenant_id="t1", collection_id="c1"
        )
        is False
    )


@pytest.mark.asyncio
async def test_matches_doc_content_hash_metadata() -> None:
    store = _store_with_collection("t1", "c1")
    store.ingest_chunk(
        Chunk(
            document_id="d1",
            content="hello world",
            embedding=[0.1, 0.2, 0.3],
            chunk_index=0,
            metadata={"doc_content_hash": "abc123"},
        ),
        collection_id="c1",
        tenant_ctx=_ctx("t1"),
    )
    assert (
        await store.exists_by_hash(
            content_hash="abc123", tenant_id="t1", collection_id="c1"
        )
        is True
    )


@pytest.mark.asyncio
async def test_matches_native_chunk_content_hash_metadata() -> None:
    store = _store_with_collection("t1", "c1")
    store.ingest_chunk(
        Chunk(
            document_id="d1",
            content="hello world",
            embedding=[0.1, 0.2, 0.3],
            chunk_index=0,
            metadata={"content_hash": "chunkhash99"},
        ),
        collection_id="c1",
        tenant_ctx=_ctx("t1"),
    )
    assert (
        await store.exists_by_hash(
            content_hash="chunkhash99", tenant_id="t1", collection_id="c1"
        )
        is True
    )


@pytest.mark.asyncio
async def test_tenant_isolation_rls_scoped() -> None:
    store = _store_with_collection("t1", "c1")
    store.create_collection(
        KnowledgeCollection(name="C2", collection_id="c1"),
        tenant_ctx=_ctx("t2"),
    )
    store.ingest_chunk(
        Chunk(
            document_id="d1",
            content="secret",
            embedding=[0.1],
            chunk_index=0,
            metadata={"doc_content_hash": "shared_hash"},
        ),
        collection_id="c1",
        tenant_ctx=_ctx("t1"),
    )
    # Tenant t2 must NOT see t1's content hash.
    assert (
        await store.exists_by_hash(content_hash="shared_hash", tenant_id="t2") is False
    )
    assert (
        await store.exists_by_hash(content_hash="shared_hash", tenant_id="t1") is True
    )


@pytest.mark.asyncio
async def test_collection_scoping_optional() -> None:
    store = _store_with_collection("t1", "c1")
    store.create_collection(
        KnowledgeCollection(name="Other", collection_id="c2"),
        tenant_ctx=_ctx("t1"),
    )
    store.ingest_chunk(
        Chunk(
            document_id="d1",
            content="x",
            embedding=[0.1],
            chunk_index=0,
            metadata={"doc_content_hash": "h_in_c1"},
        ),
        collection_id="c1",
        tenant_ctx=_ctx("t1"),
    )
    # Present in c1, absent in c2, present tenant-wide (collection_id=None).
    assert await store.exists_by_hash(
        content_hash="h_in_c1", tenant_id="t1", collection_id="c1"
    )
    assert not await store.exists_by_hash(
        content_hash="h_in_c1", tenant_id="t1", collection_id="c2"
    )
    assert await store.exists_by_hash(content_hash="h_in_c1", tenant_id="t1")


@pytest.mark.asyncio
async def test_empty_hash_is_never_a_match() -> None:
    store = _store_with_collection("t1", "c1")
    store.ingest_chunk(
        Chunk(
            document_id="d1",
            content="x",
            embedding=[0.1],
            chunk_index=0,
            metadata={"doc_content_hash": ""},
        ),
        collection_id="c1",
        tenant_ctx=_ctx("t1"),
    )
    assert await store.exists_by_hash(content_hash="", tenant_id="t1") is False
