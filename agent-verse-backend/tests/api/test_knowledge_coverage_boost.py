"""Targeted tests for previously-uncovered branches in app/api/knowledge.py.

Focuses on: RAG chat, collection stats, bulk analytics, the document browser
(list/delete/reingest/sync), new ingestion sources (email/notion/gdrive), the
RPA URL ingestion edge cases, and extra branches of the IngestionOrchestrator
document-ingest endpoint.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge import router as knowledge_router
from app.ingestion.orchestrator import EmptyIndexedContentError
from app.rag.store import EmbeddingProviderUnavailableError, KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-covboost", plan=PlanTier.PROFESSIONAL, api_key_id="kid-cb")
_VALID_KEY = "av_test_knowledge_covboost"


def _make_app(
    knowledge_store: Any | None = None,
    embedder: Any = None,
    retrieval_gateway: Any = None,
) -> FastAPI:
    from app.rag.semantic_cache import SemanticCache

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(knowledge_router)
    app.state.knowledge_store = knowledge_store if knowledge_store is not None else KnowledgeStore()
    app.state.semantic_cache = SemanticCache()
    if embedder is not None:
        app.state.embedder = embedder
    if retrieval_gateway is not None:
        app.state.retrieval_gateway = retrieval_gateway
    return app


def _client(**kwargs: Any) -> TestClient:
    return TestClient(_make_app(**kwargs), raise_server_exceptions=False)


def _make_embedder() -> Any:
    from app.providers.base import EmbedResponse

    emb = AsyncMock()
    emb.embed.return_value = EmbedResponse(embeddings=[[0.1] * 768], model="voyage")
    return emb


def _auth() -> dict[str, str]:
    return {"X-API-Key": _VALID_KEY}


# ---------------------------------------------------------------------------
# RAG chat (/knowledge/chat)
# ---------------------------------------------------------------------------


def test_rag_chat_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/knowledge/chat", json={"question": "hi"})
    assert resp.status_code == 401


def test_rag_chat_empty_question_returns_400() -> None:
    client = _client()
    resp = client.post("/knowledge/chat", json={"question": "   "}, headers=_auth())
    assert resp.status_code == 400


def test_rag_chat_no_collections_returns_404() -> None:
    client = _client()
    resp = client.post("/knowledge/chat", json={"question": "what is up?"}, headers=_auth())
    assert resp.status_code == 404


def test_rag_chat_retrieval_unavailable_returns_503() -> None:
    """No retrieval_gateway configured → federated_search fails closed."""
    client = _client()
    resp = client.post(
        "/knowledge/chat",
        json={"question": "hello", "collection_ids": ["col-1"]},
        headers=_auth(),
    )
    assert resp.status_code == 503


def _fake_citation_result(idx: int) -> dict[str, Any]:
    return {
        "citation_id": f"c{idx}",
        "chunk_id": f"chunk-{idx}",
        "content": f"Some retrieved content {idx}",
        "score": 0.9,
        "source": "doc.md",
        "collection_id": "col-1",
        "collection_ids": ["col-1"],
        "sources": ["doc.md"],
        "citation_refs": [],
        "retrieval_legs": [],
        "strategy_trace": [],
        "resolved_strategy_id": "hybrid",
        "metadata": {"source_url": "https://example.com", "page_number": 1},
    }


def test_rag_chat_grounded_success() -> None:
    from app.rag.contracts import RAGStrategyTrace

    client = _client(retrieval_gateway=object())

    trace = RAGStrategyTrace(strategy="hybrid", action="verify", status="ok", detail={})
    verified = SimpleNamespace(grounded=True, answer="The final grounded answer.", strategy_trace=[trace])

    fake_retriever = MagicMock()
    fake_retriever.synthesize = AsyncMock(return_value="draft answer")
    fake_retriever.verify_result = AsyncMock(return_value=verified)

    with (
        patch(
            "app.knowledge.federated_search.federated_search",
            new=AsyncMock(return_value=[_fake_citation_result(1)]),
        ),
        patch("app.api.knowledge.RAGRetriever", return_value=fake_retriever),
    ):
        resp = client.post(
            "/knowledge/chat",
            json={"question": "hello", "collection_ids": ["col-1"]},
            headers=_auth(),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "The final grounded answer."
    assert body["grounded"] is True
    assert body["chunks_retrieved"] == 1
    assert body["citations"][0]["chunk_id"] == "chunk-1"


def test_rag_chat_ungrounded_returns_422() -> None:
    from app.rag.contracts import RAGStrategyTrace

    client = _client(retrieval_gateway=object())

    trace = RAGStrategyTrace(
        strategy="hybrid", action="verify", status="failed", detail={"reason": "unsupported claim"}
    )
    verified = SimpleNamespace(grounded=False, answer="", strategy_trace=[trace])

    fake_retriever = MagicMock()
    fake_retriever.synthesize = AsyncMock(return_value="draft answer")
    fake_retriever.verify_result = AsyncMock(return_value=verified)

    with (
        patch(
            "app.knowledge.federated_search.federated_search",
            new=AsyncMock(return_value=[_fake_citation_result(1)]),
        ),
        patch("app.api.knowledge.RAGRetriever", return_value=fake_retriever),
    ):
        resp = client.post(
            "/knowledge/chat",
            json={"question": "hello", "collection_ids": ["col-1"]},
            headers=_auth(),
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "unsupported claim"


def test_rag_chat_no_citations_returns_404() -> None:
    client = _client(retrieval_gateway=object())
    with patch(
        "app.knowledge.federated_search.federated_search",
        new=AsyncMock(return_value=[]),
    ):
        resp = client.post(
            "/knowledge/chat",
            json={"question": "hello", "collection_ids": ["col-1"]},
            headers=_auth(),
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Collection stats (/knowledge/collections/{id}/stats)
# ---------------------------------------------------------------------------


def test_collection_stats_not_found() -> None:
    client = _client()
    resp = client.get("/knowledge/collections/nonexistent/stats", headers=_auth())
    assert resp.status_code == 404


def test_collection_stats_success() -> None:
    embedder = _make_embedder()
    client = _client(embedder=embedder)
    created = client.post(
        "/knowledge/collections", json={"name": "Stats Coll"}, headers=_auth()
    ).json()
    coll_id = created["collection_id"]

    ingest_resp = client.post(
        "/knowledge/ingest",
        json={"collection_id": coll_id, "source_type": "text", "content": "Some content here"},
        headers=_auth(),
    )
    assert ingest_resp.status_code in (200, 201)

    resp = client.get(f"/knowledge/collections/{coll_id}/stats", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["collection_id"] == coll_id
    assert "embedding_coverage_pct" in body
    assert "avg_chunk_length" in body
    assert "source_type_distribution" in body
    assert "health_score" in body


# ---------------------------------------------------------------------------
# Bulk analytics (/knowledge/analytics)
# ---------------------------------------------------------------------------


def test_analytics_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/knowledge/analytics")
    assert resp.status_code == 401


def test_analytics_no_knowledge_store() -> None:
    from app.rag.semantic_cache import SemanticCache

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(knowledge_router)
    app.state.semantic_cache = SemanticCache()
    # Deliberately do not set app.state.knowledge_store.

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/knowledge/analytics", headers=_auth())
    # No knowledge store is an outage, not "zero collections".
    assert resp.status_code == 503


def test_analytics_success() -> None:
    client = _client()
    client.post("/knowledge/collections", json={"name": "A1"}, headers=_auth())
    client.post("/knowledge/collections", json={"name": "A2"}, headers=_auth())

    resp = client.get("/knowledge/analytics", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_collections"] == 2
    assert len(body["collections"]) == 2
    # Was a hardcoded 75 / total_chunks=0; now computed from real counters —
    # an empty collection scores 0 and relevance/cache are honestly unknown.
    assert body["collections"][0]["health_score"] == 0
    assert body["collections"][0]["total_chunks"] == 0
    assert body["collections"][0]["avg_relevance_score"] is None


def test_analytics_failure_is_503_not_200_with_error_field() -> None:
    store = KnowledgeStore()

    async def _boom(**_: Any) -> Any:
        raise RuntimeError("db exploded")

    store.collection_counters_async = _boom  # type: ignore[method-assign]
    client = _client(knowledge_store=store)
    resp = client.get("/knowledge/analytics", headers=_auth())
    assert resp.status_code == 503
    assert "db exploded" not in resp.text


# ---------------------------------------------------------------------------
# ingest_document_into_collection — extra branches
# ---------------------------------------------------------------------------


def test_ingest_into_collection_not_found() -> None:
    client = _client()
    resp = client.post(
        "/knowledge/collections/nonexistent/documents",
        json={"content": "hello world"},
        headers=_auth(),
    )
    assert resp.status_code == 404


def test_ingest_into_collection_indexing_requires_embedder() -> None:
    client = _client()  # no embedder configured
    created = client.post(
        "/knowledge/collections", json={"name": "Idx Coll"}, headers=_auth()
    ).json()
    coll_id = created["collection_id"]

    resp = client.post(
        f"/knowledge/collections/{coll_id}/documents",
        json={
            "content": "hello world",
            "source_identity": "doc-1",
            "indexing_strategies": ["raptor"],
        },
        headers=_auth(),
    )
    assert resp.status_code == 503
    assert "embedder" in resp.json()["detail"]


def test_ingest_into_collection_indexing_dependency_mismatch() -> None:
    embedder = _make_embedder()
    client = _client(embedder=embedder)
    created = client.post(
        "/knowledge/collections", json={"name": "Idx Coll 2"}, headers=_auth()
    ).json()
    coll_id = created["collection_id"]

    resp = client.post(
        f"/knowledge/collections/{coll_id}/documents",
        json={
            "content": "hello world",
            "source_identity": "doc-2",
            "indexing_strategies": ["raptor"],
        },
        headers=_auth(),
    )
    assert resp.status_code == 503
    assert "provider or model" in resp.json()["detail"]


def test_ingest_into_collection_empty_indexed_content() -> None:
    client = _client()
    created = client.post(
        "/knowledge/collections", json={"name": "Empty Idx Coll"}, headers=_auth()
    ).json()
    coll_id = created["collection_id"]

    fake_orch = MagicMock()
    fake_orch.ingest = AsyncMock(side_effect=EmptyIndexedContentError("no chunks"))
    with patch("app.ingestion.orchestrator.IngestionOrchestrator", return_value=fake_orch):
        resp = client.post(
            f"/knowledge/collections/{coll_id}/documents",
            json={"content": "hello world"},
            headers=_auth(),
        )
    assert resp.status_code == 422


def test_ingest_into_collection_embedding_provider_unavailable() -> None:
    client = _client()
    created = client.post(
        "/knowledge/collections", json={"name": "Unavail Idx Coll"}, headers=_auth()
    ).json()
    coll_id = created["collection_id"]

    fake_orch = MagicMock()
    fake_orch.ingest = AsyncMock(
        side_effect=EmbeddingProviderUnavailableError("no provider configured")
    )
    with patch("app.ingestion.orchestrator.IngestionOrchestrator", return_value=fake_orch):
        resp = client.post(
            f"/knowledge/collections/{coll_id}/documents",
            json={"content": "hello world"},
            headers=_auth(),
        )
    assert resp.status_code == 503
    assert "Embedding provider" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# list_documents — KB-DOCUMENTS-503: documents come from the chunk rows
# ---------------------------------------------------------------------------


def test_list_documents_no_store_is_503() -> None:
    from app.rag.semantic_cache import SemanticCache

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(knowledge_router)
    app.state.semantic_cache = SemanticCache()

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/knowledge/collections/col-1/documents", headers=_auth())
    # An unwired store is unavailability, not an empty collection.
    assert resp.status_code == 503


def _seeded_store() -> tuple[KnowledgeStore, str]:
    from app.rag.models import Chunk, KnowledgeCollection

    store = KnowledgeStore()
    coll_id = store.create_collection(KnowledgeCollection(name="docs"), tenant_ctx=_CTX)
    cstore = store._data[(_CTX.tenant_id, coll_id)]
    for i in range(1, 6):
        for c in range(2 if i == 3 else 1):
            cstore.chunks.append(
                Chunk(
                    document_id=f"doc-{i}",
                    content=f"body {i}.{c}",
                    embedding=[0.0],
                    chunk_index=c,
                    metadata={
                        "source_file": f"file-{i}.pdf" if i % 2 else "",
                        "source_url": "" if i % 2 else f"https://example.test/{i}",
                        "doc_title": "" if i % 2 else f"Page {i}",
                        "source_type": "pdf" if i % 2 else "web",
                    },
                )
            )
    return store, coll_id


def test_list_documents_pages_with_cursor() -> None:
    store, coll_id = _seeded_store()
    client = _client(knowledge_store=store)
    first = client.get(f"/knowledge/collections/{coll_id}/documents?limit=2", headers=_auth())
    assert first.status_code == 200, first.text
    body = first.json()
    assert [d["id"] for d in body["documents"]] == ["doc-1", "doc-2"]
    assert body["total"] == 5 and body["next_cursor"] == "doc-2"
    assert body["documents"][0]["title"] == "file-1.pdf"
    assert body["documents"][1]["source_url"] == "https://example.test/2"
    second = client.get(
        f"/knowledge/collections/{coll_id}/documents?limit=2&cursor=doc-2", headers=_auth()
    ).json()
    assert [d["id"] for d in second["documents"]] == ["doc-3", "doc-4"]
    assert second["documents"][0]["chunk_count"] == 2
    assert second["documents"][0]["preview"] == "body 3.0"


def test_list_documents_filters_and_legacy_offset() -> None:
    store, coll_id = _seeded_store()
    client = _client(knowledge_store=store)
    web = client.get(
        f"/knowledge/collections/{coll_id}/documents?source_type=web", headers=_auth()
    ).json()
    assert [d["id"] for d in web["documents"]] == ["doc-2", "doc-4"]
    hit = client.get(
        f"/knowledge/collections/{coll_id}/documents?search=FILE-5", headers=_auth()
    ).json()
    assert [d["id"] for d in hit["documents"]] == ["doc-5"]
    offset = client.get(
        f"/knowledge/collections/{coll_id}/documents?limit=2&offset=3", headers=_auth()
    ).json()
    assert [d["id"] for d in offset["documents"]] == ["doc-4", "doc-5"]


def test_list_documents_unknown_or_foreign_collection_is_404() -> None:
    store, _ = _seeded_store()
    other = TenantContext(tenant_id="someone-else", plan=PlanTier.FREE, api_key_id="x")
    from app.rag.models import KnowledgeCollection

    foreign = store.create_collection(KnowledgeCollection(name="theirs"), tenant_ctx=other)
    client = _client(knowledge_store=store)
    for cid in ("nope", foreign):
        resp = client.get(f"/knowledge/collections/{cid}/documents", headers=_auth())
        assert resp.status_code == 404, resp.text


def test_list_documents_bounds_page_size_and_offset() -> None:
    store, coll_id = _seeded_store()
    client = _client(knowledge_store=store)
    for query in ("limit=101", "limit=0", "offset=10001", "offset=-1"):
        resp = client.get(f"/knowledge/collections/{coll_id}/documents?{query}", headers=_auth())
        assert resp.status_code == 422, query


def test_list_documents_failure_is_503() -> None:
    store, coll_id = _seeded_store()

    async def _boom(**kwargs: Any) -> Any:
        raise RuntimeError("boom")

    store.list_collection_documents_async = _boom  # type: ignore[method-assign]
    client = _client(knowledge_store=store)
    resp = client.get(f"/knowledge/collections/{coll_id}/documents", headers=_auth())
    # Was 200 {"documents": [], "error": "boom"} — looked like an empty collection.
    assert resp.status_code == 503
    assert "boom" not in resp.text


# ---------------------------------------------------------------------------
# delete_document
# ---------------------------------------------------------------------------


def test_delete_document_no_store() -> None:
    from app.rag.semantic_cache import SemanticCache

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(knowledge_router)
    app.state.semantic_cache = SemanticCache()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.delete("/knowledge/collections/col-1/documents/doc-1", headers=_auth())
    assert resp.status_code == 503


def test_delete_document_success_via_real_store() -> None:
    embedder = _make_embedder()
    client = _client(embedder=embedder)
    created = client.post(
        "/knowledge/collections", json={"name": "Del Doc Coll"}, headers=_auth()
    ).json()
    coll_id = created["collection_id"]
    ingest_resp = client.post(
        "/knowledge/ingest",
        json={"collection_id": coll_id, "source_type": "text", "content": "content to delete"},
        headers=_auth(),
    )
    assert ingest_resp.status_code in (200, 201)

    # Discover a real document_id from the collection stats' underlying store.
    store: KnowledgeStore = client.app.state.knowledge_store  # type: ignore[attr-defined]
    key = (_CTX.tenant_id, coll_id)
    col_store = store._data[key]
    document_id = col_store.chunks[0].document_id

    resp = client.delete(
        f"/knowledge/collections/{coll_id}/documents/{document_id}", headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"


def _collection_with_doc(client: TestClient, content: str, **meta: str) -> tuple[str, str]:
    coll_id = client.post(
        "/knowledge/collections", json={"name": f"c-{content[:6]}"}, headers=_auth()
    ).json()["collection_id"]
    resp = client.post(
        "/knowledge/ingest",
        json={"collection_id": coll_id, "source_type": "text", "content": content},
        headers=_auth(),
    )
    assert resp.status_code in (200, 201)
    store: KnowledgeStore = client.app.state.knowledge_store  # type: ignore[attr-defined]
    chunks = store._data[(_CTX.tenant_id, coll_id)].chunks
    for chunk in chunks:
        chunk.metadata = {**(chunk.metadata or {}), **meta}
    return coll_id, chunks[0].document_id


def test_delete_document_unknown_collection_or_document_is_404() -> None:
    """Deleting in a collection the caller does not have (or a missing doc) is 404."""
    client = _client(embedder=_make_embedder())
    assert client.delete(
        "/knowledge/collections/not-mine/documents/doc-1", headers=_auth()
    ).status_code == 404
    coll_id, _ = _collection_with_doc(client, "some content here")
    assert client.delete(
        f"/knowledge/collections/{coll_id}/documents/missing-doc", headers=_auth()
    ).status_code == 404


# ---------------------------------------------------------------------------
# reingest_document
# ---------------------------------------------------------------------------


def test_reingest_document_no_store() -> None:
    from app.rag.semantic_cache import SemanticCache

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(knowledge_router)
    app.state.semantic_cache = SemanticCache()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/knowledge/collections/col-1/documents/doc-1/reingest", headers=_auth()
    )
    assert resp.status_code == 503


def test_reingest_unknown_collection_or_document_is_404() -> None:
    client = _client(embedder=_make_embedder())
    assert client.post(
        "/knowledge/collections/not-mine/documents/d/reingest", headers=_auth()
    ).status_code == 404
    coll_id, _ = _collection_with_doc(client, "uploaded text body")
    assert client.post(
        f"/knowledge/collections/{coll_id}/documents/missing/reingest", headers=_auth()
    ).status_code == 404


def test_reingest_uploaded_document_is_409() -> None:
    """Uploaded content has no source to re-fetch; say so instead of faking "queued"."""
    client = _client(embedder=_make_embedder())
    coll_id, doc_id = _collection_with_doc(client, "uploaded text body")
    resp = client.post(
        f"/knowledge/collections/{coll_id}/documents/{doc_id}/reingest", headers=_auth()
    )
    assert resp.status_code == 409


def test_reingest_url_document_replaces_it() -> None:
    client = _client(embedder=_make_embedder())
    coll_id, doc_id = _collection_with_doc(
        client, "old page text", source_url="https://example.com/page", source_type="web"
    )
    from app.ingestion.web_fetch import WebResource

    fetched = WebResource(url="https://example.com/page", final_url="https://example.com/page",
                          status=200, content_type="text/plain",
                          data=b"fresh page text with new facts")
    with patch("app.api.knowledge._fetch_url_resource", new=AsyncMock(return_value=fetched)):
        resp = client.post(
            f"/knowledge/collections/{coll_id}/documents/{doc_id}/reingest", headers=_auth()
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "reingested" and body["previous_document_id"] == doc_id
    store: KnowledgeStore = client.app.state.knowledge_store  # type: ignore[attr-defined]
    chunks = store._data[(_CTX.tenant_id, coll_id)].chunks
    assert {c.document_id for c in chunks} == {body["document_id"]}
    assert "fresh page text" in chunks[0].content


def test_reingest_fetch_failure_keeps_the_old_document() -> None:
    from fastapi import HTTPException as _HTTPException

    client = _client(embedder=_make_embedder())
    coll_id, doc_id = _collection_with_doc(
        client, "old page text", source_url="https://example.com/p", source_type="web"
    )
    boom = AsyncMock(side_effect=_HTTPException(502, "https://example.com/p: HTTP 503"))
    with patch("app.api.knowledge._fetch_url_resource", new=boom):
        resp = client.post(
            f"/knowledge/collections/{coll_id}/documents/{doc_id}/reingest", headers=_auth()
        )
    assert resp.status_code == 502
    store: KnowledgeStore = client.app.state.knowledge_store  # type: ignore[attr-defined]
    assert {c.document_id for c in store._data[(_CTX.tenant_id, coll_id)].chunks} == {doc_id}


# ---------------------------------------------------------------------------
# sync_collection
# ---------------------------------------------------------------------------


def test_sync_collection_no_store() -> None:
    from app.rag.semantic_cache import SemanticCache

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(knowledge_router)
    app.state.semantic_cache = SemanticCache()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/knowledge/collections/col-1/sync", headers=_auth())
    assert resp.status_code == 503


def test_sync_unknown_collection_is_404() -> None:
    client = _client(embedder=_make_embedder())
    assert client.post("/knowledge/collections/not-mine/sync", headers=_auth()).status_code == 404


def test_sync_without_ingestion_framework_is_503() -> None:
    client = _client(embedder=_make_embedder())
    coll_id, _ = _collection_with_doc(client, "text")
    client.app.state.source_store = None  # type: ignore[attr-defined]
    client.app.state.ingestion_tracker = None  # type: ignore[attr-defined]
    with patch("app.api.ingestion._get_source_store", return_value=None):
        resp = client.post(f"/knowledge/collections/{coll_id}/sync", headers=_auth())
    assert resp.status_code == 503


def test_sync_queues_each_source_of_the_collection_on_the_celery_ingestion_queue() -> None:
    """KB-SYNC-WORKER: syncs run in the durable worker, not in this API process."""
    from types import SimpleNamespace

    client = _client(embedder=_make_embedder())
    coll_id, _ = _collection_with_doc(client, "text")
    mine = SimpleNamespace(source_id="s1", collection_id=coll_id, source_type="github")
    busy = SimpleNamespace(source_id="s2", collection_id=coll_id, source_type="github")
    other = SimpleNamespace(source_id="s3", collection_id="another-collection")
    source_store = SimpleNamespace(list=AsyncMock(return_value=[mine, busy, other]))
    tracker = SimpleNamespace(
        acquire_lock=AsyncMock(side_effect=lambda sid, tid: None if sid == "s2" else f"job-{sid}"),
        release_lock=AsyncMock(),
    )
    with (
        patch("app.api.ingestion._get_source_store", return_value=source_store),
        patch("app.api.ingestion._get_tracker", return_value=tracker),
        patch("app.ingestion.scheduler.sync_source_task") as task,
    ):
        resp = client.post(f"/knowledge/collections/{coll_id}/sync", headers=_auth())
    assert resp.status_code == 202
    body = resp.json()
    assert body["queued"] == [{"source_id": "s1", "job_id": "job-s1"}]
    assert body["already_running"] == ["s2"]
    task.apply_async.assert_called_once()
    call = task.apply_async.call_args.kwargs
    assert call["queue"] == "ingestion"
    assert call["kwargs"] == {
        "source_id": "s1", "tenant_id": _CTX.tenant_id, "triggered_by": "manual", "job_id": "job-s1",
    }
    tracker.release_lock.assert_not_awaited()


def test_sync_enqueue_failure_releases_the_lock_and_is_503() -> None:
    from types import SimpleNamespace

    client = _client(embedder=_make_embedder())
    coll_id, _ = _collection_with_doc(client, "text")
    src = SimpleNamespace(source_id="s1", collection_id=coll_id, source_type="github")
    source_store = SimpleNamespace(list=AsyncMock(return_value=[src]))
    tracker = SimpleNamespace(acquire_lock=AsyncMock(return_value="job-1"), release_lock=AsyncMock())
    with (
        patch("app.api.ingestion._get_source_store", return_value=source_store),
        patch("app.api.ingestion._get_tracker", return_value=tracker),
        patch("app.ingestion.scheduler.sync_source_task") as task,
    ):
        task.apply_async.side_effect = ConnectionError("broker down")
        resp = client.post(f"/knowledge/collections/{coll_id}/sync", headers=_auth())
    assert resp.status_code == 503
    tracker.release_lock.assert_awaited_once_with("s1", _CTX.tenant_id, "job-1")


# ---------------------------------------------------------------------------
# New ingestion sources: email / notion / gdrive
# ---------------------------------------------------------------------------


def test_ingest_email_no_store() -> None:
    from app.rag.semantic_cache import SemanticCache

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(knowledge_router)
    app.state.semantic_cache = SemanticCache()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/knowledge/ingest/email",
        json={"raw_email": "Subject: Hi\n\nBody", "collection_id": "col-1"},
        headers=_auth(),
    )
    assert resp.status_code == 503


def test_ingest_email_success() -> None:
    client = _client()
    fake_orch = MagicMock()
    fake_orch.ingest = AsyncMock(return_value=SimpleNamespace(chunks_created=2))
    with patch("app.ingestion.orchestrator.IngestionOrchestrator", return_value=fake_orch):
        resp = client.post(
            "/knowledge/ingest/email",
            json={
                "raw_email": "Subject: Hello\nFrom: a@x.com\nTo: b@x.com\n\nBody text",
                "collection_id": "col-1",
            },
            headers=_auth(),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ingested"
    assert body["chunks_created"] == 2
    assert body["source"] == "email"


def test_ingest_email_exception_returns_generic_502() -> None:
    client = _client()
    with patch(
        "app.ingestion.orchestrator.IngestionOrchestrator",
        side_effect=RuntimeError("bad email"),
    ):
        resp = client.post(
            "/knowledge/ingest/email",
            json={"raw_email": "Subject: Hi\n\nBody", "collection_id": "col-1"},
            headers=_auth(),
        )
    assert resp.status_code == 502  # generic upstream error, no raw text (KB-12)


def test_ingest_notion_requires_page_or_database() -> None:
    client = _client()
    resp = client.post(
        "/knowledge/ingest/notion",
        json={"api_key": "secret", "collection_id": "col-1"},
        headers=_auth(),
    )
    assert resp.status_code == 400


def test_ingest_notion_page_id_success() -> None:
    client = _client()
    fake_connector = MagicMock()
    fake_connector.fetch_page_content = AsyncMock(return_value="page content")
    fake_orch = MagicMock()
    fake_orch.ingest = AsyncMock(return_value=SimpleNamespace(chunks_created=1))

    with (
        patch(
            "app.ingestion.connectors.notion_connector.NotionConnector",
            return_value=fake_connector,
        ),
        patch("app.ingestion.orchestrator.IngestionOrchestrator", return_value=fake_orch),
    ):
        resp = client.post(
            "/knowledge/ingest/notion",
            json={"api_key": "secret", "page_id": "page-1", "collection_id": "col-1"},
            headers=_auth(),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["chunks_created"] == 1
    assert body["source"] == "notion"


def test_ingest_notion_database_id_success() -> None:
    client = _client()
    fake_connector = MagicMock()
    fake_connector.list_pages = AsyncMock(return_value=[{"id": "p1"}, {"id": "p2"}])
    fake_connector.fetch_page_content = AsyncMock(side_effect=["content 1", ""])
    fake_orch = MagicMock()
    fake_orch.ingest = AsyncMock(return_value=SimpleNamespace(chunks_created=1))

    with (
        patch(
            "app.ingestion.connectors.notion_connector.NotionConnector",
            return_value=fake_connector,
        ),
        patch("app.ingestion.orchestrator.IngestionOrchestrator", return_value=fake_orch),
    ):
        resp = client.post(
            "/knowledge/ingest/notion",
            json={"api_key": "secret", "database_id": "db-1", "collection_id": "col-1"},
            headers=_auth(),
        )
    assert resp.status_code == 200
    body = resp.json()
    # Only the first page had non-empty content → orch.ingest called once.
    assert body["chunks_created"] == 1
    assert fake_orch.ingest.await_count == 1


def test_ingest_notion_exception_returns_generic_502() -> None:
    client = _client()
    with patch(
        "app.ingestion.connectors.notion_connector.NotionConnector",
        side_effect=RuntimeError("bad token"),
    ):
        resp = client.post(
            "/knowledge/ingest/notion",
            json={"api_key": "secret", "page_id": "page-1", "collection_id": "col-1"},
            headers=_auth(),
        )
    assert resp.status_code == 502  # generic upstream error, no raw text (KB-12)


def test_ingest_gdrive_folder_success() -> None:
    client = _client()
    fake_connector = MagicMock()
    fake_connector.list_files.return_value = [
        {"id": "f1", "name": "one.txt", "mimeType": "text/plain"},
        {"id": "f2", "name": "two.txt", "mimeType": "text/plain"},
    ]
    fake_connector.download_file.side_effect = ["content one", ""]
    fake_orch = MagicMock()
    fake_orch.ingest = AsyncMock(return_value=SimpleNamespace(chunks_created=4))

    with (
        patch(
            "app.ingestion.connectors.gdrive_connector.GDriveConnector",
            return_value=fake_connector,
        ),
        patch("app.ingestion.orchestrator.IngestionOrchestrator", return_value=fake_orch),
    ):
        resp = client.post(
            "/knowledge/ingest/gdrive-folder",
            json={
                "folder_id": "folder-1",
                "collection_id": "col-1",
                "service_account_key_json": '{"type": "service_account"}',
            },
            headers=_auth(),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "gdrive"
    assert body["files_processed"] == 2
    assert body["chunks_created"] == 4
    assert body["errors"] == []


def test_ingest_gdrive_folder_file_error_collected() -> None:
    client = _client()
    fake_connector = MagicMock()
    fake_connector.list_files.return_value = [
        {"id": "f1", "name": "bad.txt", "mimeType": "text/plain"},
    ]
    fake_connector.download_file.side_effect = RuntimeError("download failed")
    fake_orch = MagicMock()
    fake_orch.ingest = AsyncMock(return_value=SimpleNamespace(chunks_created=0))

    with (
        patch(
            "app.ingestion.connectors.gdrive_connector.GDriveConnector",
            return_value=fake_connector,
        ),
        patch("app.ingestion.orchestrator.IngestionOrchestrator", return_value=fake_orch),
    ):
        resp = client.post(
            "/knowledge/ingest/gdrive-folder",
            json={
                "folder_id": "folder-1",
                "collection_id": "col-1",
                "service_account_key_json": '{"type": "service_account"}',
            },
            headers=_auth(),
        )
    # The only file failed: nothing was ingested, so this is not a success.
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["status"] == "failed"
    assert len(detail["errors"]) == 1
    assert "download failed" in detail["errors"][0]
    assert detail["failed"][0]["file_id"] == "f1"


def test_ingest_gdrive_folder_exception_returns_generic_502() -> None:
    client = _client()
    with patch(
        "app.ingestion.connectors.gdrive_connector.GDriveConnector",
        side_effect=RuntimeError("bad key"),
    ):
        resp = client.post(
            "/knowledge/ingest/gdrive-folder",
            json={
                "folder_id": "folder-1",
                "collection_id": "col-1",
                "service_account_key_json": '{"type": "service_account"}',
            },
            headers=_auth(),
        )
    assert resp.status_code == 502  # generic upstream error, no raw text (KB-12)


# ---------------------------------------------------------------------------
# RPA URL ingestion — extra branches (lazy executor init, scrape failure,
# empty content)
# ---------------------------------------------------------------------------


def test_rpa_url_lazy_creates_executor_on_empty_content() -> None:
    """When app.state.rpa_executor is unset, the endpoint lazily builds one."""
    client = _client()
    assert not hasattr(client.app.state, "rpa_executor")  # type: ignore[attr-defined]

    scraped = SimpleNamespace(content="   ", content_hash="", chunks=[])
    with patch(
        "app.rpa.kb_emit.scrape_url_to_chunks",
        new=AsyncMock(return_value=scraped),
    ):
        resp = client.post(
            "/knowledge/ingest/rpa-url",
            json={"collection_id": "col-1", "urls": ["https://example.com/page"]},
            headers=_auth(),
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["results"][0]["success"] is False
    assert body["results"][0]["error"] == "No content extracted"
    assert hasattr(client.app.state, "rpa_executor")  # type: ignore[attr-defined]


def test_rpa_url_scrape_exception_captured_per_url() -> None:
    client = _client()
    with patch(
        "app.rpa.kb_emit.scrape_url_to_chunks",
        new=AsyncMock(side_effect=RuntimeError("scrape blew up")),
    ):
        resp = client.post(
            "/knowledge/ingest/rpa-url",
            json={"collection_id": "col-1", "urls": ["https://example.com/broken"]},
            headers=_auth(),
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["results"][0]["success"] is False
    assert "scrape blew up" in body["results"][0]["error"]
    assert body["urls_succeeded"] == 0
