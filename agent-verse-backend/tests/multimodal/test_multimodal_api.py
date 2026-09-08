"""API-level tests for /multimodal (D-11 response labeling, D-23 code/table)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.multimodal import router as multimodal_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-mm", plan=PlanTier.PROFESSIONAL, api_key_id="kid-mm")
_KEY = "ak_mm_test_key"
_HEADERS = {"X-API-Key": _KEY}


def _make_app() -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(multimodal_router)
    return app


def test_ingest_code_via_api() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/multimodal/ingest",
        json={
            "modality": "code",
            "content": "def f():\n    return 1\n",
            "language": "python",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["span_count"] >= 1
    assert data["spans"][0]["modality"] == "code"


def test_ingest_table_via_api() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/multimodal/ingest",
        json={"modality": "table", "content": "a,b\n1,2\n"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["spans"][0]["modality"] == "table"


def test_ingest_text_response_labels_direct_text_embed() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/multimodal/ingest",
        json={"modality": "text", "content": "hello"},
        headers=_HEADERS,
    )
    data = resp.json()
    assert data["embedding_strategy"] == "direct_text_embed"


def test_ingest_image_response_never_claims_real_multimodal_embedding() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/multimodal/ingest",
        json={"modality": "image", "base64_data": "iVBORw0KGgo="},
        headers=_HEADERS,
    )
    data = resp.json()
    assert data["embedding_strategy"] == "caption_then_text_embed"
    assert data["real_multimodal_embedding"] is False
    assert data.get("extractor_model")
