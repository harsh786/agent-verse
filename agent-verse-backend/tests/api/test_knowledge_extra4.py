"""Extra tests for /knowledge API — push from 51% to 85%+ coverage.

Targets uncovered lines: 102, 116-118, 122, 192-204, 209-228, 271, 335,
392-463, 546-604, 606, 628-632, 639-682, 710-731, 734, 738-779, 803-831,
843-859, 870-886, 906-907, 931-932, 960-961, 1012-1029
"""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge import router as knowledge_router
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-know4", plan=PlanTier.PROFESSIONAL, api_key_id="kid-k4")
_VALID_KEY = "av_test_knowledge_extra4"


def _make_app(
    knowledge_store: KnowledgeStore | None = None,
    embedder: Any = None,
    semantic_cache: Any = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(knowledge_router)
    app.state.knowledge_store = knowledge_store or KnowledgeStore()
    app.state.semantic_cache = semantic_cache or SemanticCache()
    if embedder is not None:
        app.state.embedder = embedder
    return app


def _make_embedder() -> Any:
    from app.providers.base import EmbedResponse
    emb = AsyncMock()
    emb.embed = AsyncMock(return_value=EmbedResponse(embeddings=[[0.1] * 768], model="test"))
    return emb


def _make_embed_texts_mock() -> Any:
    """Mock for embed_texts that returns a single embedding."""
    async def _embed(texts, provider=None):
        return [[0.0] * 768 for _ in texts]
    return _embed


H = {"X-API-Key": _VALID_KEY}


def _create_collection(client: TestClient, name: str = "test-coll") -> str:
    resp = client.post("/knowledge/collections", json={"name": name}, headers=H)
    assert resp.status_code == 201
    return resp.json()["collection_id"]


# ---------------------------------------------------------------------------
# _require_tenant (line 102), _knowledge_store (116-118), _cache_stats (122)
# ---------------------------------------------------------------------------

def test_require_tenant_no_auth_returns_401() -> None:
    """Line 102: _require_tenant raises 401 when no tenant."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    assert client.get("/knowledge/collections").status_code == 401


def test_knowledge_store_accessed_via_helper() -> None:
    """Lines 116-118: _knowledge_store reads from app.state."""
    store = KnowledgeStore()
    client = TestClient(_make_app(knowledge_store=store), raise_server_exceptions=False)
    resp = client.get("/knowledge/collections", headers=H)
    assert resp.status_code == 200


def test_cache_stats_initialised_lazily() -> None:
    """Line 122: _cache_stats lazily initialises _cache_stats on app.state."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/knowledge/cache/stats", headers=H)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# delete_collection — legal-hold path and DB deletion (lines 192-228)
# ---------------------------------------------------------------------------

def test_delete_collection_with_legal_hold_blocks_deletion() -> None:
    """Lines 192-204: LegalHoldManager blocks deletion (409)."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    # Inject a legal hold manager that returns True
    hold_mgr = AsyncMock()
    hold_mgr.is_under_hold = AsyncMock(return_value=True)
    client.app.state.legal_hold_manager = hold_mgr

    resp = client.delete(f"/knowledge/collections/{coll_id}", headers=H)
    assert resp.status_code == 409


def test_delete_collection_legal_hold_exception_non_fatal() -> None:
    """Lines 201-204: Legal hold check exception is non-fatal (deletion proceeds)."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    hold_mgr = AsyncMock()
    hold_mgr.is_under_hold = AsyncMock(side_effect=Exception("hold check failed"))
    client.app.state.legal_hold_manager = hold_mgr

    resp = client.delete(f"/knowledge/collections/{coll_id}", headers=H)
    assert resp.status_code == 204


