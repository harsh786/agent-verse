"""POST /knowledge/ingest/file OCRs PDF pages that have no text layer (P1a-1).

Live P0/P1a: a scanned PDF upload was a 422 "scanned images need OCR" although
Tesseract and poppler are in the image, and a typed letter with a scanned annex
was indexed without the annex (silently). Pages without a text layer are now
rendered and OCR'd; each chunk keeps its page number for citations; OCR is
recorded on the chunk and in the response. With no OCR engine a fully scanned
PDF is an honest 503, a partly scanned one reports the pages it could not read.
"""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge import router as knowledge_router
from app.providers.fake import FakeProvider
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-scan", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_KEY = "av_test_scankey"
_H = {"X-API-Key": _KEY}
_TYPED = "Vessel incident letter VIL-3307: no hull damage was found on the MV Calloway Star."


def _app() -> FastAPI:
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
    return app


def _client() -> tuple[FastAPI, TestClient, str]:
    app = _app()
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/knowledge/collections", json={"name": "scan"}, headers=_H)
    assert r.status_code == 201, r.text
    return app, client, str(r.json()["collection_id"])


def _pdf(*pages: str) -> bytes:
    """``""`` = a page with no text layer (a scanned page)."""
    from fpdf import FPDF

    pdf = FPDF()
    for text in pages:
        pdf.add_page()
        if text:
            pdf.set_font("Helvetica", size=11)
            pdf.multi_cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _chunks(app: FastAPI) -> list[Any]:
    store: KnowledgeStore = app.state.knowledge_store
    return [c for entry in store._data.values() for c in entry.chunks]


def _upload(client: TestClient, cid: str, data: bytes, name: str = "scan.pdf") -> Any:
    return client.post("/knowledge/ingest/file", headers=_H, data={"collection_id": cid},
                       files={"file": (name, io.BytesIO(data), "application/pdf")})


class _Ocr:
    """Renders nothing real: page N's 'image' OCRs to the text given for N."""

    def __init__(self, texts: dict[int, str]) -> None:
        self.texts = texts
        self.rendered: list[int] = []

    def render(self, data: bytes, page_number: int, **_: Any) -> Any:
        from PIL import Image

        self.rendered.append(page_number)
        img = Image.new("L", (40, 40), 255)
        img.info["page"] = page_number
        return img

    async def page(self, img: Any, *, provider: Any = None,
                   vision_fallback: bool = True) -> tuple[str, float, str]:
        return self.texts.get(self.rendered[-1], ""), 0.91, "tesseract"


def _patched(ocr: _Ocr, *, tesseract: bool = True) -> Any:
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("app.ingestion.document_text.tesseract_available",
                              return_value=tesseract))
    stack.enter_context(patch("app.ingestion.document_text.render_pdf_page", ocr.render))
    stack.enter_context(patch("app.ocr.engine.OcrEngine._ocr_page", ocr.page))
    return stack


def test_fully_scanned_pdf_is_ocrd_page_by_page_with_page_citations() -> None:
    app, client, cid = _client()
    ocr = _Ocr({1: "Report CIR-2026-0912 inspector Rohan Deshpande",
                2: "Crane STS-09 hoist brake wear 2.4 mm"})
    with _patched(ocr):
        r = _upload(client, cid, _pdf("", ""))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["pages"] == 2
    assert body["ocr_pages"] == [1, 2]
    assert ocr.rendered == [1, 2]
    by_page = {c.metadata["page"]: c for c in _chunks(app)}
    assert by_page["2"].content == "Crane STS-09 hoist brake wear 2.4 mm"
    assert by_page["1"].metadata["ocr_used"] == "true"
    assert by_page["1"].metadata["ocr_engine"] == "tesseract"


def test_partly_scanned_pdf_ocrs_only_the_pages_without_text() -> None:
    app, client, cid = _client()
    ocr = _Ocr({2: "Fender F-12 replaced by Okonkwo Marine"})
    with _patched(ocr):
        r = _upload(client, cid, _pdf(_TYPED, ""))
    assert r.status_code == 201, r.text
    assert ocr.rendered == [2]
    assert r.json()["ocr_pages"] == [2]
    by_page = {c.metadata["page"]: c for c in _chunks(app)}
    assert "no hull damage" in by_page["1"].content
    assert "ocr_used" not in by_page["1"].metadata
    assert by_page["2"].content == "Fender F-12 replaced by Okonkwo Marine"
    assert by_page["2"].metadata["ocr_used"] == "true"


def test_scanned_pdf_without_any_ocr_engine_is_503_and_stores_nothing() -> None:
    app, client, cid = _client()
    with _patched(_Ocr({1: "x"}), tesseract=False):
        r = _upload(client, cid, _pdf(""))
    assert r.status_code == 503, r.text
    assert "OCR" in r.json()["detail"]
    assert _chunks(app) == []


def test_partly_scanned_pdf_without_ocr_reports_the_unread_pages() -> None:
    app, client, cid = _client()
    with _patched(_Ocr({}), tesseract=False):
        r = _upload(client, cid, _pdf(_TYPED, ""))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["pages_without_text"] == [2]
    assert body["ocr_pages"] == []
    assert "ocr" in body["warnings"][0].lower()
    assert [c.metadata["page"] for c in _chunks(app)] == ["1"]


def test_scan_where_ocr_finds_no_text_is_422() -> None:
    app, client, cid = _client()
    with _patched(_Ocr({1: "   "})):
        r = _upload(client, cid, _pdf(""))
    assert r.status_code == 422, r.text
    assert "no text" in r.json()["detail"].lower()
    assert _chunks(app) == []


def test_too_many_scanned_pages_is_an_honest_422() -> None:
    app, client, cid = _client()
    ocr = _Ocr({})
    with _patched(ocr), patch("app.ingestion.document_text.OCR_MAX_PDF_PAGES", 2):
        r = _upload(client, cid, _pdf("", "", ""))
    assert r.status_code == 422, r.text
    assert "split" in r.json()["detail"].lower()
    assert ocr.rendered == []
    assert _chunks(app) == []


@pytest.mark.parametrize("pages", [("",), (_TYPED, "")])
def test_identical_scanned_upload_is_deduplicated_before_ocr(pages: tuple[str, ...]) -> None:
    app, client, cid = _client()
    data = _pdf(*pages)
    ocr = _Ocr({1: "scanned page one", 2: "scanned page two"})
    with _patched(ocr):
        assert _upload(client, cid, data).status_code == 201
        rendered = list(ocr.rendered)
        again = _upload(client, cid, data)
    assert again.status_code == 201, again.text
    assert again.json()["deduplicated"] is True
    assert ocr.rendered == rendered  # no second OCR pass
