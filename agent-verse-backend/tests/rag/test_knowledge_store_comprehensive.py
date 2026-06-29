"""Comprehensive tests for app/rag/store.py — targeting 90%+ coverage."""
from __future__ import annotations

import asyncio
import math
import pytest

from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import (
    HybridSearchResult,
    KnowledgeStore,
    _cosine_similarity,
    _trigram_score,
)
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="ks-t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
_CTX2 = TenantContext(tenant_id="ks-t2", plan=PlanTier.STARTER, api_key_id="k2")


class _FailingDB:
    def __call__(self):
        return self
    async def __aenter__(self):
        raise RuntimeError("DB failure")
    async def __aexit__(self, *args):
        pass


# ── Utility functions ───────────────────────────────────────────────────────


class TestCosineSimularity:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert _cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine_similarity(a, b) == pytest.approx(0.0)

    def test_zero_vector_returns_zero(self):
        """Covers line 52: mag == 0 returns 0.0."""
        zero = [0.0, 0.0, 0.0]
        other = [1.0, 0.0, 0.0]
        assert _cosine_similarity(zero, other) == 0.0
        assert _cosine_similarity(other, zero) == 0.0
        assert _cosine_similarity(zero, zero) == 0.0

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert _cosine_similarity(a, b) == pytest.approx(-1.0)


class TestTrigramScore:
    def test_identical_text(self):
        assert _trigram_score("hello world", "hello world") == pytest.approx(1.0)

    def test_short_string_no_trigrams(self):
        """Covers line 66: short string returns 0.0."""
        assert _trigram_score("ab", "hello world") == 0.0

    def test_short_query_returns_zero(self):
        assert _trigram_score("hi", "hello world") == 0.0

    def test_no_overlap_returns_zero(self):
        assert _trigram_score("aaabbb", "zzzyyyy") == 0.0

    def test_partial_overlap(self):
        score = _trigram_score("checkout payment", "the checkout service")
        assert 0.0 < score <= 1.0


# ── KnowledgeStore ──────────────────────────────────────────────────────────


class TestKnowledgeStoreBasic:
    def _store(self) -> KnowledgeStore:
        return KnowledgeStore()

    def _make_col(self, name: str = "test") -> KnowledgeCollection:
        return KnowledgeCollection(name=name)

    def test_create_and_get_collection(self):
        store = self._store()
        col_id = store.create_collection(self._make_col("docs"), tenant_ctx=_CTX)
        col = store.get_collection(col_id, tenant_ctx=_CTX)
        assert col is not None
        assert col.name == "docs"

    def test_get_collection_not_found(self):
        store = self._store()
        col = store.get_collection("nonexistent", tenant_ctx=_CTX)
        assert col is None

    def test_list_collections_empty(self):
        store = self._store()
        assert store.list_collections(tenant_ctx=_CTX) == []

    def test_list_collections_tenant_isolation(self):
        store = self._store()
        store.create_collection(self._make_col("t1-col"), tenant_ctx=_CTX)
        cols = store.list_collections(tenant_ctx=_CTX2)
        assert cols == []

    def test_list_collections_multiple(self):
        store = self._store()
        store.create_collection(self._make_col("col-a"), tenant_ctx=_CTX)
        store.create_collection(self._make_col("col-b"), tenant_ctx=_CTX)
        cols = store.list_collections(tenant_ctx=_CTX)
        assert len(cols) == 2

    def test_ingest_chunk_missing_collection_raises(self):
        store = self._store()
        chunk = Chunk(document_id="d1", content="text", embedding=[0.1], chunk_index=0)
        with pytest.raises(KeyError):
            store.ingest_chunk(chunk, collection_id="no-such-col", tenant_ctx=_CTX)

    def test_ingest_chunk_updates_document_count(self):
        store = self._store()
        col_id = store.create_collection(self._make_col(), tenant_ctx=_CTX)
        c1 = Chunk(document_id="doc-1", content="A", embedding=[1.0], chunk_index=0)
        c2 = Chunk(document_id="doc-2", content="B", embedding=[1.0], chunk_index=0)
        store.ingest_chunk(c1, collection_id=col_id, tenant_ctx=_CTX)
        store.ingest_chunk(c2, collection_id=col_id, tenant_ctx=_CTX)
        col = store.get_collection(col_id, tenant_ctx=_CTX)
        assert col.document_count == 2

    def test_ingest_chunk_same_doc_id(self):
        """Multiple chunks from same doc_id: doc_count stays 1."""
        store = self._store()
        col_id = store.create_collection(self._make_col(), tenant_ctx=_CTX)
        for i in range(3):
            c = Chunk(document_id="same-doc", content=f"chunk {i}", embedding=[1.0], chunk_index=i)
            store.ingest_chunk(c, collection_id=col_id, tenant_ctx=_CTX)
        col = store.get_collection(col_id, tenant_ctx=_CTX)
        assert col.document_count == 1


