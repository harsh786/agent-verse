"""OcrEngine text-only / no-vision-fallback modes used by knowledge image uploads.

Knowledge ingestion only needs the OCR text: ``extract_fields=False`` skips the
classifier + (LLM) structured-field extraction. ``vision_fallback=False`` must
never reach for the system default provider (which may be the canned
FakeProvider) when the caller has no vision-capable provider.
"""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import patch

import pytest

from app.ocr.engine import OcrEngine

pytestmark = pytest.mark.asyncio


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buf, format="PNG")
    return buf.getvalue()


async def test_no_vision_fallback_never_resolves_a_provider() -> None:
    def _must_not_resolve() -> Any:
        raise AssertionError("resolve_provider must not be called")

    with (
        patch.dict("sys.modules", {"pytesseract": None}),
        patch("app.providers.registry.resolve_provider", _must_not_resolve),
    ):
        result = await OcrEngine().extract(
            image_bytes=_png(), extract_fields=False, vision_fallback=False
        )
    assert result.raw_text == ""
    assert result.engine_used == "tesseract"


async def test_text_only_mode_skips_field_extraction() -> None:
    async def _page(self: Any, img: Any, *, provider: Any = None, vision_fallback: bool = True):
        return "INVOICE NO 99 GSTIN 27AAPFU0939F1ZV", 0.9, "tesseract"

    with (
        patch.object(OcrEngine, "_ocr_page", _page),
        patch("app.ocr.engine.get_extractor", side_effect=AssertionError("no extraction")),
    ):
        result = await OcrEngine().extract(image_bytes=_png(), extract_fields=False)
    assert result.raw_text.startswith("INVOICE NO 99")
    assert result.fields == {}
