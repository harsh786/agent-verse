"""Targeted tests for previously-uncovered branches in app/api/knowledge.py.

Focuses on: RAG chat, collection stats, bulk analytics, the document browser
(list/delete/reingest/sync), new ingestion sources (email/notion/gdrive), the
RPA URL ingestion edge cases, and extra branches of the IngestionOrchestrator
document-ingest endpoint.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, UTC
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
    assert resp.status_code == 200
    assert resp.json() == {"collections": [], "total_documents": 0, "total_collections": 0}


def test_analytics_success() -> None:
    client = _client()
    client.post("/knowledge/collections", json={"name": "A1"}, headers=_auth())
    client.post("/knowledge/collections", json={"name": "A2"}, headers=_auth())

    resp = client.get("/knowledge/analytics", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_collections"] == 2
    assert len(body["collections"]) == 2
    assert body["collections"][0]["health_score"] == 75


def test_analytics_exception_is_caught() -> None:
    store = KnowledgeStore()

    async def _boom(*, tenant_ctx: Any) -> Any:
        raise RuntimeError("db exploded")

    store.list_collections_async = _boom  # type: ignore[method-assign]
    client = _client(knowledge_store=store)
    resp = client.get("/knowledge/analytics", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_collections"] == 0
    assert "db exploded" in body["error"]


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
# list_documents — native method + DB fallback branches
# ---------------------------------------------------------------------------


def test_list_documents_no_store() -> None:
    from app.rag.semantic_cache import SemanticCache

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(knowledge_router)
    app.state.semantic_cache = SemanticCache()

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/knowledge/collections/col-1/documents", headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == {"documents": [], "total": 0}


def test_list_documents_native_method() -> None:
    class _FakeStore:
        async def list_documents(
            self,
            *,
            collection_id: str,
            tenant_ctx: Any,
            limit: int,
            offset: int,
            search: str | None,
        ) -> dict[str, Any]:
            return {"documents": [{"id": "doc-1", "title": "T"}], "total": 1}

    client = _client(knowledge_store=_FakeStore())
    resp = client.get("/knowledge/collections/col-1/documents", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["documents"][0]["id"] == "doc-1"


def test_list_documents_native_method_list_result() -> None:
    class _FakeStore:
        async def list_documents(self, **kwargs: Any) -> list[dict[str, Any]]:
            return [{"id": "doc-1"}, {"id": "doc-2"}]

    client = _client(knowledge_store=_FakeStore())
    resp = client.get("/knowledge/collections/col-1/documents", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2


class _FakeResult:
    def __init__(self, rows: list[tuple] | None = None, scalar: int | None = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self) -> list[tuple]:
        return self._rows

    def scalar(self) -> int | None:
        return self._scalar


class _FakeSession:
    def __init__(self, rows: list[tuple], total: int) -> None:
        self._rows = rows
        self._total = total

    async def execute(self, query: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        text = str(query)
        if "COUNT(" in text:
            return _FakeResult(scalar=self._total)
        if "SET_CONFIG" in text.upper() or "SET_CONFIG" in text:
            return _FakeResult()
        return _FakeResult(rows=self._rows)

    async def commit(self) -> None:
        pass

    async def begin(self) -> Any:
        @asynccontextmanager
        async def _cm() -> Any:
            yield self

        return _cm()


class _FakeSessionCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _rls_noop():
    @asynccontextmanager
    async def _ctx(session: Any, tenant_id: str) -> Any:
        yield session

    return _ctx


def test_list_documents_db_fallback() -> None:
    rows = [
        (
            "doc-1",
            "Title",
            "source.md",
            "markdown",
            3,
            datetime(2024, 1, 1, tzinfo=UTC),
            "preview text",
        )
    ]
    session = _FakeSession(rows, total=1)

    class _FakeStore:
        _db = staticmethod(lambda: _FakeSessionCM(session))

    with patch("app.db.rls.sqlalchemy_rls_context", new=_rls_noop()):
        client = _client(knowledge_store=_FakeStore())
        resp = client.get(
            "/knowledge/collections/col-1/documents?search=foo", headers=_auth()
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["documents"][0]["id"] == "doc-1"
    assert body["documents"][0]["title"] == "Title"


def test_list_documents_exception_is_caught() -> None:
    class _FakeStore:
        async def list_documents(self, **kwargs: Any) -> Any:
            raise RuntimeError("boom")

    client = _client(knowledge_store=_FakeStore())
    resp = client.get("/knowledge/collections/col-1/documents", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert "boom" in body["error"]


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


def test_delete_document_not_implemented() -> None:
    class _FakeStore:
        pass

    client = _client(knowledge_store=_FakeStore())
    resp = client.delete("/knowledge/collections/col-1/documents/doc-1", headers=_auth())
    assert resp.status_code == 501


def test_delete_document_exception_returns_500() -> None:
    class _FakeStore:
        async def delete_document_async(self, **kwargs: Any) -> int:
            raise RuntimeError("delete failed")

    client = _client(knowledge_store=_FakeStore())
    resp = client.delete("/knowledge/collections/col-1/documents/doc-1", headers=_auth())
    assert resp.status_code == 500


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


def test_reingest_document_native_method() -> None:
    class _FakeStore:
        async def reingest_document(self, **kwargs: Any) -> None:
            return None

    client = _client(knowledge_store=_FakeStore())
    resp = client.post(
        "/knowledge/collections/col-1/documents/doc-1/reingest", headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "reingested"


def test_reingest_document_db_fallback_queued() -> None:
    session = _FakeSession(rows=[], total=0)

    class _FakeStore:
        _db = staticmethod(lambda: _FakeSessionCM(session))

    with patch("app.db.rls.sqlalchemy_rls_context", new=_rls_noop()):
        client = _client(knowledge_store=_FakeStore())
        resp = client.post(
            "/knowledge/collections/col-1/documents/doc-1/reingest", headers=_auth()
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"


def test_reingest_document_unsupported() -> None:
    client = _client()  # real in-memory KnowledgeStore, no _db
    resp = client.post(
        "/knowledge/collections/col-1/documents/doc-1/reingest", headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "unsupported"


def test_reingest_document_exception_returns_500() -> None:
    class _FakeStore:
        async def reingest_document(self, **kwargs: Any) -> None:
            raise RuntimeError("reingest failed")

    client = _client(knowledge_store=_FakeStore())
    resp = client.post(
        "/knowledge/collections/col-1/documents/doc-1/reingest", headers=_auth()
    )
    assert resp.status_code == 500


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


def test_sync_collection_native_method() -> None:
    class _FakeStore:
        async def sync_collection(self, **kwargs: Any) -> dict[str, Any]:
            return {"documents_synced": 3}

    client = _client(knowledge_store=_FakeStore())
    resp = client.post("/knowledge/collections/col-1/sync", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "syncing"
    assert body["documents_synced"] == 3


def test_sync_collection_unsupported() -> None:
    client = _client()
    resp = client.post("/knowledge/collections/col-1/sync", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["status"] == "unsupported"


def test_sync_collection_exception_returns_500() -> None:
    class _FakeStore:
        async def sync_collection(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("sync failed")

    client = _client(knowledge_store=_FakeStore())
    resp = client.post("/knowledge/collections/col-1/sync", headers=_auth())
    assert resp.status_code == 500


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


def test_ingest_email_exception_returns_500() -> None:
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
    assert resp.status_code == 500


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


def test_ingest_notion_exception_returns_500() -> None:
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
    assert resp.status_code == 500


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
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["errors"]) == 1
    assert "download failed" in body["errors"][0]


def test_ingest_gdrive_folder_exception_returns_500() -> None:
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
    assert resp.status_code == 500


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