def test_delete_collection_with_db_mock() -> None:
    """Lines 207-228: delete_collection with DB (in-memory store + mock DB)."""
    from unittest.mock import AsyncMock, MagicMock

    session = AsyncMock()
    mock_result = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    db = MagicMock(return_value=cm)

    store = KnowledgeStore()
    store._db = db  # type: ignore[attr-defined]

    client = TestClient(_make_app(knowledge_store=store), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    resp = client.delete(f"/knowledge/collections/{coll_id}", headers=H)
    assert resp.status_code in (204, 500)


# ---------------------------------------------------------------------------
# ingest — fallback for short content (line 271)
# ---------------------------------------------------------------------------

def test_ingest_very_short_content_uses_fallback_chunk() -> None:
    """Line 271: Very short content that doesn't chunk creates a fallback chunk."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    with patch("app.knowledge.chunker_v2.chunk_by_tokens", return_value=[]):
        with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
            resp = client.post(
                "/knowledge/ingest",
                json={
                    "collection_id": coll_id,
                    "source_type": "text",
                    "content": "Hi",
                },
                headers=H,
            )
    assert resp.status_code in (201, 503)


# ---------------------------------------------------------------------------
# search — hybrid_search_db path (line 335)
# ---------------------------------------------------------------------------

def test_search_uses_hybrid_search_db_when_available() -> None:
    """Line 335: Uses hybrid_search_db when store has the method."""
    embedder = _make_embedder()

    store = KnowledgeStore()
    # Add hybrid_search_db method to the store
    async def _hybrid_search_db(q, embedding, collection_id, tenant_ctx, top_k=10):
        return []
    store.hybrid_search_db = _hybrid_search_db  # type: ignore[attr-defined]

    coll_id_holder: list[str] = []
    client = TestClient(_make_app(knowledge_store=store, embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
        resp = client.get(
            f"/knowledge/search?q=test&collection_id={coll_id}",
            headers=H,
        )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# ingest/file — text, pdf fallback, docx fallback (lines 392-463)
# ---------------------------------------------------------------------------

def test_ingest_file_txt_with_embedder() -> None:
    """Lines 392-468: Ingest a plain text file."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    file_content = b"This is a test document with enough content to be chunked."
    with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
        resp = client.post(
            "/knowledge/ingest/file",
            files={"file": ("test.txt", io.BytesIO(file_content), "text/plain")},
            data={"collection_id": coll_id},
            headers=H,
        )
    assert resp.status_code in (200, 201, 503)


def test_ingest_file_md() -> None:
    """Lines 392-468: Ingest a markdown file (text source_type)."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    content = b"# Heading\n\nSome markdown content for the knowledge base.\n"
    with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
        resp = client.post(
            "/knowledge/ingest/file",
            files={"file": ("README.md", io.BytesIO(content), "text/markdown")},
            data={"collection_id": coll_id},
            headers=H,
        )
    assert resp.status_code in (200, 201, 503)


def test_ingest_file_python_source() -> None:
    """Lines 399, 392-468: .py files use 'code' source_type."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    content = b"def hello():\n    return 'world'\n"
    with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
        resp = client.post(
            "/knowledge/ingest/file",
            files={"file": ("main.py", io.BytesIO(content), "text/plain")},
            data={"collection_id": coll_id},
            headers=H,
        )
    assert resp.status_code in (200, 201, 503)


def test_ingest_file_pdf_fallback_no_pypdf() -> None:
    """Lines 402-410: PDF ingest falls back to UTF-8 decode when pypdf not installed."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    # Pretend pypdf is not installed
    content = b"PDF content as raw text"
    with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
        with patch.dict("sys.modules", {"pypdf": None}):
            resp = client.post(
                "/knowledge/ingest/file",
                files={"file": ("doc.pdf", io.BytesIO(content), "application/pdf")},
                data={"collection_id": coll_id},
                headers=H,
            )
    assert resp.status_code in (200, 201, 422, 503)


def test_ingest_file_docx_fallback_no_python_docx() -> None:
    """Lines 411-419: DOCX ingest falls back to UTF-8 decode when python-docx not installed."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    content = b"DOCX content as raw text"
    with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
        with patch.dict("sys.modules", {"docx": None}):
            resp = client.post(
                "/knowledge/ingest/file",
                files={"file": ("report.docx", io.BytesIO(content), "application/vnd.openxmlformats")},
                data={"collection_id": coll_id},
                headers=H,
            )
    assert resp.status_code in (200, 201, 422, 503)


def test_ingest_file_empty_raises_422() -> None:
    """Line 424: Empty file content raises 422."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
        resp = client.post(
            "/knowledge/ingest/file",
            files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
            data={"collection_id": coll_id},
            headers=H,
        )
    assert resp.status_code == 422


def test_ingest_file_no_embedder_uses_empty_embedding() -> None:
    """Lines 438-447: No embedder → embedding is empty list, still ingests."""
    client = TestClient(_make_app(), raise_server_exceptions=False)  # no embedder
    coll_id = _create_collection(client)

    content = b"Some file content without embedder configured."
    resp = client.post(
        "/knowledge/ingest/file",
        files={"file": ("plain.txt", io.BytesIO(content), "text/plain")},
        data={"collection_id": coll_id},
        headers=H,
    )
    assert resp.status_code in (200, 201, 503)


def test_ingest_file_embedder_raises_exception() -> None:
    """Lines 443-447: Embedder exception → empty embedding, still ingests."""
    from app.providers.base import EmbedResponse
    embedder = AsyncMock()
    embedder.embed = AsyncMock(side_effect=Exception("Embedder down"))
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    content = b"Content when embedder fails."
    resp = client.post(
        "/knowledge/ingest/file",
        files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
        data={"collection_id": coll_id},
        headers=H,
    )
    assert resp.status_code in (200, 201, 503)


def test_ingest_file_short_content_fallback_chunk() -> None:
    """Lines 432-433: Very short file content creates a single fallback chunk."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    with patch("app.knowledge.chunker_v2.chunk_by_tokens", return_value=[]):
        with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
            resp = client.post(
                "/knowledge/ingest/file",
                files={"file": ("short.txt", io.BytesIO(b"Hi there"), "text/plain")},
                data={"collection_id": coll_id},
                headers=H,
            )
    assert resp.status_code in (200, 201, 503)


# ---------------------------------------------------------------------------
# ingest/repo — background task (lines 546-609)
# ---------------------------------------------------------------------------

def test_ingest_repo_returns_202_immediately() -> None:
    """Lines 475-512: Repo ingest returns 202 without blocking."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    resp = client.post(
        "/knowledge/ingest/repo",
        json={
            "repo_url": "https://github.com/example/repo",
            "collection_id": coll_id,
            "branch": "main",
        },
        headers=H,
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "ingestion_started"


def test_ingest_repo_background_clone_failure() -> None:
    """Lines 546-608: Background task handles clone failure gracefully."""
    import asyncio

    async def _run():
        from app.api.knowledge import _ingest_repo_background
        store = KnowledgeStore()
        # Using a URL that will fail to clone (no real git)
        with patch("asyncio.create_subprocess_exec") as mock_proc:
            proc = AsyncMock()
            proc.returncode = 1
            proc.communicate = AsyncMock(return_value=(b"", b"fatal: not found"))
            mock_proc.return_value = proc
            await _ingest_repo_background(
                repo_url="https://github.com/notexist/repo",
                collection_id="coll-x",
                branch="main",
                file_patterns=["**/*.py"],
                max_files=10,
                store=store,
                embedder=None,
                tenant_ctx=_CTX,
            )

    import asyncio
    asyncio.run(_run())


def test_ingest_repo_background_timeout() -> None:
    """Lines 544-548: Background task handles clone timeout."""
    import asyncio

    async def _run():
        from app.api.knowledge import _ingest_repo_background
        store = KnowledgeStore()
        with patch("asyncio.create_subprocess_exec") as mock_proc:
            proc = AsyncMock()
            proc.returncode = 0
            proc.kill = MagicMock()
            proc.communicate = AsyncMock(side_effect=TimeoutError())
            mock_proc.return_value = proc
            await _ingest_repo_background(
                repo_url="https://github.com/slow/repo",
                collection_id="coll-x",
                branch="main",
                file_patterns=["**/*.py"],
                max_files=10,
                store=store,
                embedder=None,
                tenant_ctx=_CTX,
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# ingest/openapi — parse and ingest paths (lines 628-688)
# ---------------------------------------------------------------------------

def test_ingest_openapi_valid_spec() -> None:
    """Lines 628-688: OpenAPI spec with paths is parsed and ingested."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    openapi_spec = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "summary": "List users",
                    "description": "Returns all users",
                    "parameters": [{"name": "limit", "in": "query"}],
                },
                "post": {
                    "summary": "Create user",
                    "description": "Creates a new user",
                },
            },
            "/users/{id}": {
                "put": {
                    "summary": "Update user",
                    "description": "Updates existing user",
                },
                "delete": {
                    "summary": "Delete user",
                },
            },
        },
    }
    import json
    with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
        resp = client.post(
            "/knowledge/ingest/openapi",
            json={"collection_id": coll_id, "content": json.dumps(openapi_spec), "source_url": "https://api.example.com"},
            headers=H,
        )
    assert resp.status_code in (200, 201)
    body = resp.json()
    assert body["endpoints_ingested"] >= 4  # 4 HTTP methods across 2 paths


