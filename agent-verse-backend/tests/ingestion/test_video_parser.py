"""VideoParser — real functional scenarios for audio extraction + transcription.

ffmpeg and the Whisper-backed AudioParser are mocked (no real subprocess/network
calls); the extraction/cleanup logic around them is exercised for real.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.parsers.audio_parser import AudioParseResult
from app.ingestion.parsers.video_parser import VideoParser, VideoParseResult


class TestVideoParseResultToChunks:
    def test_transcript_only_produces_transcript_chunk(self) -> None:
        result = VideoParseResult(source_name="clip.mp4", transcript="Hello world")
        chunks = result.to_chunks()
        assert len(chunks) == 1
        assert chunks[0]["chunk_type"] == "transcript"
        assert chunks[0]["content"] == "[Transcript]\nHello world"
        assert chunks[0]["chunk_index"] == 0
        assert chunks[0]["content_type"] == "video"

    def test_scene_descriptions_only_produces_scene_chunks(self) -> None:
        result = VideoParseResult(
            source_name="clip.mp4",
            scene_descriptions=["A cat on a table", "A dog barking"],
        )
        chunks = result.to_chunks()
        assert len(chunks) == 2
        assert chunks[0]["chunk_type"] == "scene_description"
        assert chunks[0]["scene_index"] == 0
        assert chunks[0]["content"] == "A cat on a table"
        assert chunks[1]["scene_index"] == 1
        assert chunks[1]["chunk_index"] == 1

    def test_transcript_and_scenes_combined_indexes_sequentially(self) -> None:
        result = VideoParseResult(
            source_name="clip.mp4",
            transcript="Narration text",
            scene_descriptions=["Scene A", "Scene B"],
        )
        chunks = result.to_chunks()
        assert len(chunks) == 3
        assert chunks[0]["chunk_type"] == "transcript"
        assert chunks[0]["chunk_index"] == 0
        assert chunks[1]["chunk_type"] == "scene_description"
        assert chunks[1]["chunk_index"] == 1
        assert chunks[1]["scene_index"] == 0
        assert chunks[2]["chunk_index"] == 2
        assert chunks[2]["scene_index"] == 1

    def test_no_content_falls_back_to_placeholder_chunk(self) -> None:
        result = VideoParseResult(source_name="clip.mp4")
        chunks = result.to_chunks()
        assert len(chunks) == 1
        assert chunks[0]["content"] == "[Video: clip.mp4]"
        assert chunks[0]["chunk_index"] == 0
        assert "chunk_type" not in chunks[0]


class TestVideoParserParseBytes:
    async def test_empty_bytes_returns_error(self) -> None:
        parser = VideoParser()
        result = await parser.parse_bytes(b"", "empty.mp4")
        assert result.error == "empty video"
        assert result.transcript == ""
        assert result.source_name == "empty.mp4"

    async def test_successful_extraction_returns_transcript(self) -> None:
        parser = VideoParser()

        def _fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            # cmd = ["ffmpeg", "-i", video_path, ..., audio_path, "-y", ...]
            audio_path = cmd[cmd.index("-map") + 2]
            with open(audio_path, "wb") as f:
                f.write(b"fake-audio-bytes")
            proc = MagicMock()
            proc.returncode = 0
            return proc

        fake_audio_result = AudioParseResult(source_name="audio", transcript="Hi there")
        with (
            patch("subprocess.run", side_effect=_fake_run),
            patch(
                "app.ingestion.parsers.audio_parser.AudioParser.parse_file_path",
                new=AsyncMock(return_value=fake_audio_result),
            ),
        ):
            result = await parser.parse_bytes(b"fake-video-bytes", "clip.mp4")

        assert result.error is None
        assert result.transcript == "Hi there"
        assert result.source_name == "clip.mp4"

    async def test_ffmpeg_nonzero_returncode_yields_extraction_failed_message(self) -> None:
        parser = VideoParser()
        proc = MagicMock()
        proc.returncode = 1
        with patch("subprocess.run", return_value=proc):
            result = await parser.parse_bytes(b"fake-video-bytes", "broken.mp4")
        assert result.transcript == "[Audio extraction failed for broken.mp4]"
        assert result.error is None

    async def test_ffmpeg_success_but_no_audio_file_yields_extraction_failed_message(
        self,
    ) -> None:
        parser = VideoParser()
        proc = MagicMock()
        proc.returncode = 0
        # ffmpeg reports success but never wrote the audio file — os.path.exists
        # check must still catch this and fall back cleanly.
        with patch("subprocess.run", return_value=proc):
            result = await parser.parse_bytes(b"fake-video-bytes", "silent.mp4")
        assert result.transcript == "[Audio extraction failed for silent.mp4]"

    async def test_subprocess_exception_is_caught_and_reported(self) -> None:
        parser = VideoParser()
        with patch("subprocess.run", side_effect=RuntimeError("ffmpeg missing")):
            result = await parser.parse_bytes(b"fake-video-bytes", "err.mp4")
        assert result.transcript == "[Video transcription error: ffmpeg missing]"
        assert result.error is None

    async def test_video_temp_file_is_cleaned_up_after_success(self) -> None:
        parser = VideoParser()
        captured_paths: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            video_path = cmd[2]
            audio_path = cmd[cmd.index("-map") + 2]
            captured_paths.append(video_path)
            captured_paths.append(audio_path)
            with open(audio_path, "wb") as f:
                f.write(b"x")
            proc = MagicMock()
            proc.returncode = 0
            return proc

        fake_audio_result = AudioParseResult(source_name="audio", transcript="ok")
        with (
            patch("subprocess.run", side_effect=_fake_run),
            patch(
                "app.ingestion.parsers.audio_parser.AudioParser.parse_file_path",
                new=AsyncMock(return_value=fake_audio_result),
            ),
        ):
            await parser.parse_bytes(b"vid-bytes", "clip.mp4")

        assert captured_paths, "ffmpeg should have been invoked"
        for path in captured_paths:
            assert not os.path.exists(path), f"temp file {path} was not cleaned up"


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
