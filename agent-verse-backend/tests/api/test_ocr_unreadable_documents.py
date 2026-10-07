"""/ocr/extract and /ocr/batch never answer a plain 200 with empty text for a
document nothing could be read from, and the size limit is the documented one.

* corrupt / truncated / encrypted / zero-page PDF, undecodable image → 422 with
  the reason (resubmitting can never succeed);
* readable PDF the renderer failed on (or no renderer), every page failed → 502;
* partly readable → 200 ``degraded`` with ``failed_pages``;
* /ocr/batch: the same per item, in ``errors`` + ``error_status``.

The real tool + engine run; only the renderer (poppler is not installed on dev
machines — simulated with the exception pdf2image raises) and the per-page OCR
call are stubbed. Size: the agent tool used to cap documents at a hardcoded
10 MiB below the documented 25 MiB ``OCR_MAX_UPLOAD_BYTES`` (an 11 MiB PDF was a
422 "10 MB"); every 413 now names the real limit.
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.integrations.body_limit import PublicIngressBodyLimitMiddleware, ocr_body_cap
from app.ocr.engine import OcrEngine, _note_page_failure
from app.ocr.models import DocumentType, OcrResult
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from tests.ocr._pdf_inputs import (
    corrupt_pdf,
    encrypted_pdf,
    page_image,
    png_bytes,
    poppler_refuses,
    truncated_pdf,
    valid_pdf,
    zero_page_pdf,
)

_CTX = TenantContext(tenant_id="t-ocr-unread", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_KEY = "av_professional_ocrunread"
_H = {"X-API-Key": _KEY}
_MIB = 1024 * 1024


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _make_client(*, ocr_body_bytes: int | None = None) -> TestClient:
    from app.api.ocr import router

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(PublicIngressBodyLimitMiddleware, ocr_max_body_bytes=ocr_body_bytes)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client() -> TestClient:
    return _make_client()


@pytest.fixture(autouse=True)
def _no_provider() -> Any:
    with patch("app.api.llm_access.tenant_llm_provider", AsyncMock(return_value=None)):
        yield


async def _read_page(_engine: Any, img: Any, **_: Any) -> tuple[str, float, str]:
    return f"page {img.info.get('page', 1)} invoice total 42", 0.9, "tesseract"


@pytest.fixture
def poppler_refuses_input() -> Any:
    """Poppler cannot open the document (what pdfinfo answers for every broken
    input here); pages are OCR'd by a stub."""
    with (
        patch("app.ocr.engine.pdf_page_count", poppler_refuses),
        patch.object(OcrEngine, "_ocr_page", _read_page),
    ):
        yield


def _renders(count: int, *, fail: set[int] | frozenset[int] = frozenset()) -> Any:
    def _render(_p: Any, n: int, *, dpi: int) -> Any:
        if n in fail:
            raise RuntimeError(f"pdftoppm: page {n} is damaged")
        return page_image(n)

    return (
        patch("app.ocr.engine.pdf_page_count", lambda _p: count),
        patch("app.ocr.engine.render_pdf_page_image", _render),
    )


# ── /ocr/extract: unopenable input is a 422 with the reason ──────────────────


@pytest.mark.parametrize(
    ("data", "words"),
    [
        (corrupt_pdf(), "corrupt or truncated"),
        (truncated_pdf(), "corrupt or truncated"),
        (encrypted_pdf(), "encrypted (password-protected)"),
        (zero_page_pdf(), "no pages"),
    ],
    ids=["corrupt", "truncated", "encrypted", "zero-pages"],
)
def test_unopenable_pdf_json_is_422_with_the_reason(
    client: TestClient, poppler_refuses_input: Any, data: bytes, words: str
) -> None:
    resp = client.post("/ocr/extract", json={"pdf_base64": _b64(data)}, headers=_H)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail.startswith("The document cannot be OCR'd: ")
    assert words in detail


@pytest.mark.parametrize(
    "data", [truncated_pdf(), encrypted_pdf()], ids=["truncated", "encrypted"]
)
def test_unopenable_pdf_upload_is_422(
    client: TestClient, poppler_refuses_input: Any, data: bytes
) -> None:
    resp = client.post(
        "/ocr/extract", files={"file": ("scan.pdf", data, "application/pdf")}, headers=_H
    )
    assert resp.status_code == 422, resp.text


