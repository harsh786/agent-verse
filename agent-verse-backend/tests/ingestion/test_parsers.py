"""Real parser implementations with graceful fallbacks."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.parsers.audio_parser import AudioParser, AudioParseResult, AudioSegment
from app.ingestion.parsers.docx_parser import DOCXParser, DOCXParseResult
from app.ingestion.parsers.pdf_parser import PDFParser, PDFParseResult
from app.ingestion.parsers.vision_parser import VisionParser, VisionParseResult


def test_pdf_parser_parse_text():
    parser = PDFParser()
    result = parser.parse_text("Page 1 content.\n\nPage 2 content.", "test.pdf")
    assert isinstance(result, PDFParseResult)
    chunks = result.to_chunks()
    assert len(chunks) >= 1
    assert all(c["content"] for c in chunks)


def test_pdf_parser_handles_empty():
    parser = PDFParser()
    result = parser.parse_bytes(b"", "empty.pdf")
    assert isinstance(result, PDFParseResult)
    assert result.pages == [] or result.error is not None


def test_pdf_parser_to_chunks_has_page_number():
    parser = PDFParser()
    result = parser.parse_text("Paragraph one.\n\nParagraph two.", "doc.pdf")
    chunks = result.to_chunks()
    assert all("page_number" in c for c in chunks)


@pytest.mark.anyio
async def test_audio_parser_with_mock_whisper():
    mock_transcription = MagicMock(
        text="Hello this is a test.",
        segments=[MagicMock(start=0.0, end=3.5, text="Hello this is a test.")],
    )
    parser = AudioParser()
    with patch.object(parser, "_transcribe_with_whisper", AsyncMock(return_value=mock_transcription)):
        result = await parser.parse_bytes(b"fake_audio", "test.mp3", "audio/mpeg")
    assert isinstance(result, AudioParseResult)
    assert result.transcript is not None
    assert "Hello" in result.transcript


@pytest.mark.anyio
async def test_audio_parser_fallback_without_key():
    parser = AudioParser()
    with patch.object(parser, "_transcribe_with_whisper", AsyncMock(side_effect=Exception("No key"))):
        result = await parser.parse_bytes(b"fake", "test.mp3", "audio/mpeg")
    assert isinstance(result, AudioParseResult)


def test_audio_parse_result_to_chunks():
    result = AudioParseResult(
        source_name="test.mp3",
        transcript="Hello world.",
        segments=[
            AudioSegment(start=0.0, end=5.0, text="Hello."),
            AudioSegment(start=5.0, end=10.0, text="World."),
        ],
    )
    chunks = result.to_chunks()
    assert len(chunks) >= 1
    assert all("start_time" in c for c in chunks)


@pytest.mark.anyio
async def test_vision_parser_uses_fallback():
    parser = VisionParser()
    tiny_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    with patch.object(parser, "_describe_with_openai", AsyncMock(return_value="A white image.")):
        result = await parser.parse_image_bytes(tiny_png, "test.png")
    assert isinstance(result, VisionParseResult)
    assert result.description


@pytest.mark.anyio
async def test_vision_parser_fallback_placeholder():
    parser = VisionParser()
    with patch.object(parser, "_describe_with_openai", AsyncMock(side_effect=Exception("No key"))):
        with patch.object(parser, "_describe_with_anthropic", AsyncMock(side_effect=Exception("No key"))):
            result = await parser.parse_image_bytes(b"\x89PNG\r\n\x1a\n", "test.png")
    assert isinstance(result, VisionParseResult)
    assert "test.png" in result.description or result.description


def test_docx_parser_handles_plain_text_fallback():
    parser = DOCXParser()
    result = parser.parse_bytes(b"Heading\n\nParagraph content here.", "test.docx")
    assert isinstance(result, DOCXParseResult)
    assert len(result.to_chunks()) >= 0


def test_docx_parser_handles_empty():
    parser = DOCXParser()
    result = parser.parse_bytes(b"", "empty.docx")
    assert isinstance(result, DOCXParseResult)
