"""Tests for Phase 5 — Agentic RAG + memory.

Tests cover:
- Document and Chunk models
- KnowledgeStore: in-memory CRUD + hybrid search scoring
- SemanticCache: hit/miss based on cosine similarity
- ExecutionMemory: store and recall winning plans
"""

from __future__ import annotations

import math

import pytest

from app.rag.models import Chunk, Document, KnowledgeCollection
from app.rag.store import HybridSearchResult, KnowledgeStore
from app.rag.semantic_cache import SemanticCache
from app.memory.execution import ExecutionMemory
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-test", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_CTX_B = TenantContext(tenant_id="tid-b", plan=PlanTier.STARTER, api_key_id="kid-2")


# ── Models ────────────────────────────────────────────────────────────────────

def test_document_has_required_fields() -> None:
    doc = Document(
        collection_id="col-1",
        source="git://github.com/org/repo",
        content="Hello world",
        content_hash="abc123",
    )
    assert doc.collection_id == "col-1"
    assert doc.content_hash == "abc123"


def test_chunk_embedding_dimension() -> None:
    vec = [0.1] * 1024
    chunk = Chunk(document_id="doc-1", content="test", embedding=vec, chunk_index=0)
    assert len(chunk.embedding) == 1024


def test_knowledge_collection_model() -> None:
    col = KnowledgeCollection(name="API docs", description="REST API documentation")
    assert col.name == "API docs"
    assert col.document_count == 0


# ── KnowledgeStore (in-memory) ────────────────────────────────────────────────

def _store() -> KnowledgeStore:
    return KnowledgeStore()


def test_store_add_and_get_collection() -> None:
    store = _store()
    col_id = store.create_collection(KnowledgeCollection(name="docs"), tenant_ctx=_CTX)
    col = store.get_collection(col_id, tenant_ctx=_CTX)
    assert col is not None
    assert col.name == "docs"


def test_store_collection_tenant_isolation() -> None:
    store = _store()
    col_id = store.create_collection(KnowledgeCollection(name="private"), tenant_ctx=_CTX)
    col = store.get_collection(col_id, tenant_ctx=_CTX_B)
    assert col is None


def test_store_ingest_and_search() -> None:
    store = _store()
    col_id = store.create_collection(KnowledgeCollection(name="code"), tenant_ctx=_CTX)

    # Fake embedding: first dim is 1.0 for "checkout" documents
    checkout_embedding = [1.0] + [0.0] * 1023
    login_embedding = [0.0, 1.0] + [0.0] * 1022

    store.ingest_chunk(
        Chunk(
            document_id="doc-1",
            content="The checkout service handles payment processing",
            embedding=checkout_embedding,
            chunk_index=0,
        ),
        collection_id=col_id,
        tenant_ctx=_CTX,
    )
    store.ingest_chunk(
        Chunk(
            document_id="doc-2",
            content="Login authentication via JWT tokens",
            embedding=login_embedding,
            chunk_index=0,
        ),
        collection_id=col_id,
        tenant_ctx=_CTX,
    )

    # Query embedding similar to checkout
    query_vec = [0.99] + [0.0] * 1023
    results = store.hybrid_search(
        query="payment processing",
        query_embedding=query_vec,
        collection_id=col_id,
        tenant_ctx=_CTX,
        top_k=2,
    )
    assert len(results) >= 1
    # Checkout doc should rank first (highest cosine similarity)
    assert "checkout" in results[0].content.lower()


def test_store_search_returns_hybrid_score() -> None:
    store = _store()
    col_id = store.create_collection(KnowledgeCollection(name="docs"), tenant_ctx=_CTX)
    vec = [1.0] + [0.0] * 1023
    store.ingest_chunk(
        Chunk(document_id="d1", content="memory leak detection", embedding=vec, chunk_index=0),
        collection_id=col_id,
        tenant_ctx=_CTX,
    )
    results = store.hybrid_search(
        query="memory leak",
        query_embedding=vec,
        collection_id=col_id,
        tenant_ctx=_CTX,
    )
    assert results[0].score > 0.0
    assert 0.0 <= results[0].score <= 1.0


# ── SemanticCache ─────────────────────────────────────────────────────────────

def _unit_vec(dim: int = 8, ones: int = 1) -> list[float]:
    v = [0.0] * dim
    for i in range(ones):
        v[i] = 1.0
    mag = math.sqrt(sum(x * x for x in v))
    return [x / mag for x in v]


def test_semantic_cache_miss_returns_none() -> None:
    cache = SemanticCache(threshold=0.92)
    result = cache.lookup(query_embedding=_unit_vec(), tenant_ctx=_CTX)
    assert result is None


def test_semantic_cache_hit_above_threshold() -> None:
    cache = SemanticCache(threshold=0.92)
    vec = _unit_vec()
    cache.store(query_embedding=vec, response="cached answer", tenant_ctx=_CTX)
    # Identical vector should hit
    result = cache.lookup(query_embedding=vec, tenant_ctx=_CTX)
    assert result == "cached answer"


def test_semantic_cache_miss_below_threshold() -> None:
    cache = SemanticCache(threshold=0.92)
    vec_a = _unit_vec(8, 1)  # [1, 0, 0, 0, 0, 0, 0, 0]
    vec_b = [0.0] * 7 + [1.0]  # very different direction
    cache.store(query_embedding=vec_a, response="answer a", tenant_ctx=_CTX)
    result = cache.lookup(query_embedding=vec_b, tenant_ctx=_CTX)
    assert result is None


def test_semantic_cache_tenant_isolation() -> None:
    cache = SemanticCache(threshold=0.92)
    vec = _unit_vec()
    cache.store(query_embedding=vec, response="secret answer", tenant_ctx=_CTX)
    # Different tenant should not see the cached value
    result = cache.lookup(query_embedding=vec, tenant_ctx=_CTX_B)
    assert result is None


# ── ExecutionMemory ───────────────────────────────────────────────────────────

def test_execution_memory_stores_and_recalls() -> None:
    mem = ExecutionMemory()
    mem.record(goal="Fix memory leak", plan=["Step 1: profile", "Step 2: patch"], tenant_ctx=_CTX)
    results = mem.recall(goal_hint="memory leak", tenant_ctx=_CTX, top_k=5)
    assert len(results) >= 1
    assert results[0]["goal"] == "Fix memory leak"


def test_execution_memory_tenant_isolation() -> None:
    mem = ExecutionMemory()
    mem.record(goal="secret goal", plan=["secret step"], tenant_ctx=_CTX)
    results = mem.recall(goal_hint="secret", tenant_ctx=_CTX_B, top_k=5)
    assert len(results) == 0


def test_execution_memory_records_failed_approaches() -> None:
    mem = ExecutionMemory()
    mem.record_failure(
        goal="deploy app",
        failed_step="push to prod",
        error="permission denied",
        tenant_ctx=_CTX,
    )
    failures = mem.recall_failures(goal_hint="deploy", tenant_ctx=_CTX)
    assert len(failures) >= 1
    assert failures[0]["error"] == "permission denied"