class TestKnowledgeStoreSearch:
    def _populated_store(self) -> tuple[KnowledgeStore, str]:
        store = KnowledgeStore()
        col_id = store.create_collection(KnowledgeCollection(name="search-test"), tenant_ctx=_CTX)
        checkout_vec = [1.0] + [0.0] * 7
        login_vec = [0.0, 1.0] + [0.0] * 6
        store.ingest_chunk(
            Chunk(document_id="d1", content="checkout payment processing", embedding=checkout_vec, chunk_index=0),
            collection_id=col_id, tenant_ctx=_CTX
        )
        store.ingest_chunk(
            Chunk(document_id="d2", content="login authentication", embedding=login_vec, chunk_index=0),
            collection_id=col_id, tenant_ctx=_CTX
        )
        return store, col_id

    def test_hybrid_search_returns_results(self):
        store, col_id = self._populated_store()
        query_vec = [0.99] + [0.0] * 7
        results = store.hybrid_search("checkout payment", query_vec, col_id, _CTX, top_k=2)
        assert len(results) >= 1
        assert "checkout" in results[0].content.lower()

    def test_hybrid_search_empty_collection_returns_empty(self):
        store = KnowledgeStore()
        results = store.hybrid_search("query", [0.5], "nonexistent-col", _CTX)
        assert results == []

    def test_hybrid_search_respects_top_k(self):
        store, col_id = self._populated_store()
        results = store.hybrid_search("test", [1.0] + [0.0] * 7, col_id, _CTX, top_k=1)
        assert len(results) == 1

    def test_hybrid_search_result_scores_between_0_and_1(self):
        store, col_id = self._populated_store()
        results = store.hybrid_search("checkout", [1.0] + [0.0] * 7, col_id, _CTX)
        for r in results:
            assert isinstance(r, HybridSearchResult)
            assert 0.0 <= r.score <= 1.0

    @pytest.mark.asyncio
    async def test_hybrid_search_db_falls_back_to_memory_when_no_db(self):
        """hybrid_search_db without DB falls back to in-memory search."""
        store = KnowledgeStore()
        col_id = store.create_collection(KnowledgeCollection(name="fallback"), tenant_ctx=_CTX)
        v = [1.0] + [0.0] * 7
        store.ingest_chunk(
            Chunk(document_id="d1", content="fallback content", embedding=v, chunk_index=0),
            collection_id=col_id, tenant_ctx=_CTX
        )
        results = await store.hybrid_search_db("fallback content", v, col_id, _CTX, top_k=3)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_hybrid_search_db_falls_back_to_memory_on_db_error(self):
        """When DB raises, hybrid_search_db must fall back to in-memory."""
        store = KnowledgeStore(db_session_factory=_FailingDB())
        col_id = store.create_collection(KnowledgeCollection(name="db-err"), tenant_ctx=_CTX)
        v = [1.0] + [0.0] * 7
        store.ingest_chunk(
            Chunk(document_id="d1", content="db error test", embedding=v, chunk_index=0),
            collection_id=col_id, tenant_ctx=_CTX
        )
        results = await store.hybrid_search_db("db error test", v, col_id, _CTX)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_hybrid_search_db_empty_embedding_falls_back(self):
        """Empty query_embedding triggers fallback to in-memory."""
        store = KnowledgeStore()
        col_id = store.create_collection(KnowledgeCollection(name="empty-vec"), tenant_ctx=_CTX)
        results = await store.hybrid_search_db("test", [], col_id, _CTX)
        assert isinstance(results, list)


