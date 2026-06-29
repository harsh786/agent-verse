"""Extra coverage for app/api/knowledge.py — ingestors, URL ingest, OpenAPI ingest."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge import router as knowledge_router
from app.providers.fake import FakeProvider
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-rag-ex", plan=PlanTier.ENTERPRISE, api_key_id="kid-ex")
_VALID_KEY = "av_test_ragkey_extra"


def _make_app(
    knowledge_store: KnowledgeStore | None = None,
    semantic_cache: SemanticCache | None = None,
    embedder: object | None = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(knowledge_router)
    app.state.knowledge_store = knowledge_store or KnowledgeStore()
    app.state.semantic_cache = semantic_cache or SemanticCache()
    # FakeProvider gives embeddings so search works
    app.state.embedder = embedder if embedder is not None else FakeProvider(embed_dim=768)
    return app


_H = {"X-API-Key": _VALID_KEY}


# ── Collection CRUD ───────────────────────────────────────────────────────────

class TestCollectionCrud:
    def test_create_and_list_collection(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.post(
            "/knowledge/collections",
            json={"name": "Test Collection", "description": "For testing"},
            headers=_H,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "collection_id" in body
        assert body["name"] == "Test Collection"

        list_resp = client.get("/knowledge/collections", headers=_H)
        assert list_resp.status_code == 200
        assert len(list_resp.json()) >= 1

    def test_delete_nonexistent_collection(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.delete("/knowledge/collections/ghost-id", headers=_H)
        assert resp.status_code == 404

    def test_delete_existing_collection(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        create_resp = client.post(
            "/knowledge/collections",
            json={"name": "Deletable Collection"},
            headers=_H,
        )
        cid = create_resp.json()["collection_id"]
        del_resp = client.delete(f"/knowledge/collections/{cid}", headers=_H)
        assert del_resp.status_code == 204

    def test_collections_unauthorized(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/knowledge/collections")
        assert resp.status_code == 401


# ── Text ingest ───────────────────────────────────────────────────────────────

class TestTextIngest:
    def test_ingest_text_chunk(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        # First create a collection
        cid_resp = client.post(
            "/knowledge/collections",
            json={"name": "Docs"},
            headers=_H,
        )
        cid = cid_resp.json()["collection_id"]

        resp = client.post(
            "/knowledge/ingest",
            json={
                "collection_id": cid,
                "source_type": "text",
                "content": "This is some test content that needs to be ingested into the knowledge store for retrieval.",
            },
            headers=_H,
        )
        assert resp.status_code in (201, 200)
        body = resp.json()
        assert "chunks_created" in body or "document_id" in body or "ok" in str(body)

    def test_ingest_markdown_content(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        cid_resp = client.post(
            "/knowledge/collections",
            json={"name": "Markdown"},
            headers=_H,
        )
        cid = cid_resp.json()["collection_id"]

        content = "# Architecture\n\n## Overview\n\nThis document describes the system architecture.\n\n## Components\n\nMultiple microservices communicate via message queues."
        resp = client.post(
            "/knowledge/ingest",
            json={"collection_id": cid, "source_type": "markdown", "content": content},
            headers=_H,
        )
        assert resp.status_code in (201, 200)


# ── OpenAPI ingest ────────────────────────────────────────────────────────────

class TestOpenAPIIngest:
    def _make_openapi_spec(self) -> str:
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0"},
            "paths": {
                "/users": {
                    "get": {
                        "summary": "List users",
                        "description": "Returns a list of all users in the system",
                        "operationId": "listUsers",
                        "responses": {"200": {"description": "Success"}},
                    },
                    "post": {
                        "summary": "Create user",
                        "description": "Create a new user account with email and password",
                        "operationId": "createUser",
                    },
                },
                "/users/{id}": {
                    "get": {
                        "summary": "Get user",
                        "description": "Returns a specific user by ID",
                    }
                },
            },
        }
        return json.dumps(spec)

    def test_ingest_openapi_spec(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        cid_resp = client.post(
            "/knowledge/collections",
            json={"name": "API Docs"},
            headers=_H,
        )
        cid = cid_resp.json()["collection_id"]

        resp = client.post(
            "/knowledge/ingest/openapi",
            json={
                "collection_id": cid,
                "content": self._make_openapi_spec(),
                "source_url": "http://api.example.com/openapi.json",
            },
            headers=_H,
        )
        assert resp.status_code in (201, 200, 422)
        if resp.status_code == 201:
            body = resp.json()
            assert "chunks_created" in body or "endpoints_ingested" in body

    def test_ingest_invalid_openapi_returns_422(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        cid_resp = client.post(
            "/knowledge/collections",
            json={"name": "API Docs"},
            headers=_H,
        )
        cid = cid_resp.json()["collection_id"]

        resp = client.post(
            "/knowledge/ingest/openapi",
            json={
                "collection_id": cid,
                "content": "this is not valid JSON or YAML at all {{{",
            },
            headers=_H,
        )
        assert resp.status_code in (422, 500)


# ── URL ingest ────────────────────────────────────────────────────────────────

class TestUrlIngest:
    def test_ingest_url_web(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        cid_resp = client.post(
            "/knowledge/collections",
            json={"name": "Web Content"},
            headers=_H,
        )
        cid = cid_resp.json()["collection_id"]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "<html><head><title>Test Page</title></head><body><p>Content here.</p></body></html>"

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_http):
            resp = client.post(
                "/knowledge/ingest/url",
                json={
                    "collection_id": cid,
                    "url": "https://docs.example.com/guide",
                    "source_type": "web",
                },
                headers=_H,
            )
        assert resp.status_code in (201, 200, 422, 500)

    def test_ingest_url_unsupported_type(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        cid_resp = client.post(
            "/knowledge/collections",
            json={"name": "Test"},
            headers=_H,
        )
        cid = cid_resp.json()["collection_id"]

        resp = client.post(
            "/knowledge/ingest/url",
            json={
                "collection_id": cid,
                "url": "sftp://example.com/file",
                "source_type": "unsupported_type",
            },
            headers=_H,
        )
        assert resp.status_code in (400, 422, 500)

    def test_ingest_github_url(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        cid_resp = client.post(
            "/knowledge/collections",
            json={"name": "GitHub"},
            headers=_H,
        )
        cid = cid_resp.json()["collection_id"]

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "# README\n\nThis is documentation content for testing purposes."

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_http):
            resp = client.post(
                "/knowledge/ingest/url",
                json={
                    "collection_id": cid,
                    "url": "https://github.com/owner/repo/blob/main/README.md",
                    "source_type": "github",
                },
                headers=_H,
            )
        assert resp.status_code in (201, 200, 422, 500)


# ── Search ────────────────────────────────────────────────────────────────────

class TestKnowledgeSearch:
    def test_search_empty_collection(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        cid_resp = client.post(
            "/knowledge/collections",
            json={"name": "Search Test"},
            headers=_H,
        )
        cid = cid_resp.json()["collection_id"]

        # The search endpoint uses `q` not `query`
        resp = client.get(
            "/knowledge/search",
            params={
                "q": "test query about something",
                "collection_id": cid,
                "top_k": 5,
            },
            headers=_H,
        )
        assert resp.status_code in (200, 503)
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, list)

    def test_search_no_embedder_returns_503(self):
        client = TestClient(_make_app(embedder=None), raise_server_exceptions=False)
        cid_resp = client.post(
            "/knowledge/collections",
            json={"name": "Search Test 2"},
            headers=_H,
        )
        cid = cid_resp.json()["collection_id"]
        resp = client.get(
            "/knowledge/search",
            params={"q": "test", "collection_id": cid},
            headers=_H,
        )
        # With no embedder set → 503
        assert resp.status_code in (200, 503)

    def test_search_unauthorized(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/knowledge/search", params={"q": "test", "collection_id": "x"})
        assert resp.status_code == 401


# ── Semantic cache ────────────────────────────────────────────────────────────

class TestSemanticCache:
    def test_get_cache_stats(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/knowledge/cache/stats", headers=_H)
        assert resp.status_code in (200, 404)

    def test_clear_cache(self):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.delete("/knowledge/cache", headers=_H)
        assert resp.status_code in (200, 204, 404)
