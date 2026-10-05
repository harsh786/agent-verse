"""POST /knowledge/ingest/file accepts ZIP archives (P1a-3).

Live P0/P1a: every .zip was a 415 "unsupported binary file". An archive is now
one document whose chunks cite ``<archive>/<member path>``; members are parsed
with the same extractors as a direct upload (nested archives expanded), skipped
members are reported with the reason, and a zip bomb is refused (422 ratio,
413 size) with nothing stored.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge import router as knowledge_router
from app.providers.fake import FakeProvider
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-zip", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_KEY = "av_test_zipkey"
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
    r = client.post("/knowledge/collections", json={"name": "zip"}, headers=_H)
    assert r.status_code == 201, r.text
    return app, client, str(r.json()["collection_id"])


def _zip(files: dict[str, bytes | str], method: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _docx(text: str) -> bytes:
    import docx

    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _chunks(app: FastAPI) -> list[Any]:
    store: KnowledgeStore = app.state.knowledge_store
    return [c for entry in store._data.values() for c in entry.chunks]


def _upload(client: TestClient, cid: str, data: bytes, name: str = "bundle.zip") -> Any:
    return client.post("/knowledge/ingest/file", headers=_H, data={"collection_id": cid},
                       files={"file": (name, io.BytesIO(data), "application/zip")})


def test_nested_mixed_archive_is_one_document_citing_each_member() -> None:
    app, client, cid = _client()
    inner = _zip({"notes/history.md": "# History\n\nCommissioned in 1987 by Brackwater."})
    data = _zip({
        "handbook/overtime.docx": _docx("Overtime is paid at 1.75 times the base rate."),
        "contacts.csv": "role,name\nDuty harbour master,Ines Valdivia\n",
        "archive/2025.zip": inner,
        "__MACOSX/handbook/._overtime.docx": b"\x00\x05\x16\x07",
        "legacy/roster.doc": b"\xd0\xcf\x11\xe0" + bytes(64),
    })
    r = _upload(client, cid, data)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["archive"]["members_indexed"] == [
        "handbook/overtime.docx", "contacts.csv", "archive/2025.zip/notes/history.md"]
    skipped = {s["name"]: s["reason"] for s in body["archive"]["members_skipped"]}
    assert list(skipped) == ["legacy/roster.doc"] and ".doc" in skipped["legacy/roster.doc"]
    chunks = _chunks(app)
    assert body["chunks_created"] == len(chunks) == 3
    assert len({c.document_id for c in chunks}) == 1 == len({body["document_id"]})
    by_member = {c.metadata["archive_member"]: c for c in chunks}
    assert "1.75 times" in by_member["handbook/overtime.docx"].content
    assert "Ines Valdivia" in by_member["contacts.csv"].content
    history = by_member["archive/2025.zip/notes/history.md"]
    assert history.metadata["source_file"] == "bundle.zip/archive/2025.zip/notes/history.md"
    assert history.metadata["archive"] == "bundle.zip"
    assert history.metadata["ext"] == "md"
    assert by_member["contacts.csv"].metadata["source_type"] == "table"


def test_zip_bomb_is_refused_and_nothing_is_stored() -> None:
    app, client, cid = _client()
    r = _upload(client, cid, _zip({"payload.txt": bytes(16 * 1024 * 1024)}))
    assert r.status_code == 422, r.text
    assert "zip bomb" in r.json()["detail"]
    assert _chunks(app) == []


def test_archive_expanding_past_the_total_limit_is_413() -> None:
    from unittest.mock import patch

    app, client, cid = _client()
    data = _zip({f"part-{i}.txt": bytes(range(256)) * 512 for i in range(4)},
                method=zipfile.ZIP_STORED)  # 4 x 128 KiB
    with patch.dict("os.environ", {"KNOWLEDGE_ARCHIVE_MAX_TOTAL_BYTES": str(300 * 1024)}):
        r = _upload(client, cid, data)
    assert r.status_code == 413, r.text
    assert "uncompressed" in r.json()["detail"]
    assert _chunks(app) == []


def test_archive_with_nothing_indexable_is_422_with_the_reasons() -> None:
    app, client, cid = _client()
    r = _upload(client, cid, _zip({"old.doc": b"\xd0\xcf\x11\xe0" + bytes(64)}))
    assert r.status_code == 422, r.text
    assert "no indexable files" in r.json()["detail"] and "old.doc" in r.json()["detail"]
    assert _chunks(app) == []


def test_corrupt_archive_is_422() -> None:
    _, client, cid = _client()
    r = _upload(client, cid, b"PK\x03\x04 this is not really a zip")
    assert r.status_code == 422, r.text
    assert "zip" in r.json()["detail"].lower()


def test_identical_archive_is_deduplicated() -> None:
    _, client, cid = _client()
    data = _zip({"a.txt": "Berth 7 dredging finishes in March."})
    assert _upload(client, cid, data).status_code == 201
    again = _upload(client, cid, data)
    assert again.status_code == 201 and again.json()["deduplicated"] is True