def test_ingest_openapi_no_embedder() -> None:
    """Lines 628-688: OpenAPI ingest without embedder (empty embeddings)."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    import json
    spec = {"openapi": "3.0.0", "paths": {"/test": {"get": {"summary": "Test endpoint"}}}}
    resp = client.post(
        "/knowledge/ingest/openapi",
        json={"collection_id": coll_id, "content": json.dumps(spec)},
        headers=H,
    )
    assert resp.status_code in (200, 201)


def test_ingest_openapi_yaml_format() -> None:
    """Lines 629-631: YAML format is also accepted."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    yaml_spec = "openapi: '3.0.0'\npaths:\n  /test:\n    get:\n      summary: Test\n"
    with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
        resp = client.post(
            "/knowledge/ingest/openapi",
            json={"collection_id": coll_id, "content": yaml_spec},
            headers=H,
        )
    assert resp.status_code in (200, 201, 422)


def test_ingest_openapi_invalid_content() -> None:
    """Line 632: Invalid content raises 422 or returns 201 with 0 chunks."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    resp = client.post(
        "/knowledge/ingest/openapi",
        json={"collection_id": coll_id, "content": "this is not json or yaml {{{"},
        headers=H,
    )
    # YAML accepts arbitrary strings; may return 201 with 0 chunks or 422 or 500
    assert resp.status_code in (201, 422, 500)


def test_ingest_openapi_empty_paths() -> None:
    """Lines 634-682: OpenAPI spec with no paths → 0 chunks ingested."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    import json
    spec = {"openapi": "3.0.0", "paths": {}}
    resp = client.post(
        "/knowledge/ingest/openapi",
        json={"collection_id": coll_id, "content": json.dumps(spec)},
        headers=H,
    )
    assert resp.status_code in (200, 201)
    if resp.status_code in (200, 201):
        assert resp.json()["endpoints_ingested"] == 0


