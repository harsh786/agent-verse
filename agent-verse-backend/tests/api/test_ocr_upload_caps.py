"""a10-F243-05: OCR uploads are bounded.

``/ocr/extract`` read a multipart upload whole (``await file.read()``, no cap)
and base64-inflated it; JSON base64 documents had no size check either, and the
request-body middleware did not cover ``/ocr``. Now: the middleware bounds every
``/ocr/`` body before the route reads it, and the route enforces the exact
per-document cap (``OCR_MAX_UPLOAD_BYTES``) — chunked for uploads, from the
encoded length for base64 — with a 413.
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.integrations.body_limit import (
    PublicIngressBodyLimitMiddleware,
    cap_for_path,
    ocr_body_cap,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="t-ocr-cap", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_KEY = "av_professional_ocrcap"
_H = {"X-API-Key": _KEY}
_LIMIT = 1000

_OK = {
    "raw_text": "hello", "document_type": "general", "fields": {},
    "engine_used": "llm_vision", "overall_confidence": 0.85, "page_count": 1,
}


@pytest.fixture
def tool() -> Any:
    mock = AsyncMock(return_value=_OK)
    with (
        patch("app.api.ocr._tool.execute", mock),
        patch("app.api.ocr._ocr_max_bytes", lambda: _LIMIT),
    ):
        yield mock


@pytest.fixture
def client() -> TestClient:
    from app.api.ocr import router

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_multipart_upload_over_the_cap_is_413(client: TestClient, tool: Any) -> None:
    resp = client.post(
        "/ocr/extract", files={"file": ("big.png", b"x" * (_LIMIT + 1), "image/png")}, headers=_H
    )
    assert resp.status_code == 413
    tool.assert_not_awaited()


def test_multipart_upload_at_the_cap_is_accepted(client: TestClient, tool: Any) -> None:
    resp = client.post(
        "/ocr/extract", files={"file": ("ok.png", b"x" * _LIMIT, "image/png")}, headers=_H
    )
    assert resp.status_code == 200, resp.text


def test_json_base64_over_the_cap_is_413_before_decoding(client: TestClient, tool: Any) -> None:
    big = base64.b64encode(b"x" * (_LIMIT + 3)).decode()
    resp = client.post("/ocr/extract", json={"pdf_base64": big}, headers=_H)
    assert resp.status_code == 413
    tool.assert_not_awaited()


def test_json_base64_at_the_cap_is_accepted(client: TestClient, tool: Any) -> None:
    ok = base64.b64encode(b"x" * _LIMIT).decode()
    resp = client.post("/ocr/extract", json={"image_base64": ok}, headers=_H)
    assert resp.status_code == 200, resp.text


def test_batch_oversized_document_is_an_item_error(client: TestClient, tool: Any) -> None:
    small = base64.b64encode(b"x" * 10).decode()
    big = base64.b64encode(b"x" * (_LIMIT + 3)).decode()
    resp = client.post(
        "/ocr/batch",
        json={"documents": [{"image_base64": small}, {"image_base64": big}]},
        headers=_H,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"][0] is None
    assert "OCR limit" in body["errors"][1]
    assert tool.await_count == 1


# ── middleware ───────────────────────────────────────────────────────────────


def test_ocr_paths_get_the_ocr_cap() -> None:
    assert cap_for_path("/ocr/extract", 1024, ocr_cap=5000) == 5000
    assert cap_for_path("/ocr/batch", 1024, ocr_cap=5000) == 5000
    assert cap_for_path("/goals", 1024, ocr_cap=5000) is None


def test_ocr_body_cap_allows_one_base64_document_plus_envelope() -> None:
    assert ocr_body_cap(3_000_000) == 4_000_000 + 1_048_576


def test_middleware_refuses_an_oversized_ocr_body_before_the_route_runs() -> None:
    app = FastAPI()

    @app.post("/ocr/extract")
    async def extract(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    app.add_middleware(PublicIngressBodyLimitMiddleware, ocr_max_body_bytes=2048)
    c = TestClient(app)
    assert c.post("/ocr/extract", content=b"x" * 2048).status_code == 200
    assert c.post("/ocr/extract", content=b"x" * 2049).status_code == 413


def test_middleware_default_ocr_cap_follows_the_setting() -> None:
    from app.core.config import get_settings

    mw = PublicIngressBodyLimitMiddleware(FastAPI())
    assert mw.ocr_max_body_bytes == ocr_body_cap(int(get_settings().ocr_max_upload_bytes))
