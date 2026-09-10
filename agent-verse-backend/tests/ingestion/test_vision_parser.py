# tests/ingestion/test_vision_parser.py
"""Vision parser must describe images using GPT-4V or Claude Vision."""
from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest

from app.ingestion.parsers.vision_parser import VisionParser, VisionParseResult


@pytest.fixture
def tiny_png_bytes():
    """1x1 white PNG for testing."""
    b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI6QAAAABJRU5ErkJggg=="
    return base64.b64decode(b64)


async def test_vision_parser_describes_image(tiny_png_bytes):
    """VisionParser must call vision model and return description."""
    parser = VisionParser()
    with patch.object(parser, "_describe_with_openai",
                      AsyncMock(return_value="A white 1x1 pixel image.")):
        result = await parser.parse_image_bytes(
            image_bytes=tiny_png_bytes,
            source_name="test.png",
            prompt="Describe this image in detail.",
        )
    assert isinstance(result, VisionParseResult)
    assert result.description
    assert len(result.description) > 0


async def test_vision_parser_returns_structured_chunks(tiny_png_bytes):
    """VisionParseResult must produce chunks with image metadata."""
    parser = VisionParser()
    with patch.object(parser, "_describe_with_openai",
                      AsyncMock(return_value="White pixel image")):
        result = await parser.parse_image_bytes(tiny_png_bytes, "test.png")
    chunks = result.to_chunks()
    assert len(chunks) == 1
    assert chunks[0]["content_type"] == "image"
    assert chunks[0]["source_name"] == "test.png"


async def test_vision_parser_fallback_on_no_key(tiny_png_bytes):
    """Vision parser must degrade gracefully without API key."""
    parser = VisionParser()
    with patch.object(parser, "_describe_with_openai",
                      AsyncMock(side_effect=Exception("No API key"))), \
         patch.object(parser, "_describe_with_anthropic",
                      AsyncMock(side_effect=Exception("No API key"))):
        result = await parser.parse_image_bytes(tiny_png_bytes, "test.png")
    assert isinstance(result, VisionParseResult)
    assert result.description or result.error


async def test_vision_parser_uses_anthropic_fallback(tiny_png_bytes):
    """Vision parser tries Anthropic Claude when OpenAI is unavailable."""
    parser = VisionParser(prefer_provider="anthropic")
    with patch.object(parser, "_describe_with_anthropic",
                      AsyncMock(return_value="Image described by Claude.")):
        result = await parser.parse_image_bytes(tiny_png_bytes, "test.png")
    assert result.description