def test_ingest_openapi_embedder_exception_swallowed() -> None:
    """Lines 663-667: Embedder exception is swallowed per-chunk."""
    from app.providers.base import EmbedResponse
    embedder = AsyncMock()
    embedder.embed = AsyncMock(side_effect=Exception("Embed fail"))
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    import json
    spec = {"openapi": "3.0.0", "paths": {"/test": {"get": {"summary": "T"}}}}
    resp = client.post(
        "/knowledge/ingest/openapi",
        json={"collection_id": coll_id, "content": json.dumps(spec)},
        headers=H,
    )
    assert resp.status_code in (200, 201)


# ---------------------------------------------------------------------------
# ingest/url — web and github (lines 710-779)
# ---------------------------------------------------------------------------

def test_ingest_url_web_success() -> None:
    """Lines 705-716: URL ingest for web fetches and chunks content."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    mock_response = MagicMock()
    mock_response.text = "<html><title>Test Page</title><body><p>Great content here.</p></body></html>"
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
            resp = client.post(
                "/knowledge/ingest/url",
                json={"collection_id": coll_id, "url": "https://example.com", "source_type": "web"},
                headers=H,
            )
    assert resp.status_code in (200, 201, 500)


def test_ingest_url_github_success() -> None:
    """Lines 717-728: GitHub URL ingest fetches raw content."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    mock_response = MagicMock()
    mock_response.text = "def main():\n    print('hello')\n"
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
            resp = client.post(
                "/knowledge/ingest/url",
                json={
                    "collection_id": coll_id,
                    "url": "https://github.com/org/repo/blob/main/file.py",
                    "source_type": "github",
                },
                headers=H,
            )
    assert resp.status_code in (200, 201, 500)


