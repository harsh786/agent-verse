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
