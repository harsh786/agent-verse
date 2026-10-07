"""a10-F243-03: pages the OCR engine failed on are reported, not joined in as "".

``_llm_vision_ocr`` returned ``("", 0.0, "llm_vision")`` when no provider could be
resolved or the vision call failed, and ``extract`` joined every page's text
regardless — so ``/ocr/extract`` answered 200 with empty / partial text and no
sign anything went wrong. Failed pages are now listed (``failed_pages``), mark
the result ``degraded`` with the reasons, and a document none of whose pages
could be read is a 502 at the API.
"""

from __future__ import annotations

import base64
import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.ocr.engine import OcrEngine
from app.providers.base import CompletionResponse
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware


class _Vision:
    """Fails on the pages whose (1-based) number is in ``fail``."""

    def __init__(self, fail: set[int]) -> None:
        self.fail = fail

    def supports_vision(self) -> bool:
        return True

    async def complete(self, request: Any) -> CompletionResponse:
        page = int(str(request.messages[0].image_data).removeprefix("page-"))
        if page in self.fail:
            raise ConnectionError("upstream reset")
        return CompletionResponse(content=f"text of page {page}", model="m")


def _pages(n: int) -> Any:
    @asynccontextmanager
    async def _open(self: Any, **_: Any) -> AsyncIterator[list[Any]]:
        def _loader(page: int) -> Any:
            img = Image.new("L", (32, 32), 255)
            img.info["page"] = page
            return img

        yield [(lambda p=p: _loader(p)) for p in range(1, n + 1)]

    return _open


@pytest.fixture(autouse=True)
def _vision_only(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv("OCR_TESSERACT_ENABLED", raising=False)  # default: vision only
    with (
        patch("app.ocr.engine._ocr_model", lambda: "m"),
        patch("app.ocr.engine._ocr_fallback_models", lambda _m: []),
        patch("app.ocr.engine.vision_reserve_usd", lambda *a, **k: 0.0),
        # Tag each page's request so the fake provider can fail chosen pages.
        patch.object(
            OcrEngine, "_image_to_base64", staticmethod(lambda img: f"page-{img.info['page']}")
        ),
    ):
        yield


async def _extract(n: int, fail: set[int]) -> Any:
    with patch.object(OcrEngine, "_open_pages", _pages(n)):
        return await OcrEngine().extract(
            pdf_bytes=b"%PDF-", provider=_Vision(fail), extract_fields=False
        )


async def test_all_pages_read_is_not_degraded() -> None:
    res = await _extract(3, set())
    assert res.failed_pages == []
    assert res.empty_pages == []
    assert res.degraded is False
    assert "text of page 2" in res.raw_text


async def test_partially_failed_document_lists_failed_pages_and_reason() -> None:
    res = await _extract(3, {2})
    assert res.failed_pages == [2]
    assert res.empty_pages == [2]
    assert res.degraded is True
    assert "1 of 3 page(s) could not be read (pages 2)" in (res.degradation_reason or "")
    assert "ConnectionError" in (res.degradation_reason or "")
    # The pages that were read keep their text.
    assert "text of page 1" in res.raw_text and "text of page 3" in res.raw_text


async def test_fully_failed_document_says_nothing_could_be_read() -> None:
    res = await _extract(2, {1, 2})
    assert res.failed_pages == [1, 2]
    assert res.degraded is True
    assert (res.degradation_reason or "").startswith("no text could be read from any of the 2")


async def test_no_resolvable_provider_is_a_recorded_failure() -> None:
    def _boom() -> Any:
        raise RuntimeError("no LLM configured")

    with (
        patch.object(OcrEngine, "_open_pages", _pages(1)),
        patch("app.providers.registry.resolve_provider", _boom),
    ):
        res = await OcrEngine().extract(pdf_bytes=b"%PDF-", extract_fields=False)
    assert res.failed_pages == [1]
    assert "no LLM provider" in (res.degradation_reason or "")


# ── API ──────────────────────────────────────────────────────────────────────

_CTX = TenantContext(tenant_id="t-ocr-fail", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_KEY = "av_professional_ocrfail"


def _client(tool_result: dict[str, Any]) -> TestClient:
    from unittest.mock import AsyncMock

    from app.api.ocr import router

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    patcher = patch("app.api.ocr._tool.execute", AsyncMock(return_value=tool_result))
    patcher.start()
    client = TestClient(app, raise_server_exceptions=False)
    client.patcher = patcher  # type: ignore[attr-defined]
    return client


def _result(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "raw_text": "", "document_type": "general", "fields": {},
        "engine_used": "llm_vision", "overall_confidence": 0.0, "page_count": 2,
        "page_engines": ["llm_vision", "llm_vision"], "vision_pages": 0,
        "confidence_measured": True, "degraded": True,
        "degradation_reason": "no text could be read from any of the 2 page(s): x",
        "empty_pages": [1, 2], "failed_pages": [1, 2],
    }
    base.update(over)
    return base


@pytest.fixture
def _png() -> str:
    buf = io.BytesIO()
    Image.new("L", (4, 4), 255).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_api_unread_document_is_502_not_200_empty(_png: str) -> None:
    client = _client(_result())
    try:
        resp = client.post("/ocr/extract", json={"image_base64": _png}, headers={"X-API-Key": _KEY})
    finally:
        client.patcher.stop()  # type: ignore[attr-defined]
    assert resp.status_code == 502
    assert "could not read the document" in resp.json()["detail"]


def test_api_partial_document_is_200_with_failed_pages(_png: str) -> None:
    client = _client(_result(
        raw_text="page one", failed_pages=[2], empty_pages=[2],
        degradation_reason="1 of 2 page(s) could not be read (pages 2): x",
    ))
    try:
        resp = client.post("/ocr/extract", json={"image_base64": _png}, headers={"X-API-Key": _KEY})
    finally:
        client.patcher.stop()  # type: ignore[attr-defined]
    assert resp.status_code == 200
    body = resp.json()
    assert body["failed_pages"] == [2]
    assert body["empty_pages"] == [2]
    assert body["degraded"] is True