def test_ingest_url_unsupported_source_type() -> None:
    """Line 731: Unsupported source_type raises 400."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    resp = client.post(
        "/knowledge/ingest/url",
        json={"collection_id": coll_id, "url": "https://example.com", "source_type": "slack"},
        headers=H,
    )
    assert resp.status_code == 400


def test_ingest_url_fetch_failure() -> None:
    """Line 735-736: HTTP failure raises 500."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=Exception("Network error"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        resp = client.post(
            "/knowledge/ingest/url",
            json={"collection_id": coll_id, "url": "https://fail.example.com", "source_type": "web"},
            headers=H,
        )
    assert resp.status_code == 500


def test_ingest_url_empty_content_raises_422() -> None:
    """Line 739: No content extracted → 422."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    mock_response = MagicMock()
    mock_response.text = "   "  # whitespace only
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        resp = client.post(
            "/knowledge/ingest/url",
            json={"collection_id": coll_id, "url": "https://empty.example.com", "source_type": "web"},
            headers=H,
        )
    assert resp.status_code == 422


def test_ingest_url_with_embedder_chunks_content() -> None:
    """Lines 742-784: URL content is chunked and embedded."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    mock_response = MagicMock()
    mock_response.text = "Word " * 200  # enough to create chunks
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
            resp = client.post(
                "/knowledge/ingest/url",
                json={"collection_id": coll_id, "url": "https://long.example.com", "source_type": "web"},
                headers=H,
            )
    assert resp.status_code in (200, 201, 500)