class TestKnowledgeStoreIngestDocument:
    """Tests for ingest_document (lines 348-428)."""

    @pytest.mark.asyncio
    async def test_ingest_document_without_embedder(self):
        store = KnowledgeStore()
        col_id = store.create_collection(KnowledgeCollection(name="doc-col"), tenant_ctx=_CTX)
        chunk_id = await store.ingest_document(
            collection_id=col_id,
            content="Python is a versatile programming language.",
            tenant_ctx=_CTX,
            embedder=None,
        )
        assert chunk_id is not None
        assert len(chunk_id) == 32  # uuid hex

    @pytest.mark.asyncio
    async def test_ingest_document_with_embedder(self):
        from app.providers.fake import FakeProvider
        store = KnowledgeStore()
        col_id = store.create_collection(KnowledgeCollection(name="embed-col"), tenant_ctx=_CTX)
        embedder = FakeProvider()
        chunk_id = await store.ingest_document(
            collection_id=col_id,
            content="FastAPI is a high-performance Python web framework.",
            tenant_ctx=_CTX,
            embedder=embedder,
        )
        assert chunk_id is not None
        # Chunk with embedding should be in memory
        col = store.get_collection(col_id, tenant_ctx=_CTX)
        assert col.document_count >= 1

    @pytest.mark.asyncio
    async def test_ingest_document_with_citation_metadata(self):
        store = KnowledgeStore()
        col_id = store.create_collection(KnowledgeCollection(name="cite-col"), tenant_ctx=_CTX)
        chunk_id = await store.ingest_document(
            collection_id=col_id,
            content="Chapter 3: Advanced Features",
            tenant_ctx=_CTX,
            source_url="https://docs.example.com/chapter-3",
            source_type="documentation",
            source_doc_id="doc-handbook",
            page_number=42,
        )
        assert chunk_id is not None

    @pytest.mark.asyncio
    async def test_ingest_document_missing_collection_no_error(self):
        """Ingesting to non-existent collection silently skips in-memory storage."""
        store = KnowledgeStore()
        chunk_id = await store.ingest_document(
            collection_id="nonexistent-col",
            content="Orphaned content",
            tenant_ctx=_CTX,
        )
        # Should still return a chunk_id without raising
        assert chunk_id is not None

    @pytest.mark.asyncio
    async def test_ingest_document_with_failing_embedder(self):
        """Failing embedder must not raise — embedding is best-effort."""
        class _BadEmbedder:
            async def embed(self, req):
                raise RuntimeError("Embedder down")

        store = KnowledgeStore()
        col_id = store.create_collection(KnowledgeCollection(name="bad-emb"), tenant_ctx=_CTX)
        chunk_id = await store.ingest_document(
            collection_id=col_id,
            content="Test content",
            tenant_ctx=_CTX,
            embedder=_BadEmbedder(),
        )
        assert chunk_id is not None


class TestKnowledgeStoreSyncFromDB:
    """Tests for sync_from_db (lines 502-590)."""

    @pytest.mark.asyncio
    async def test_sync_from_db_no_db_returns_zero(self):
        """sync_from_db without DB returns 0 immediately."""
        store = KnowledgeStore()
        loaded = await store.sync_from_db()
        assert loaded == 0

    @pytest.mark.asyncio
    async def test_sync_from_db_failing_db_returns_zero(self):
        """DB failure in sync_from_db returns 0 (graceful degradation)."""
        store = KnowledgeStore(db_session_factory=_FailingDB())
        loaded = await store.sync_from_db()
        assert loaded == 0


class TestHybridSearchResult:
    def test_fields(self):
        result = HybridSearchResult(
            chunk_id="c1",
            content="test content",
            score=0.87,
            vector_score=0.9,
            trigram_score=0.7,
            source_url="https://example.com",
            source_doc_id="doc-123",
            page_number=5,
            metadata={"key": "value"},
        )
        assert result.chunk_id == "c1"
        assert result.score == pytest.approx(0.87)
        assert result.source_url == "https://example.com"
        assert result.page_number == 5

    def test_defaults(self):
        result = HybridSearchResult(
            chunk_id="c1", content="text", score=0.5, vector_score=0.6, trigram_score=0.4
        )
        assert result.source_url == ""
        assert result.source_doc_id == ""
        assert result.page_number is None
        assert result.metadata == {}
