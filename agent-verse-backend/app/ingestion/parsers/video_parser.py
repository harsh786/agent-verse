"""VideoParser — extract audio + transcript from video."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VideoParseResult:
    source_name: str
    transcript: str = ""
    scene_descriptions: list[str] = field(default_factory=list)
    error: str | None = None

    def to_chunks(self) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        if self.transcript:
            chunks.append({
                "content": f"[Transcript]\n{self.transcript}",
                "chunk_index": 0,
                "source_name": self.source_name,
                "content_type": "video",
                "chunk_type": "transcript",
            })
        for i, desc in enumerate(self.scene_descriptions):
            chunks.append({
                "content": desc,
                "chunk_index": len(chunks),
                "source_name": self.source_name,
                "content_type": "video",
                "chunk_type": "scene_description",
                "scene_index": i,
            })
        return chunks or [{
            "content": f"[Video: {self.source_name}]",
            "chunk_index": 0,
            "source_name": self.source_name,
            "content_type": "video",
        }]


class VideoParser:
    async def parse_bytes(self, video_bytes: bytes, source_name: str) -> VideoParseResult:
        if not video_bytes:
            return VideoParseResult(source_name=source_name, error="empty video")
        transcript = await self._extract_and_transcribe(video_bytes, source_name)
        return VideoParseResult(source_name=source_name, transcript=transcript)

    async def _extract_and_transcribe(self, video_bytes: bytes, filename: str) -> str:
        try:
            import os
            import subprocess
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as vf:
                vf.write(video_bytes)
                video_path = vf.name
            audio_path = video_path.replace(".mp4", "_audio.mp3")
            result = subprocess.run(
                ["ffmpeg", "-i", video_path, "-q:a", "0", "-map", "a", audio_path, "-y", "-loglevel", "quiet"],
                capture_output=True,
                timeout=120,
            )
            if result.returncode == 0 and os.path.exists(audio_path):
                from app.ingestion.parsers.audio_parser import AudioParser
                parser = AudioParser()
                audio_result = await parser.parse_file_path(audio_path)
                transcript = audio_result.transcript
                os.unlink(audio_path)
            else:
                transcript = f"[Audio extraction failed for {filename}]"
            os.unlink(video_path)
            return transcript
        except Exception as exc:
            return f"[Video transcription error: {exc!s}]"