def test_undecodable_image_upload_is_422(client: TestClient) -> None:
    resp = client.post(
        "/ocr/extract",
        files={"file": ("photo.png", png_bytes()[:40], "image/png")},
        headers=_H,
    )
    # PIL opens a truncated PNG lazily (the header is intact); it is decoded
    # up front so it is refused instead of reaching vision OCR with no image.
    assert resp.status_code == 422, resp.text
    assert "truncated" in resp.json()["detail"]
    resp = client.post(
        "/ocr/extract", files={"file": ("x.png", b"not an image", "image/png")}, headers=_H
    )
    assert resp.status_code == 422
    assert "image could not be decoded" in resp.json()["detail"]


# ── /ocr/extract: readable input the engine failed on is a 502 ───────────────


def test_valid_pdf_without_a_renderer_is_502(client: TestClient) -> None:
    with patch.dict("sys.modules", {"pdf2image": None}):
        resp = client.post("/ocr/extract", json={"pdf_base64": _b64(valid_pdf())}, headers=_H)
    assert resp.status_code == 502, resp.text
    assert "not installed" in resp.json()["detail"]


def test_every_page_failing_is_502(client: TestClient) -> None:
    async def _fails(_engine: Any, _img: Any, **_: Any) -> tuple[str, float, str]:
        _note_page_failure("LLM vision OCR failed (TimeoutError)")
        return "", 0.0, "llm_vision"

    count, render = _renders(2)
    with count, render, patch.object(OcrEngine, "_ocr_page", _fails):
        resp = client.post("/ocr/extract", json={"pdf_base64": _b64(valid_pdf(2))}, headers=_H)
    assert resp.status_code == 502
    assert "TimeoutError" in resp.json()["detail"]


def test_zero_pages_without_a_reason_is_never_a_plain_200(client: TestClient) -> None:
    """Defensive: a tool result with no page and no failure kind is a 502."""
    empty = {
        "raw_text": "", "document_type": "general", "fields": {},
        "engine_used": "tesseract", "overall_confidence": 0.0, "page_count": 0,
    }
    with patch("app.api.ocr._tool.execute", AsyncMock(return_value=empty)):
        resp = client.post("/ocr/extract", json={"pdf_base64": _b64(b"%PDF-")}, headers=_H)
    assert resp.status_code == 502


# ── /ocr/extract: readable documents ─────────────────────────────────────────


def test_partly_readable_pdf_is_200_degraded_with_failed_pages(client: TestClient) -> None:
    count, render = _renders(3, fail={2})
    with count, render, patch.object(OcrEngine, "_ocr_page", _read_page):
        resp = client.post("/ocr/extract", json={"pdf_base64": _b64(valid_pdf())}, headers=_H)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page_count"] == 3
    assert body["degraded"] is True
    assert body["failed_pages"] == [2]
    assert "page could not be rendered" in body["degradation_reason"]
    assert "page 1 invoice" in body["raw_text"] and "page 3 invoice" in body["raw_text"]


