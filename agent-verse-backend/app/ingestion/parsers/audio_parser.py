"""AudioParser — transcribes audio using OpenAI Whisper API with timestamp chunking."""
from __future__ import annotations
import io
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AudioSegment:
    start: float
    end: float
    text: str


@dataclass
class AudioParseResult:
    source_name: str
    transcript: str = ""
    segments: list[AudioSegment] = field(default_factory=list)
    error: str | None = None
    language: str = "en"

    def to_chunks(self, chunk_duration_seconds: float = 60.0) -> list[dict[str, Any]]:
        if not self.segments:
            return (
                [{"content": self.transcript, "chunk_index": 0, "start_time": "00:00:00",
                  "source_name": self.source_name, "content_type": "audio"}]
                if self.transcript else []
            )
        chunks: list[dict[str, Any]] = []
        window_start = self.segments[0].start
        window_texts: list[str] = []
        chunk_idx = 0
        for seg in self.segments:
            if seg.start - window_start >= chunk_duration_seconds and window_texts:
                chunks.append({
                    "content": " ".join(window_texts),
                    "chunk_index": chunk_idx,
                    "start_time": _fmt(window_start),
                    "end_time": _fmt(seg.start),
                    "source_name": self.source_name,
                    "content_type": "audio",
                })
                chunk_idx += 1
                window_start = seg.start
                window_texts = [seg.text]
            else:
                window_texts.append(seg.text)
        if window_texts:
            chunks.append({
                "content": " ".join(window_texts),
                "chunk_index": chunk_idx,
                "start_time": _fmt(window_start),
                "end_time": _fmt(self.segments[-1].end) if self.segments else "unknown",
                "source_name": self.source_name,
                "content_type": "audio",
            })
        return chunks


def _fmt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class AudioParser:
    def __init__(self, chunk_duration_seconds: float = 60.0) -> None:
        self._chunk_duration = chunk_duration_seconds

    async def parse_bytes(
        self,
        audio_bytes: bytes,
        source_name: str,
        mime_type: str = "audio/mpeg",
    ) -> AudioParseResult:
        if not audio_bytes:
            return AudioParseResult(source_name=source_name, error="empty audio")
        try:
            transcription = await self._transcribe_with_whisper(audio_bytes, source_name, mime_type)
            raw_segments = getattr(transcription, "segments", None) or []
            segments = [
                AudioSegment(start=seg.start, end=seg.end, text=seg.text)
                for seg in raw_segments
            ]
            return AudioParseResult(
                source_name=source_name,
                transcript=getattr(transcription, "text", ""),
                segments=segments,
                language=getattr(transcription, "language", "en"),
            )
        except Exception as exc:
            return AudioParseResult(source_name=source_name, error=str(exc))

    async def _transcribe_with_whisper(
        self, audio_bytes: bytes, filename: str, mime_type: str
    ) -> Any:
        import openai  # type: ignore[import]
        client = openai.AsyncOpenAI()
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = filename  # type: ignore[attr-defined]
        return await client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    async def parse_file_path(self, file_path: str) -> AudioParseResult:
        try:
            import os
            with open(file_path, "rb") as f:
                audio_bytes = f.read()
            source_name = os.path.basename(file_path)
            ext = os.path.splitext(file_path)[1].lower()
            mime_map = {
                ".mp3": "audio/mpeg",
                ".wav": "audio/wav",
                ".m4a": "audio/mp4",
                ".ogg": "audio/ogg",
                ".flac": "audio/flac",
            }
            mime_type = mime_map.get(ext, "audio/mpeg")
            return await self.parse_bytes(audio_bytes, source_name, mime_type)
        except Exception as exc:
            return AudioParseResult(source_name=file_path, error=str(exc))
