# tests/ingestion/test_audio_parser.py
"""Audio parser must transcribe audio using Whisper API and chunk by timestamps."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.parsers.audio_parser import (
    AudioParser,
    AudioParseResult,
    AudioSegment,
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


# ── Edge cases: corrupted / unsupported / zero-duration / very long audio ──────


async def test_audio_parser_corrupted_file_sets_error_not_crash():
    """A corrupted audio file (bytes present, but undecodable by Whisper)
    surfaces as a Whisper API error -- the parser must degrade to an
    AudioParseResult with `.error` set rather than raising."""
    parser = AudioParser()
    with patch.object(
        parser,
        "_transcribe_with_whisper",
        AsyncMock(side_effect=Exception("Invalid file format: could not decode audio stream")),
    ):
        result = await parser.parse_bytes(b"\x00\x01garbage-not-real-audio", "corrupted.mp3", "audio/mpeg")
    assert isinstance(result, AudioParseResult)
    assert result.error is not None
    assert "could not decode" in result.error
    assert result.transcript == ""
    assert result.segments == []
    # Corrupted-audio result must chunk to nothing, not fabricate content.
    assert result.to_chunks() == []


async def test_audio_parser_unsupported_codec_sets_error_not_crash():
    """An unsupported audio codec/container (e.g. Whisper rejects the mime
    type) must also degrade gracefully with `.error` set."""
    parser = AudioParser()
    with patch.object(
        parser,
        "_transcribe_with_whisper",
        AsyncMock(side_effect=Exception("Unsupported file type: audio/x-unknown-codec")),
    ):
        result = await parser.parse_bytes(
            b"fake-bytes-in-an-unsupported-codec",
            "clip.xyz",
            mime_type="audio/x-unknown-codec",
        )
    assert isinstance(result, AudioParseResult)
    assert result.error is not None
    assert "Unsupported file type" in result.error
    assert result.to_chunks() == []


async def test_audio_parser_zero_duration_audio_produces_no_chunks():
    """A valid-but-silent/zero-duration audio file: Whisper returns success
    with an empty transcript and no segments. Must not error, and must not
    fabricate a chunk out of nothing."""
    parser = AudioParser()
    zero_duration = MagicMock(text="", segments=[], language="en")
    with patch.object(parser, "_transcribe_with_whisper", AsyncMock(return_value=zero_duration)):
        result = await parser.parse_bytes(b"real-but-silent-audio-bytes", "silence.wav", "audio/wav")
    assert result.error is None
    assert result.transcript == ""
    assert result.segments == []
    assert result.to_chunks() == []


async def test_audio_parser_extremely_long_audio_chunks_across_many_windows():
    """A multi-hour transcript (hundreds of Whisper segments) must chunk
    into multiple duration-bounded windows -- not a single giant blob, and
    not a crash/hang on many segments. There is no timeout/streaming logic
    in this parser itself (transcription happens in one Whisper API call);
    this pins down that to_chunks() itself scales to long-form audio."""
    # 3 hours of audio, one segment every 5 seconds => 2160 segments.
    total_seconds = 3 * 60 * 60
    segments = [
        MagicMock(start=float(t), end=float(t + 5), text=f"segment at {t}s")
        for t in range(0, total_seconds, 5)
    ]
    long_transcription = MagicMock(
        text=" ".join(s.text for s in segments),
        segments=segments,
        language="en",
    )
    parser = AudioParser(chunk_duration_seconds=60.0)
    with patch.object(
        parser, "_transcribe_with_whisper", AsyncMock(return_value=long_transcription)
    ):
        result = await parser.parse_bytes(b"very-long-audio-bytes", "lecture.mp3", "audio/mpeg")

    assert result.error is None
    assert len(result.segments) == len(segments)

    chunks = result.to_chunks()
    # ~60s windows over 3 hours => on the order of 180 chunks, definitely > 1.
    assert len(chunks) > 100
    # Chunk indices must be contiguous starting at 0.
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    # The last chunk's end_time must reflect the final segment, formatted HH:MM:SS.
    assert chunks[-1]["end_time"] == "03:00:00"
    for c in chunks:
        assert c["content"].strip()


async def test_audio_parser_file_path_nonexistent_file_sets_error():
    """A missing file path must degrade to an error result, not raise."""
    parser = AudioParser()
    result = await parser.parse_file_path("/no/such/path/does-not-exist.mp3")
    assert isinstance(result, AudioParseResult)
    assert result.error is not None


async def test_audio_parser_file_path_unknown_extension_falls_back_to_default_mime():
    """An unrecognized extension (not in the mime map) must still be
    parsed -- falling back to the default mime type rather than erroring
    out before transcription is even attempted."""
    import os
    import tempfile

    parser = AudioParser()
    mock_transcription = MagicMock(text="transcribed unknown format", segments=[], language="en")
    with tempfile.NamedTemporaryFile(suffix=".aac", delete=False) as f:
        f.write(b"fake-aac-bytes")
        path = f.name
    try:
        with patch.object(
            parser, "_transcribe_with_whisper", AsyncMock(return_value=mock_transcription)
        ) as mock_transcribe:
            result = await parser.parse_file_path(path)
        assert result.error is None
        assert result.transcript == "transcribed unknown format"
        # Called with the default fallback mime type for an unmapped extension.
        assert mock_transcribe.call_args.args[2] == "audio/mpeg"
    finally:
        os.unlink(path)