def test_valid_pdf_is_200(client: TestClient) -> None:
    count, render = _renders(3)
    with count, render, patch.object(OcrEngine, "_ocr_page", _read_page):
        resp = client.post(
            "/ocr/extract",
            files={"file": ("scan.pdf", valid_pdf(), "application/pdf")},
            headers=_H,
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["degraded"] is False
    assert resp.json()["page_count"] == 3


# ── /ocr/batch: per-item reason and status ───────────────────────────────────


def test_batch_reports_each_unreadable_item_with_its_reason(client: TestClient) -> None:
    good = valid_pdf(1)
    broken = {truncated_pdf(), encrypted_pdf(), zero_page_pdf()}

    def _count(path: Any) -> int:
        with open(path, "rb") as fh:
            data = fh.read()
        if data in broken:
            return poppler_refuses(path)
        return 1

    with (
        patch("app.ocr.engine.pdf_page_count", _count),
        patch("app.ocr.engine.render_pdf_page_image", lambda _p, n, *, dpi: page_image(n)),
        patch.object(OcrEngine, "_ocr_page", _read_page),
    ):
        resp = client.post(
            "/ocr/batch",
            json={
                "documents": [
                    {"pdf_base64": _b64(good)},
                    {"pdf_base64": _b64(truncated_pdf())},
                    {"pdf_base64": _b64(encrypted_pdf())},
                    {"pdf_base64": _b64(zero_page_pdf())},
                    {"image_base64": _b64(b"not an image")},
                ]
            },
            headers=_H,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 5 and body["succeeded"] == 1 and body["failed"] == 4
    assert body["results"][0]["raw_text"].startswith("page 1 invoice")
    assert body["results"][1:] == [None, None, None, None]
    assert body["errors"][0] is None
    assert "corrupt or truncated" in body["errors"][1]
    assert "encrypted" in body["errors"][2]
    assert "no pages" in body["errors"][3]
    assert "image could not be decoded" in body["errors"][4]
    assert body["error_status"] == [None, 422, 422, 422, 422]


def test_batch_item_without_a_renderer_is_a_502_item(client: TestClient) -> None:
    with patch.dict("sys.modules", {"pdf2image": None}):
        resp = client.post(
            "/ocr/batch", json={"documents": [{"pdf_base64": _b64(valid_pdf(1))}]}, headers=_H
        )
    body = resp.json()
    assert body["error_status"] == [502]
    assert "not installed" in body["errors"][0]


# ── the size limit: OCR_MAX_UPLOAD_BYTES (25 MiB) everywhere ─────────────────


def _ok_result() -> OcrResult:
    return OcrResult(
        raw_text="ok", document_type=DocumentType.GENERAL, overall_confidence=0.9, page_count=1
    )


@pytest.fixture
def engine_ok() -> Any:
    """The real API + tool (decode and size checks); the engine is stubbed."""
    mock = AsyncMock(return_value=_ok_result())
    with patch("app.api.ocr._engine.extract", mock):
        yield mock


def test_an_11_mib_pdf_is_accepted_not_refused_at_10_mib(
    client: TestClient, engine_ok: Any
) -> None:
    data = b"%PDF-1.4\n" + b"x" * (11 * _MIB)
    resp = client.post("/ocr/extract", json={"pdf_base64": _b64(data)}, headers=_H)
    assert resp.status_code == 200, resp.text
    assert engine_ok.await_args.kwargs["pdf_bytes"] == data


def test_upload_at_exactly_the_documented_25_mib_limit_is_accepted(
    client: TestClient, engine_ok: Any
) -> None:
    resp = client.post(
        "/ocr/extract",
        files={"file": ("big.pdf", b"x" * (25 * _MIB), "application/pdf")},
        headers=_H,
    )
    assert resp.status_code == 200, resp.text


def test_upload_one_byte_over_25_mib_is_413_naming_the_real_limit(
    client: TestClient, engine_ok: Any
) -> None:
    resp = client.post(
        "/ocr/extract",
        files={"file": ("big.pdf", b"x" * (25 * _MIB + 1), "application/pdf")},
        headers=_H,
    )
    assert resp.status_code == 413
    detail = resp.json()["detail"]
    assert "25 MiB (26214400 bytes)" in detail
    assert "OCR_MAX_UPLOAD_BYTES" in detail
    engine_ok.assert_not_awaited()


def test_base64_at_25_mib_passes_the_body_limit_middleware(
    client: TestClient, engine_ok: Any
) -> None:
    resp = client.post(
        "/ocr/extract", json={"image_base64": _b64(b"x" * (25 * _MIB))}, headers=_H
    )
    assert resp.status_code == 200, resp.text


def test_base64_over_25_mib_is_413_naming_the_real_limit(
    client: TestClient, engine_ok: Any
) -> None:
    resp = client.post(
        "/ocr/extract", json={"image_base64": _b64(b"x" * (25 * _MIB + 3))}, headers=_H
    )
    assert resp.status_code == 413
    assert "25 MiB (26214400 bytes)" in resp.json()["detail"]


def test_body_limit_413_states_the_document_limit_not_only_the_body_cap(engine_ok: Any) -> None:
    client = _make_client(ocr_body_bytes=ocr_body_cap(1000 * 1000))
    cap = ocr_body_cap(1000 * 1000)
    resp = client.post("/ocr/extract", content=b"x" * (cap + 1), headers=_H)
    assert resp.status_code == 413
    detail = resp.json()["detail"]
    assert f"exceeds {cap} bytes" in detail
    assert "1000000 bytes per document (OCR_MAX_UPLOAD_BYTES)" in detail
    assert "/ocr/batch" in detail


def test_batch_documents_share_one_documents_body(engine_ok: Any) -> None:
    """Documented: a batch's documents together are bounded like one document."""
    client = _make_client(ocr_body_bytes=ocr_body_cap(_MIB))
    doc = {"image_base64": _b64(b"x" * (_MIB * 3 // 4))}
    resp = client.post("/ocr/batch", json={"documents": [doc, doc, doc]}, headers=_H)
    assert resp.status_code == 413
    assert "/ocr/batch" in resp.json()["detail"]