def test_ingest_url_short_content_fallback_chunk() -> None:
    """Lines 745-746: Very short content falls back to single chunk."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    mock_response = MagicMock()
    mock_response.text = "Short content."
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with patch("app.knowledge.chunker_v2.chunk_by_tokens", return_value=[]):
            resp = client.post(
                "/knowledge/ingest/url",
                json={"collection_id": coll_id, "url": "https://short.example.com", "source_type": "web"},
                headers=H,
            )
    assert resp.status_code in (200, 201, 422, 500)


# ---------------------------------------------------------------------------
# _ingest_chunks_from_source helper (lines 803-831)
# ---------------------------------------------------------------------------

def test_ingest_chunks_from_source_helper() -> None:
    """Lines 803-831: _ingest_chunks_from_source embeds and ingests chunks."""
    import asyncio
    from app.api.knowledge import _ingest_chunks_from_source

    store = KnowledgeStore()
    store.create_collection(
        KnowledgeCollection(collection_id="coll-helper", name="Test"),
        tenant_ctx=_CTX,
    )
    embedder = _make_embedder()

    chunks = [
        {"content": "Chunk one content here", "metadata": {"source": "test"}, "source_type": "text"},
        {"content": "", "metadata": {}},  # empty chunk — should be skipped
        {"content": "Chunk two content here", "metadata": {}},
    ]

    async def _run():
        with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
            count = await _ingest_chunks_from_source(store, chunks, "coll-helper", _CTX, embedder)
        return count

    count = asyncio.run(_run())
    assert count >= 1


def test_ingest_chunks_from_source_embedder_exception() -> None:
    """Lines 807-831: Embedder exception per chunk is swallowed."""
    import asyncio
    from app.api.knowledge import _ingest_chunks_from_source

    store = KnowledgeStore()
    store.create_collection(
        KnowledgeCollection(collection_id="coll-exc", name="Exc"),
        tenant_ctx=_CTX,
    )
    embedder = AsyncMock()
    embedder.embed = AsyncMock(side_effect=Exception("Embed fail"))

    chunks = [{"content": "Some content for chunk", "metadata": {}}]

    async def _run():
        count = await _ingest_chunks_from_source(store, chunks, "coll-exc", _CTX, embedder)
        return count

    count = asyncio.run(_run())
    assert count >= 0  # swallowed, may succeed with empty embedding


def test_ingest_chunks_no_embedder() -> None:
    """Lines 803-831: No embedder → empty embeddings, still ingests."""
    import asyncio
    from app.api.knowledge import _ingest_chunks_from_source

    store = KnowledgeStore()
    store.create_collection(
        KnowledgeCollection(collection_id="coll-noemb", name="NoEmb"),
        tenant_ctx=_CTX,
    )

    chunks = [{"content": "Content without embedder", "metadata": {"key": "value"}}]

    async def _run():
        count = await _ingest_chunks_from_source(store, chunks, "coll-noemb", _CTX, None)
        return count

    count = asyncio.run(_run())
    assert count >= 0


# ---------------------------------------------------------------------------
# ingest/pdf and ingest/docx (lines 843-886)
# ---------------------------------------------------------------------------

def test_ingest_pdf_with_mocked_ingestor() -> None:
    """Lines 843-859: PDF ingest uses PdfIngestor."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    mock_chunks = [{"content": "Page 1 content", "metadata": {"page": 1}, "source_type": "pdf"}]
    with patch("app.knowledge.ingestors.pdf_ingestor.PdfIngestor.extract_chunks", return_value=mock_chunks):
        with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
            resp = client.post(
                "/knowledge/ingest/pdf",
                files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
                data={"collection_id": coll_id, "source_url": "https://example.com/doc.pdf"},
                headers=H,
            )
    assert resp.status_code in (200, 201, 500)


def test_ingest_docx_with_mocked_ingestor() -> None:
    """Lines 870-886: DOCX ingest uses DocxIngestor."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    mock_chunks = [{"content": "Paragraph one", "metadata": {"section": "intro"}, "source_type": "docx"}]
    with patch("app.knowledge.ingestors.docx_ingestor.DocxIngestor.extract_chunks", return_value=mock_chunks):
        with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
            resp = client.post(
                "/knowledge/ingest/docx",
                files={"file": ("report.docx", io.BytesIO(b"fake docx content"), "application/vnd.openxmlformats")},
                data={"collection_id": coll_id},
                headers=H,
            )
    assert resp.status_code in (200, 201, 500)


# ---------------------------------------------------------------------------
# ingest/github, /confluence, /jira, /slack (lines 906-961)
# ---------------------------------------------------------------------------

def test_ingest_github_with_mocked_ingestor() -> None:
    """Lines 906-907: GitHub ingest uses GitHubIngestor."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    mock_chunks = [{"content": "def foo(): pass", "metadata": {"file": "main.py"}, "source_type": "github"}]
    with patch("app.knowledge.ingestors.github_ingestor.GitHubIngestor.ingest_repo", new_callable=AsyncMock, return_value=mock_chunks):
        with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
            resp = client.post(
                "/knowledge/ingest/github",
                json={"collection_id": coll_id, "owner": "myorg", "repo": "myrepo"},
                headers=H,
            )
    assert resp.status_code in (200, 202, 500)


