"""Tests for OcrDocumentTool."""
from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ocr.models import DocumentType, ExtractedField, OcrResult
from app.tools.ocr_tool import OcrDocumentTool


def _make_result(doc_type: DocumentType = DocumentType.GENERAL) -> OcrResult:
    return OcrResult(
        raw_text="PERMANENT ACCOUNT NUMBER ABCDE1234F",
        document_type=doc_type,
        fields={
            "pan_number": ExtractedField(name="pan_number", value="ABCDE1234F", confidence=0.97)
        },
        engine_used="tesseract",
        overall_confidence=0.92,
        page_count=1,
    )


@pytest.mark.asyncio
async def test_execute_with_image_base64():
    mock_engine = MagicMock()
    mock_engine.extract = AsyncMock(return_value=_make_result(DocumentType.PAN_CARD))
    tool = OcrDocumentTool(ocr_engine=mock_engine)

    img_b64 = base64.b64encode(b"fake_image_data").decode()
    result = await tool.execute(image_base64=img_b64)

    assert result["document_type"] == "pan_card"
    assert "pan_number" in result["fields"]
    assert result["fields"]["pan_number"]["value"] == "ABCDE1234F"
    mock_engine.extract.assert_called_once_with(
        image_bytes=b"fake_image_data", pdf_bytes=None, provider=None
    )


@pytest.mark.asyncio
async def test_execute_with_pdf_base64():
    mock_engine = MagicMock()
    mock_engine.extract = AsyncMock(return_value=_make_result())
    tool = OcrDocumentTool(ocr_engine=mock_engine)

    pdf_b64 = base64.b64encode(b"fake_pdf_data").decode()
    result = await tool.execute(pdf_base64=pdf_b64)

    mock_engine.extract.assert_called_once_with(
        image_bytes=None, pdf_bytes=b"fake_pdf_data", provider=None
    )


@pytest.mark.asyncio
async def test_execute_with_file_path(tmp_path):
    fake_img = tmp_path / "test.png"
    fake_img.write_bytes(b"fake_png_data")

    mock_engine = MagicMock()
    mock_engine.extract = AsyncMock(return_value=_make_result())
    tool = OcrDocumentTool(ocr_engine=mock_engine)

    result = await tool.execute(file_path=str(fake_img))

    mock_engine.extract.assert_called_once_with(
        image_bytes=b"fake_png_data", pdf_bytes=None, provider=None
    )


@pytest.mark.asyncio
async def test_execute_with_file_path_pdf(tmp_path):
    fake_pdf = tmp_path / "doc.pdf"
    fake_pdf.write_bytes(b"fake_pdf_data")

    mock_engine = MagicMock()
    mock_engine.extract = AsyncMock(return_value=_make_result())
    tool = OcrDocumentTool(ocr_engine=mock_engine)

    await tool.execute(file_path=str(fake_pdf))

    mock_engine.extract.assert_called_once_with(
        image_bytes=None, pdf_bytes=b"fake_pdf_data", provider=None
    )


@pytest.mark.asyncio
async def test_execute_universal_document_base64_routes_through_extract_any():
    """WS-14: a generic document routes through the universal extract_any path
    and surfaces source_format + degradation metadata."""
    mock_engine = MagicMock()
    mock_engine.extract = AsyncMock()  # must NOT be used on the universal path
    degraded = OcrResult(
        raw_text="",
        document_type=DocumentType.GENERAL,
        degraded=True,
        degradation_reason="office document conversion requires LibreOffice",
        source_format="office",
    )
    mock_engine.extract_any = AsyncMock(return_value=degraded)
    tool = OcrDocumentTool(ocr_engine=mock_engine)

    doc_b64 = base64.b64encode(b"PK\x03\x04fakedocx").decode()
    result = await tool.execute(document_base64=doc_b64, filename="report.docx")

    mock_engine.extract_any.assert_called_once()
    mock_engine.extract.assert_not_called()
    assert result["source_format"] == "office"
    assert result["degraded"] is True
    assert "LibreOffice" in result["degradation_reason"]


@pytest.mark.asyncio
async def test_execute_raises_on_empty_input():
    tool = OcrDocumentTool()
    with pytest.raises(ValueError, match="must be provided"):
        await tool.execute()


@pytest.mark.asyncio
async def test_execute_raises_on_invalid_base64():
    tool = OcrDocumentTool()
    with pytest.raises(ValueError, match="Invalid base64"):
        await tool.execute(image_base64="not-valid-base64!!!")


@pytest.mark.asyncio
async def test_execute_raises_on_oversized_input():
    tool = OcrDocumentTool()
    big_data = b"x" * (11 * 1024 * 1024)  # 11 MB
    big_b64 = base64.b64encode(big_data).decode()
    with pytest.raises(ValueError, match="maximum allowed size"):
        await tool.execute(image_base64=big_b64)


@pytest.mark.asyncio
async def test_result_shape():
    mock_engine = MagicMock()
    mock_engine.extract = AsyncMock(return_value=_make_result(DocumentType.PAN_CARD))
    tool = OcrDocumentTool(ocr_engine=mock_engine)

    img_b64 = base64.b64encode(b"x").decode()
    result = await tool.execute(image_base64=img_b64)

    assert "raw_text" in result
    assert "document_type" in result
    assert "fields" in result
    assert "engine_used" in result
    assert "overall_confidence" in result
    assert "page_count" in result
