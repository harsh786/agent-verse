"""Tests for OcrEngine — Tesseract primary, LLM vision fallback."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from app.ocr.engine import CONFIDENCE_THRESHOLD, OcrEngine
from app.ocr.models import DocumentType


def _make_pil_image() -> Image.Image:
    return Image.new("RGB", (100, 50), color=(255, 255, 255))


def _make_tess_data(conf_values: list[int], texts: list[str]) -> dict:
    return {"conf": conf_values, "text": texts}


def _mock_pytesseract(tess_data: dict) -> MagicMock:
    """Build a fake pytesseract module with image_to_data pre-configured."""
    mock_tess = MagicMock()
    mock_tess.image_to_data.return_value = tess_data
    mock_tess.Output.DICT = "dict"
    return mock_tess


async def test_extract_returns_empty_result_when_no_input():
    engine = OcrEngine()
    result = await engine.extract()
    assert result.raw_text == ""
    assert result.page_count == 0
    assert result.document_type == DocumentType.GENERAL


async def test_extract_uses_tesseract_when_high_confidence():
    engine = OcrEngine()
    img = _make_pil_image()

    high_conf_data = _make_tess_data([90, 95, 88], ["PERMANENT", "ACCOUNT", "NUMBER"])
    mock_tess = _mock_pytesseract(high_conf_data)

    with (
        patch("app.ocr.engine.OcrEngine._to_images", return_value=[img]),
        patch.dict("sys.modules", {"pytesseract": mock_tess}),
    ):
        result = await engine.extract(image_bytes=b"fake")

    assert result.engine_used == "tesseract"
    assert result.overall_confidence >= CONFIDENCE_THRESHOLD


async def test_extract_falls_back_to_llm_when_low_confidence():
    engine = OcrEngine()
    img = _make_pil_image()

    low_conf_data = _make_tess_data([20, 15, 10], ["blurry", "text", "here"])
    mock_tess = _mock_pytesseract(low_conf_data)

    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(
        return_value=MagicMock(content="PERMANENT ACCOUNT NUMBER ABCDE1234F")
    )

    with (
        patch("app.ocr.engine.OcrEngine._to_images", return_value=[img]),
        patch.dict("sys.modules", {"pytesseract": mock_tess}),
    ):
        result = await engine.extract(image_bytes=b"fake", provider=mock_provider)

    assert result.engine_used == "llm_vision"


async def test_extract_falls_back_when_pytesseract_not_installed():
    engine = OcrEngine()
    img = _make_pil_image()

    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(
        return_value=MagicMock(content="Invoice No: INV-001\nTotal: Rs. 500\nGST number")
    )

    with (
        patch("app.ocr.engine.OcrEngine._to_images", return_value=[img]),
        patch.dict("sys.modules", {"pytesseract": None}),
    ):
        result = await engine.extract(image_bytes=b"fake", provider=mock_provider)

    assert result.engine_used == "llm_vision"


async def test_extract_classifies_document_type():
    engine = OcrEngine()
    img = _make_pil_image()

    high_conf_data = _make_tess_data(
        [90, 90, 90, 90, 90, 90],
        ["PERMANENT", "ACCOUNT", "NUMBER", "ABCDE1234F", "Income", "Tax"],
    )
    mock_tess = _mock_pytesseract(high_conf_data)

    with (
        patch("app.ocr.engine.OcrEngine._to_images", return_value=[img]),
        patch.dict("sys.modules", {"pytesseract": mock_tess}),
    ):
        result = await engine.extract(image_bytes=b"fake")

    assert result.document_type == DocumentType.PAN_CARD


async def test_extract_multi_page_pdf():
    engine = OcrEngine()
    imgs = [_make_pil_image(), _make_pil_image()]

    high_conf_data = _make_tess_data([80, 80], ["page", "text"])
    mock_tess = _mock_pytesseract(high_conf_data)

    with (
        patch("app.ocr.engine.OcrEngine._to_images", return_value=imgs),
        patch.dict("sys.modules", {"pytesseract": mock_tess}),
    ):
        result = await engine.extract(pdf_bytes=b"fake_pdf")

    assert result.page_count == 2


async def test_to_images_returns_empty_for_import_error():
    engine = OcrEngine()
    with patch.dict("sys.modules", {"pdf2image": None}):
        pages = engine._to_images(image_bytes=None, pdf_bytes=b"fake")
    assert pages == []


def test_preprocess_image_returns_image():
    """_preprocess_image should return an image object (not raise)."""
    engine = OcrEngine()
    img = _make_pil_image()
    result = engine._preprocess_image(img)
    assert result is not None