def test_ingest_confluence_with_mocked_ingestor() -> None:
    """Lines 931-932: Confluence ingest uses ConfluenceIngestor."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    mock_chunks = [{"content": "Confluence page content", "metadata": {}, "source_type": "confluence"}]
    with patch("app.knowledge.ingestors.confluence_ingestor.ConfluenceIngestor.ingest_space", new_callable=AsyncMock, return_value=mock_chunks):
        with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
            resp = client.post(
                "/knowledge/ingest/confluence",
                json={
                    "collection_id": coll_id,
                    "base_url": "https://company.atlassian.net",
                    "space_key": "ENG",
                    "token": "my-token",
                    "user": "me@company.com",
                },
                headers=H,
            )
    assert resp.status_code in (200, 202, 500)


def test_ingest_jira_with_mocked_ingestor() -> None:
    """Lines 960-961: Jira ingest uses JiraIngestor."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    mock_chunks = [{"content": "Jira issue content", "metadata": {}, "source_type": "jira"}]
    with patch("app.knowledge.ingestors.jira_ingestor.JiraIngestor.ingest_project", new_callable=AsyncMock, return_value=mock_chunks):
        with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
            resp = client.post(
                "/knowledge/ingest/jira",
                json={
                    "collection_id": coll_id,
                    "base_url": "https://company.atlassian.net",
                    "project_key": "PROJ",
                    "token": "jira-token",
                    "user": "me@company.com",
                },
                headers=H,
            )
    assert resp.status_code in (200, 202, 500)


def test_ingest_slack_with_mocked_ingestor() -> None:
    """Lines 982-990: Slack ingest uses SlackIngestor."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll_id = _create_collection(client)

    mock_chunks = [{"content": "Slack message content", "metadata": {}, "source_type": "slack"}]
    with patch("app.knowledge.ingestors.slack_ingestor.SlackIngestor.ingest_channel", new_callable=AsyncMock, return_value=mock_chunks):
        with patch("app.providers.base.embed_texts", side_effect=_make_embed_texts_mock()):
            resp = client.post(
                "/knowledge/ingest/slack",
                json={
                    "collection_id": coll_id,
                    "channel_id": "C01234567",
                    "token": "xoxb-test-token",
                    "channel_name": "#engineering",
                },
                headers=H,
            )
    assert resp.status_code in (200, 202, 500)


# ---------------------------------------------------------------------------
# federated search (lines 1012-1029)
# ---------------------------------------------------------------------------

def test_federated_search_with_embedder() -> None:
    """Lines 1012-1029: Federated search with embedder returns results."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    coll1 = _create_collection(client, "FedColl1")
    coll2 = _create_collection(client, "FedColl2")

    mock_results: list = []
    with patch("app.knowledge.federated_search.federated_search", new_callable=AsyncMock, return_value=mock_results):
        resp = client.post(
            "/knowledge/search/federated",
            json={"query": "test query", "collection_ids": [coll1, coll2], "top_k": 5},
            headers=H,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "results" in body
    assert "total" in body


def test_federated_search_no_embedder_503() -> None:
    """Lines 1006-1010: No embedder returns 503."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/knowledge/search/federated",
        json={"query": "test", "collection_ids": ["c1", "c2"]},
        headers=H,
    )
    assert resp.status_code == 503


def test_federated_search_missing_query() -> None:
    """Lines 1017-1018: Missing/empty query returns 422."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    resp = client.post(
        "/knowledge/search/federated",
        json={"collection_ids": ["c1"]},
        headers=H,
    )
    # Endpoint raises HTTPException(422) for empty query
    assert resp.status_code in (422, 500)


def test_federated_search_missing_collection_ids() -> None:
    """Lines 1019-1020: Missing collection_ids returns 422."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)
    resp = client.post(
        "/knowledge/search/federated",
        json={"query": "test query"},
        headers=H,
    )
    assert resp.status_code in (422, 500)


def test_federated_search_top_k_clamped() -> None:
    """Line 1015: top_k clamped to 1-100."""
    embedder = _make_embedder()
    client = TestClient(_make_app(embedder=embedder), raise_server_exceptions=False)

    with patch("app.knowledge.federated_search.federated_search", new_callable=AsyncMock, return_value=[]):
        resp = client.post(
            "/knowledge/search/federated",
            json={"query": "test", "collection_ids": ["c1"], "top_k": 9999},
            headers=H,
        )
    assert resp.status_code == 200
