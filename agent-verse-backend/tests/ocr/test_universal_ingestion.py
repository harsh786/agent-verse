"""WS-6: universal OCR ingestion — any format → image → OCR, with honest
degradation metadata when a format cannot be rasterized.

These tests assert the *routing + provenance* contract of ``extract_any`` without
requiring Tesseract/poppler/LibreOffice to be installed: the detected
``source_format`` is always recorded, and any format that cannot be rasterized
sets ``degraded`` + a human-readable ``degradation_reason`` (never a silent drop).
"""

from __future__ import annotations

import base64

import pytest

from app.ocr.engine import OcrEngine, detect_ocr_format

# Real 1x1 PNG bytes.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
_ZIP_OFFICE = b"PK\x03\x04" + b"\x00" * 40  # docx/xlsx are zip containers


def test_detect_format_by_magic_and_extension() -> None:
    assert detect_ocr_format(_PNG, None, "scan.png") == "image"
    assert detect_ocr_format(_PDF, "application/pdf", None) == "pdf"
    assert detect_ocr_format(b"plain text here", "text/plain", "a.txt") == "text"
    assert detect_ocr_format(_ZIP_OFFICE, None, "report.docx") == "office"
    assert detect_ocr_format(b"\x00\x01\x02random", "application/x-thing", "x.bin") == "unsupported"


@pytest.mark.asyncio
async def test_extract_any_image_routes_and_records_format() -> None:
    result = await OcrEngine().extract_any(_PNG, filename="scan.png")
    assert result.source_format == "image"
    # A 1x1 image yields little/no text but must not be flagged as a degradation
    # of the *format* (the format was handled); it simply has no text.
    assert result.degradation_reason is None or "format" not in (result.degradation_reason or "")


@pytest.mark.asyncio
async def test_extract_any_unsupported_format_degrades_honestly() -> None:
    result = await OcrEngine().extract_any(
        b"\x00\x01\x02not a document", content_type="application/x-thing", filename="x.bin"
    )
    assert result.source_format == "unsupported"
    assert result.degraded is True
    assert result.degradation_reason
    assert result.raw_text == ""


@pytest.mark.asyncio
async def test_extract_any_text_input_is_not_ocr_eligible() -> None:
    result = await OcrEngine().extract_any(b"already readable text", filename="notes.txt")
    assert result.source_format == "text"
    assert result.degraded is True
    assert "text" in result.degradation_reason.lower()


@pytest.mark.asyncio
async def test_extract_any_office_without_converter_degrades(monkeypatch) -> None:
    # Force the "no LibreOffice available" branch regardless of the host.
    import app.ocr.engine as engine_mod

    monkeypatch.setattr(engine_mod.shutil, "which", lambda _name: None)
    result = await OcrEngine().extract_any(_ZIP_OFFICE, filename="report.docx")
    assert result.source_format == "office"
    assert result.degraded is True
    assert "libreoffice" in result.degradation_reason.lower()
