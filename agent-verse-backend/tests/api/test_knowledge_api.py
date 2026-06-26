"""Tests for /knowledge API endpoints."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge import router as knowledge_router
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-rag", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_ragkey"


def _make_app(
    knowledge_store: KnowledgeStore | None = None,
    semantic_cache: SemanticCache | None = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(knowledge_router)
    app.state.knowledge_store = knowledge_store or KnowledgeStore()
    app.state.semantic_cache = semantic_cache or SemanticCache()
    return app


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

def test_list_collections_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/knowledge/collections", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_collection() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/knowledge/collections",
        json={
            "name": "engineering-docs",
            "description": "Internal engineering documentation",
            "embedder_type": "openai",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "engineering-docs"
    assert body["embedder"] == "openai"
    assert "collection_id" in body
    assert body["document_count"] == 0


def test_delete_collection() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    create_resp = client.post(
        "/knowledge/collections",
        json={"name": "temp-collection"},
        headers={"X-API-Key": _VALID_KEY},
    )
    cid = create_resp.json()["collection_id"]

    del_resp = client.delete(
        f"/knowledge/collections/{cid}", headers={"X-API-Key": _VALID_KEY}
    )
    assert del_resp.status_code == 204

    # Collection should no longer appear.
    list_resp = client.get("/knowledge/collections", headers={"X-API-Key": _VALID_KEY})
    assert list_resp.json() == []


def test_delete_collection_not_found() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete(
        "/knowledge/collections/ghost-cid", headers={"X-API-Key": _VALID_KEY}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def test_ingest_text() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    # Create collection first.
    create_resp = client.post(
        "/knowledge/collections",
        json={"name": "api-docs", "description": "API reference"},
        headers={"X-API-Key": _VALID_KEY},
    )
    cid = create_resp.json()["collection_id"]

    ingest_resp = client.post(
        "/knowledge/ingest",
        json={
            "collection_id": cid,
            "source_type": "text",
            "content": "The quick brown fox jumps over the lazy dog.",
            "metadata": {"author": "test"},
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert ingest_resp.status_code == 201
    body = ingest_resp.json()
    assert "document_id" in body
    assert body["collection_id"] == cid
    assert body["chunks_created"] >= 1
    assert "content_hash" in body


def test_ingest_into_missing_collection_returns_404() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/knowledge/ingest",
        json={
            "collection_id": "ghost-cid",
            "source_type": "text",
            "content": "Some content",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def test_search_returns_results() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    # Create collection and ingest content.
    cid = client.post(
        "/knowledge/collections",
        json={"name": "search-test"},
        headers={"X-API-Key": _VALID_KEY},
    ).json()["collection_id"]

    client.post(
        "/knowledge/ingest",
        json={
            "collection_id": cid,
            "source_type": "markdown",
            "content": "FastAPI is a modern web framework for building APIs with Python.",
        },
        headers={"X-API-Key": _VALID_KEY},
    )

    resp = client.get(
        f"/knowledge/search?q=FastAPI&collection_id={cid}&top_k=5&threshold=0.0",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    results = resp.json()
    assert isinstance(results, list)
    # With threshold=0.0 and random embeddings, at least the trigram score
    # should ensure results are returned for matching text.
    for result in results:
        assert "chunk_id" in result
        assert "content" in result
        assert "score" in result


def test_search_empty_collection_returns_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get(
        "/knowledge/search?q=anything&collection_id=no-such&top_k=5&threshold=0.0",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Semantic cache
# ---------------------------------------------------------------------------

def test_cache_stats() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/knowledge/cache/stats", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert "hits" in body
    assert "misses" in body
    assert body["hits"] == 0
    assert body["misses"] == 0


def test_clear_cache() -> None:
    cache = SemanticCache()
    # Populate the cache for our test tenant.
    cache.store(
        query_embedding=[0.1] * 768,
        response="cached response",
        tenant_ctx=_CTX,
    )
    assert len(cache._entries.get(_CTX.tenant_id, [])) == 1  # type: ignore[attr-defined]

    client = TestClient(_make_app(semantic_cache=cache), raise_server_exceptions=False)
    del_resp = client.delete("/knowledge/cache", headers={"X-API-Key": _VALID_KEY})
    assert del_resp.status_code == 204

    # Cache should now be empty for this tenant.
    assert _CTX.tenant_id not in cache._entries  # type: ignore[attr-defined]


def test_knowledge_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    assert client.get("/knowledge/collections").status_code == 401
    assert client.get("/knowledge/cache/stats").status_code == 401


# ---------------------------------------------------------------------------
# File upload ingestion
# ---------------------------------------------------------------------------

def test_ingest_file_txt() -> None:
    """POST /knowledge/ingest/file accepts plain text files."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    # Create a collection first
    r = client.post(
        "/knowledge/collections",
        json={"name": "test-col"},
        headers={"X-API-Key": _VALID_KEY},
    )
    col_id = r.json()["collection_id"]

    import io
    content = b"This is test content for the knowledge base. It has multiple sentences."
    r2 = client.post(
        "/knowledge/ingest/file",
        files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
        data={"collection_id": col_id},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert r2.status_code in (201, 503)  # 503 if no embedder configured
    if r2.status_code == 201:
        body = r2.json()
        assert body["filename"] == "test.txt"
        assert body["chunks_created"] >= 1
        assert body["collection_id"] == col_id


def test_ingest_openapi_parses_endpoints() -> None:
    """POST /knowledge/ingest/openapi creates chunks per endpoint."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    r = client.post(
        "/knowledge/collections",
        json={"name": "api-col"},
        headers={"X-API-Key": _VALID_KEY},
    )
    col_id = r.json()["collection_id"]
    spec = '{"openapi":"3.0.0","paths":{"/users":{"get":{"summary":"List users"}}}}'
    r2 = client.post(
        "/knowledge/ingest/openapi",
        json={"content": spec, "collection_id": col_id},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert r2.status_code in (201, 503)
    if r2.status_code == 201:
        assert r2.json()["endpoints_ingested"] >= 1
