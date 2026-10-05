"""D1 — a same-name re-upload replaces only a document of the same source.

Owner decision (2026-10-05): replace stays the default, but the match is scoped
to the same collection AND the same source. Uploads are the source ``upload``;
connector documents carry their ``source_id``. A ``scan.pdf`` from source X must
never replace one from source Y or from an upload (and an upload never replaces
a connector's, a repository's or a scraped page's document of that name).
``replace_existing=false`` keeps both; a legal hold blocks the replacement with
an error that says so.
"""

from __future__ import annotations

import asyncio
import io
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge import router as knowledge_router
from app.providers.fake import FakeProvider
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.semantic_cache import SemanticCache
from app.rag.store import UPLOAD_SOURCE, KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-d1", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_KEY = "av_test_d1key"
_H = {"X-API-Key": _KEY}


def _client() -> tuple[FastAPI, TestClient, str]:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(knowledge_router)
    app.state.knowledge_store = KnowledgeStore()
    app.state.semantic_cache = SemanticCache()
    app.state.embedder = FakeProvider(embed_dim=768)
    app.state.llm_provider = None
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/knowledge/collections", json={"name": "d1"}, headers=_H)
    assert r.status_code == 201, r.text
    return app, client, str(r.json()["collection_id"])


def _store(app: FastAPI) -> KnowledgeStore:
    store: KnowledgeStore = app.state.knowledge_store
    return store


def _docs(app: FastAPI) -> set[str]:
    return {c.document_id for e in _store(app)._data.values() for c in e.chunks}


def _upload(client: TestClient, cid: str, name: str, text: str, **form: str) -> Any:
    return client.post(
        "/knowledge/ingest/file",
        headers=_H,
        data={"collection_id": cid, **form},
        files={"file": (name, io.BytesIO(text.encode()), "text/markdown")},
    )


def _seed(app: FastAPI, cid: str, doc_id: str, metadata: dict[str, str]) -> None:
    _store(app).ingest_chunk(
        Chunk(
            document_id=doc_id,
            content=f"content of {doc_id}",
            embedding=[0.1] * 768,
            chunk_index=0,
            metadata=metadata,
        ),
        collection_id=cid,
        tenant_ctx=_CTX,
    )


def test_an_upload_never_replaces_a_connector_document_of_the_same_name() -> None:
    app, client, cid = _client()
    _seed(app, cid, "drive-doc", {"source_id": "src-x", "doc_title": "notes.md",
                                  "source_file": "notes.md", "source_type": "gdrive"})
    r = _upload(client, cid, "notes.md", "# Notes\n\nuploaded version\n")
    assert r.status_code == 201, r.text
    assert r.json()["replaced"] is False and r.json()["replaced_document_ids"] == []
    assert _docs(app) == {"drive-doc", r.json()["document_id"]}


def test_an_upload_never_replaces_a_repository_or_scraped_document() -> None:
    app, client, cid = _client()
    _seed(app, cid, "repo-doc", {"source_file": "notes.md", "repo_url": "https://g/x",
                                 "source_type": "github"})
    _seed(app, cid, "web-doc", {"doc_title": "notes.md", "source_url": "https://e.test/n",
                                "source_type": "web"})
    r = _upload(client, cid, "notes.md", "# Notes\n\nuploaded version\n")
    assert r.status_code == 201, r.text
    assert r.json()["replaced"] is False
    assert _docs(app) == {"repo-doc", "web-doc", r.json()["document_id"]}


def test_uploaded_chunks_record_the_upload_source() -> None:
    app, client, cid = _client()
    r = _upload(client, cid, "notes.md", "# Notes\n\nv1\n")
    assert r.status_code == 201, r.text
    chunks = [c for e in _store(app)._data.values() for c in e.chunks]
    assert chunks and all(c.metadata.get("ingest_source") == UPLOAD_SOURCE for c in chunks)
    assert all("source_id" not in c.metadata for c in chunks)


def test_a_reupload_still_replaces_the_previous_upload() -> None:
    app, client, cid = _client()
    _seed(app, cid, "drive-doc", {"source_id": "src-x", "doc_title": "notes.md"})
    v1 = _upload(client, cid, "notes.md", "# Notes\n\nv1 text\n").json()
    v2 = _upload(client, cid, "notes.md", "# Notes\n\nv2 text\n").json()
    assert v2["replaced"] is True and v2["document_id"] == v1["document_id"]
    assert _docs(app) == {"drive-doc", v1["document_id"]}


def test_replace_existing_false_keeps_both_uploads() -> None:
    app, client, cid = _client()
    a = _upload(client, cid, "notes.md", "# Notes\n\nv1 text\n").json()
    b = _upload(client, cid, "notes.md", "# Notes\n\nv2 text\n", replace_existing="false").json()
    assert b["replaced"] is False and a["document_id"] != b["document_id"]
    assert _docs(app) == {a["document_id"], b["document_id"]}


def test_legal_hold_blocks_the_replacement_with_a_clear_error() -> None:
    app, client, cid = _client()
    held = _upload(client, cid, "notes.md", "# Notes\n\nv1 text\n").json()["document_id"]
    app.state.legal_hold_manager = AsyncMock()
    app.state.legal_hold_manager.is_under_hold = AsyncMock(
        side_effect=lambda resource_id, tenant_id: resource_id == held
    )
    r = _upload(client, cid, "notes.md", "# Notes\n\nv2 text\n")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "legal hold" in detail and "notes.md" in detail and "replaced" in detail
    assert "v1 text" in "\n".join(c.content for e in _store(app)._data.values() for c in e.chunks)


def test_a_held_connector_document_of_the_same_name_does_not_block_an_upload() -> None:
    app, client, cid = _client()
    _seed(app, cid, "drive-doc", {"source_id": "src-x", "doc_title": "notes.md"})
    app.state.legal_hold_manager = AsyncMock()
    app.state.legal_hold_manager.is_under_hold = AsyncMock(
        side_effect=lambda resource_id, tenant_id: resource_id == "drive-doc"
    )
    r = _upload(client, cid, "notes.md", "# Notes\n\nuploaded\n")
    assert r.status_code == 201, r.text
    assert "drive-doc" in _docs(app)


def test_store_scopes_same_name_matches_to_one_source() -> None:
    store = KnowledgeStore()
    cid = store.create_collection(KnowledgeCollection(name="c"), tenant_ctx=_CTX)
    for doc, meta in (
        ("x-1", {"source_id": "src-x", "doc_title": "scan.pdf"}),
        ("y-1", {"source_id": "src-y", "doc_title": "scan.pdf"}),
        ("up-1", {"ingest_source": "upload", "source_file": "scan.pdf", "ext": "pdf"}),
        ("legacy", {"source_file": "scan.pdf", "ext": "pdf"}),
        ("x-other", {"source_id": "src-x", "doc_title": "other.pdf"}),
    ):
        store.ingest_chunk(
            Chunk(document_id=doc, content=doc, embedding=[0.1] * 768, chunk_index=0,
                  metadata=meta),
            collection_id=cid, tenant_ctx=_CTX,
        )

    def same(source: str) -> list[str]:
        return asyncio.run(store.same_name_document_ids_async(
            tenant_ctx=_CTX, collection_id=cid, name="scan.pdf", source=source))

    assert same("src-x") == ["x-1"]
    assert same("src-y") == ["y-1"]
    assert same(UPLOAD_SOURCE) == ["legacy", "up-1"]
    assert same("src-z") == []
