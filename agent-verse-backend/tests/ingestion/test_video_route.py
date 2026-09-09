"""WS-12 [video gap] — ContentType.VIDEO routes through the real VideoParser.

Before this fix ``parse_bytes_async`` had no VIDEO branch, so video bytes fell
through to naive byte-decode (garbage). This pins that VIDEO is transcribed via
``app.ingestion.parsers.video_parser.VideoParser``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.ingestion.content_classifier import ContentType
from app.ingestion.parser_registry import ParserRegistry
from app.ingestion.parsers.video_parser import VideoParseResult


@pytest.mark.asyncio
async def test_video_routes_through_video_parser() -> None:
    registry = ParserRegistry()
    fake = VideoParseResult(source_name="clip.mp4", transcript="hello from the video")
    with patch(
        "app.ingestion.parsers.video_parser.VideoParser.parse_bytes",
        new=AsyncMock(return_value=fake),
    ):
        text, _meta = await registry.parse_bytes_async(
            b"\x00\x00\x00\x18ftypmp42", ContentType.VIDEO, filename="clip.mp4"
        )
    assert text == "hello from the video"
    # Not naive byte-decoded container garbage.
    assert "ftyp" not in text


@pytest.mark.asyncio
async def test_video_parse_error_surfaces_degradation_not_garbage() -> None:
    registry = ParserRegistry()
    fake = VideoParseResult(source_name="clip.mp4", transcript="", error="no ffmpeg")
    with patch(
        "app.ingestion.parsers.video_parser.VideoParser.parse_bytes",
        new=AsyncMock(return_value=fake),
    ):
        text, meta = await registry.parse_bytes_async(
            b"\x00\x00\x00\x18ftypmp42", ContentType.VIDEO, filename="clip.mp4"
        )
    assert text == ""
    assert meta.get("video_degraded") == "no ffmpeg"
