"""Re-uploading an edited file replaces it instead of adding a copy (P1a-4).

Live P0/P1a (KB-COMPLEX-LIFECYCLE): every upload minted a random document id,
so uploading an amended agreement under the same name stored a second copy
(9 -> 17 chunks) and kept serving the superseded clause. An upload's document
id is now stable per (tenant, collection, file name): a new version replaces the
old one in the same transaction, unchanged chunks keep their chunk ids, and a
pre-existing copy stored under a random id (before this fix) is removed too.
A document under legal hold is never replaced. ``replace_existing=false`` keeps
both copies.
"""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge import router as knowledge_router
from app.providers.fake import FakeProvider
from app.rag.models import Chunk
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-rep", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_KEY = "av_test_repkey"
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
    r = client.post("/knowledge/collections", json={"name": "rep"}, headers=_H)
    assert r.status_code == 201, r.text
    return app, client, str(r.json()["collection_id"])


def _chunks(app: FastAPI) -> list[Chunk]:
    store: KnowledgeStore = app.state.knowledge_store
    return [c for entry in store._data.values() for c in entry.chunks]


def _upload(client: TestClient, cid: str, name: str, text: str, **form: str) -> Any:
    return client.post("/knowledge/ingest/file", headers=_H,
                       data={"collection_id": cid, **form},
                       files={"file": (name, io.BytesIO(text.encode()), "text/markdown")})


def _agreement(notice_days: int) -> str:
    filler = " ".join(f"Clause {i}: the vendor keeps records of shipment {i} for audit."
                      for i in range(160))
    return (f"# Vendor agreement\n\n## Liability\n\n{filler}\n\n## Termination\n\n"
            f"Either party may terminate with a written notice period of {notice_days} days.\n")


def test_edited_upload_with_the_same_name_replaces_the_document() -> None:
    app, client, cid = _client()
    first = _upload(client, cid, "agreement.md", _agreement(75))
    assert first.status_code == 201, first.text
    v1_chunks = first.json()["chunks_created"]
    assert first.json()["replaced"] is False
    edited = _upload(client, cid, "agreement.md", _agreement(120))
    assert edited.status_code == 201, edited.text
    body = edited.json()
    assert body["document_id"] == first.json()["document_id"]
    assert body["replaced"] is True
    chunks = _chunks(app)
    assert len(chunks) == body["chunks_created"] == v1_chunks
    text = "\n".join(c.content for c in chunks)
    assert "notice period of 120 days" in text
    assert "notice period of 75 days" not in text


def test_unchanged_chunks_keep_their_chunk_ids_across_an_edit() -> None:
    app, client, cid = _client()
    _upload(client, cid, "agreement.md", _agreement(75))
    before = {c.content: c.chunk_id for c in _chunks(app)}
    assert len(before) >= 3
    _upload(client, cid, "agreement.md", _agreement(120))
    after = {c.content: c.chunk_id for c in _chunks(app)}
    unchanged = set(before) & set(after)
    assert len(unchanged) == len(before) - 1  # only the termination chunk changed
    assert all(before[c] == after[c] for c in unchanged)


def test_different_file_names_stay_separate_documents() -> None:
    app, client, cid = _client()
    a = _upload(client, cid, "agreement-a.md", _agreement(75)).json()
    b = _upload(client, cid, "agreement-b.md", _agreement(120)).json()
    assert a["document_id"] != b["document_id"]
    assert len({c.document_id for c in _chunks(app)}) == 2


def test_replace_existing_false_keeps_both_versions() -> None:
    app, client, cid = _client()
    a = _upload(client, cid, "agreement.md", _agreement(75)).json()
    b = _upload(client, cid, "agreement.md", _agreement(120), replace_existing="false").json()
    assert a["document_id"] != b["document_id"] and b["replaced"] is False
    assert len({c.document_id for c in _chunks(app)}) == 2


def test_a_legacy_copy_under_a_random_id_is_removed_on_replace() -> None:
    app, client, cid = _client()
    store: KnowledgeStore = app.state.knowledge_store
    store.ingest_chunk(
        Chunk(document_id="legacy0000000000000000000000000a", content="old notice 75 days",
              embedding=[0.1] * 768, chunk_index=0,
              metadata={"source_file": "agreement.md", "ext": "md"}),
        collection_id=cid, tenant_ctx=_CTX)
    r = _upload(client, cid, "agreement.md", _agreement(120))
    assert r.status_code == 201, r.text
    assert r.json()["replaced"] is True
    assert r.json()["replaced_document_ids"] == ["legacy0000000000000000000000000a"]
    assert {c.document_id for c in _chunks(app)} == {r.json()["document_id"]}


def test_a_document_under_legal_hold_is_not_replaced() -> None:
    app, client, cid = _client()
    first = _upload(client, cid, "agreement.md", _agreement(75)).json()
    held = first["document_id"]
    app.state.legal_hold_manager = AsyncMock()
    app.state.legal_hold_manager.is_under_hold = AsyncMock(
        side_effect=lambda resource_id, tenant_id: resource_id == held)
    r = _upload(client, cid, "agreement.md", _agreement(120))
    assert r.status_code == 409, r.text
    assert "legal hold" in r.json()["detail"]
    assert "notice period of 75 days" in "\n".join(c.content for c in _chunks(app))


def test_identical_reupload_is_still_deduplicated() -> None:
    app, client, cid = _client()
    _upload(client, cid, "agreement.md", _agreement(75))
    again = _upload(client, cid, "agreement.md", _agreement(75)).json()
    assert again["deduplicated"] is True and again["chunks_created"] == 0
    assert len({c.document_id for c in _chunks(app)}) == 1
