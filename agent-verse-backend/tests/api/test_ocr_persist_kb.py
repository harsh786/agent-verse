"""WS-13: standalone /ocr/extract → optional persist-to-KB.

The standalone OCR extract can now also index its text into the KnowledgeStore
with ``source_type=ocr`` provenance, deduped against the one store via
``exists_by_hash``.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ocr import router as ocr_router
from app.rag.models import KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-ocr-kb", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_professional_ocrkbkey"

_FIXTURE_RESULT = {
    "raw_text": "INVOICE 2026 total due 1234.56 for consulting services rendered.",
    "document_type": "invoice",
    "fields": {"total": {"value": "1234.56", "confidence": 0.95}},
    "engine_used": "tesseract",
    "overall_confidence": 0.9,
    "page_count": 1,
}


def _make_app() -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(ocr_router)

    store = KnowledgeStore()
    store.create_collection(
        KnowledgeCollection(name="ocr-col", collection_id="ocr-1"), tenant_ctx=_CTX
    )
    app.state.knowledge_store = store
    app.state.embedder = object()
    return app


@pytest.fixture
def app() -> FastAPI:
    return _make_app()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _mocks():
    mock = AsyncMock(return_value=_FIXTURE_RESULT)

    async def _fake_embed(texts, embedder):
        return [[0.1] * 768 for _ in texts]

    with (
        patch("app.api.ocr._tool.execute", mock),
        patch("app.api.knowledge._embed_texts_or_http", _fake_embed),
    ):
        yield


def _img_body(**extra) -> dict:
    return {"image_base64": base64.b64encode(b"fake").decode(), **extra}


def test_persist_false_does_not_touch_kb(client: TestClient, app: FastAPI):
    resp = client.post("/ocr/extract", json=_img_body(), headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["kb_persisted"] is False
    assert body["kb_chunks_ingested"] == 0


def test_persist_indexes_with_ocr_provenance(client: TestClient, app: FastAPI):
    resp = client.post(
        "/ocr/extract",
        json=_img_body(persist_to_kb=True, collection_id="ocr-1"),
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kb_persisted"] is True
    assert body["kb_deduplicated"] is False
    assert body["kb_chunks_ingested"] >= 1
    assert body["kb_collection_id"] == "ocr-1"

    # The indexed chunk carries source_type=ocr + ocr_used provenance.
    store: KnowledgeStore = app.state.knowledge_store
    col_store = store._data[("tid-ocr-kb", "ocr-1")]
    assert col_store.chunks
    meta = col_store.chunks[0].metadata
    assert meta["source_type"] == "ocr"
    assert meta["ocr_used"] == "true"
    assert meta["doc_content_hash"]


def test_persist_dedups_on_readd(client: TestClient, app: FastAPI):
    payload = _img_body(persist_to_kb=True, collection_id="ocr-1")
    first = client.post("/ocr/extract", json=payload, headers={"X-API-Key": _VALID_KEY})
    second = client.post("/ocr/extract", json=payload, headers={"X-API-Key": _VALID_KEY})
    assert first.json()["kb_deduplicated"] is False
    assert first.json()["kb_chunks_ingested"] >= 1
    assert second.json()["kb_persisted"] is True
    assert second.json()["kb_deduplicated"] is True
    assert second.json()["kb_chunks_ingested"] == 0


def test_persist_requires_collection_id(client: TestClient):
    resp = client.post(
        "/ocr/extract",
        json=_img_body(persist_to_kb=True),
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422
    assert "collection_id" in resp.json()["detail"]
