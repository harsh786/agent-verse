# tests/ingestion/test_audio_parser.py
"""Audio parser must transcribe audio using Whisper API and chunk by timestamps."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.ingestion.parsers.audio_parser import (
    AudioParser, AudioParseResult, AudioSegment,
)


@pytest.fixture
def mock_transcription():
    """Mock OpenAI Whisper transcription response."""
    return MagicMock(
        text="Hello this is a test. The system supports dynamic orchestration.",
        segments=[
            MagicMock(start=0.0, end=3.5, text="Hello this is a test."),
            MagicMock(start=3.5, end=7.2, text="The system supports dynamic orchestration."),
        ]
    )


async def test_audio_parser_transcribes_with_whisper(mock_transcription):
    """AudioParser must call Whisper API and return transcript with timestamps."""
    parser = AudioParser()
    with patch.object(parser, "_transcribe_with_whisper",
                      AsyncMock(return_value=mock_transcription)):
        result = await parser.parse_bytes(
            audio_bytes=b"fake_audio_content",
            source_name="interview.mp3",
            mime_type="audio/mpeg",
        )
    assert isinstance(result, AudioParseResult)
    assert result.transcript is not None
    assert len(result.transcript) > 0
    assert "orchestration" in result.transcript.lower()


async def test_audio_parser_chunks_by_timestamp(mock_transcription):
    """Chunks must include start/end timestamps from Whisper segments."""
    parser = AudioParser(chunk_duration_seconds=5.0)
    with patch.object(parser, "_transcribe_with_whisper",
                      AsyncMock(return_value=mock_transcription)):
        result = await parser.parse_bytes(b"fake", "test.mp3", "audio/mpeg")
    chunks = result.to_chunks()
    assert len(chunks) >= 1
    for chunk in chunks:
        assert "content" in chunk
        assert "start_time" in chunk
        assert "end_time" in chunk
        assert chunk["content"].strip()


async def test_audio_parser_fallback_without_openai_key():
    """Parser must gracefully degrade without OpenAI key."""
    parser = AudioParser()
    with patch.object(parser, "_transcribe_with_whisper",
                      AsyncMock(side_effect=Exception("No API key"))):
        result = await parser.parse_bytes(b"fake", "test.mp3", "audio/mpeg")
    assert isinstance(result, AudioParseResult)


def test_audio_parse_result_to_chunks_from_transcript():
    """Pre-existing transcript text must be chunked by duration."""
    result = AudioParseResult(
        source_name="test.mp3",
        transcript="[00:00:00] Hello.\n[00:00:05] World.\n[00:01:00] End.",
        segments=[
            AudioSegment(start=0.0, end=5.0, text="Hello."),
            AudioSegment(start=5.0, end=60.0, text="World."),
            AudioSegment(start=60.0, end=65.0, text="End."),
        ],
    )
    chunks = result.to_chunks()
    assert len(chunks) >= 1
    assert all("start_time" in c for c in chunks)


async def test_audio_parser_handles_empty_bytes():
    """Empty audio must not crash."""
    parser = AudioParser()
    result = await parser.parse_bytes(b"", "empty.mp3", "audio/mpeg")
    assert isinstance(result, AudioParseResult)
