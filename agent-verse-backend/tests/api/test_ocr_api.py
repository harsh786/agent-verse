"""API tests for POST /ocr/extract."""
from __future__ import annotations

import base64
import io
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ocr import router as ocr_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

# Minimal fixture result
_FIXTURE_RESULT = {
    "raw_text": "PERMANENT ACCOUNT NUMBER ABCDE1234F",
    "document_type": "pan_card",
    "fields": {
        "pan_number": {"value": "ABCDE1234F", "confidence": 0.97}
    },
    "engine_used": "tesseract",
    "overall_confidence": 0.92,
    "page_count": 1,
}

_CTX = TenantContext(tenant_id="tid-ocr-test", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_professional_ocrtestkey"


def _make_app() -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(ocr_router)
    return app


@pytest.fixture
def client():
    return TestClient(_make_app(), raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def mock_tool():
    """Patch OcrDocumentTool.execute globally for all tests."""
    mock = AsyncMock(return_value=_FIXTURE_RESULT)
    with patch("app.api.ocr._tool.execute", mock):
        yield mock


def test_extract_with_json_image_base64(client):
    img_b64 = base64.b64encode(b"fake_image").decode()
    response = client.post(
        "/ocr/extract",
        json={"image_base64": img_b64},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["document_type"] == "pan_card"
    assert "pan_number" in data["fields"]


def test_extract_with_json_pdf_base64(client):
    pdf_b64 = base64.b64encode(b"fake_pdf").decode()
    response = client.post(
        "/ocr/extract",
        json={"pdf_base64": pdf_b64},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert response.status_code == 200
    assert response.json()["page_count"] == 1


def test_extract_with_file_upload_image(client):
    fake_img = b"PNG_FAKE_DATA"
    response = client.post(
        "/ocr/extract",
        files={"file": ("test.png", io.BytesIO(fake_img), "image/png")},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert response.status_code == 200


def test_extract_with_file_upload_pdf(client):
    fake_pdf = b"PDF_FAKE_DATA"
    response = client.post(
        "/ocr/extract",
        files={"file": ("doc.pdf", io.BytesIO(fake_pdf), "application/pdf")},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert response.status_code == 200


def test_extract_empty_body_returns_422(client):
    response = client.post(
        "/ocr/extract",
        json={},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert response.status_code == 422


def test_extract_unsupported_mime_returns_422(client):
    response = client.post(
        "/ocr/extract",
        files={"file": ("doc.txt", io.BytesIO(b"text"), "text/plain")},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert response.status_code == 422


def test_extract_response_shape(client):
    img_b64 = base64.b64encode(b"x").decode()
    response = client.post(
        "/ocr/extract",
        json={"image_base64": img_b64},
        headers={"X-API-Key": _VALID_KEY},
    )
    data = response.json()
    assert "raw_text" in data
    assert "document_type" in data
    assert "fields" in data
    assert "engine_used" in data
    assert "overall_confidence" in data
    assert "page_count" in data


# ── OCR-FB-3: per-page provenance reaches the API ────────────────────────────

_MIXED_RESULT = {
    **_FIXTURE_RESULT,
    "engine_used": "mixed",
    "overall_confidence": 0.9,
    "page_count": 2,
    "page_engines": ["tesseract", "llm_vision"],
    "vision_pages": 1,
    "confidence_measured": True,
    "degraded": False,
    "degradation_reason": None,
    "source_format": "pdf",
}


def test_extract_exposes_per_page_provenance(client, mock_tool):
    mock_tool.return_value = _MIXED_RESULT
    response = client.post(
        "/ocr/extract",
        json={"pdf_base64": base64.b64encode(b"fake_pdf").decode()},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["engine_used"] == "mixed"
    assert data["page_engines"] == ["tesseract", "llm_vision"]
    assert data["vision_pages"] == 1
    assert data["confidence_measured"] is True
    assert data["source_format"] == "pdf"
    assert data["degraded"] is False


def test_batch_exposes_per_page_provenance(client, mock_tool):
    mock_tool.return_value = {**_MIXED_RESULT, "confidence_measured": False,
                              "engine_used": "llm_vision", "page_engines": ["llm_vision"],
                              "page_count": 1}
    response = client.post(
        "/ocr/batch",
        json={"documents": [{"image_base64": base64.b64encode(b"img").decode()}]},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert response.status_code == 200
    item = response.json()["results"][0]
    assert item["page_engines"] == ["llm_vision"]
    assert item["vision_pages"] == 1
    assert item["confidence_measured"] is False


def test_old_tool_results_without_provenance_still_serialise(client, mock_tool):
    mock_tool.return_value = _FIXTURE_RESULT  # no provenance keys at all
    response = client.post(
        "/ocr/extract",
        json={"image_base64": base64.b64encode(b"img").decode()},
        headers={"X-API-Key": _VALID_KEY},
    )
    data = response.json()
    assert data["page_engines"] == [] and data["vision_pages"] == 0
    assert data["confidence_measured"] is True


@pytest.mark.asyncio
async def test_extract_document_tool_output_carries_provenance():
    """The agent-callable tool's dict carries the same fields (real tool, fake engine)."""
    from app.ocr.models import DocumentType, OcrResult
    from app.tools.ocr_tool import OcrDocumentTool

    engine = AsyncMock()
    engine.extract = AsyncMock(return_value=OcrResult(
        raw_text="a\n\nb", document_type=DocumentType.GENERAL, engine_used="mixed",
        overall_confidence=0.9, page_count=2, page_engines=["tesseract", "llm_vision"],
        vision_pages=1, confidence_measured=True,
    ))
    out = await OcrDocumentTool(ocr_engine=engine).execute(
        image_base64=base64.b64encode(b"img").decode()
    )
    assert out["page_engines"] == ["tesseract", "llm_vision"]
    assert out["vision_pages"] == 1
    assert out["confidence_measured"] is True
