"""AudioParser — transcribes audio with the Model Registry's speech-to-text model
(timestamp chunking).

The model chain comes from :func:`app.ai_router.resolve.resolve_stt` (registry
preference order → env pins → local faster-whisper); each model runs where it is
configured — an OpenAI-compatible ``/audio/transcriptions`` endpoint at the
model's own ``base_url`` with its own key, or an in-process engine — and the next
one is tried when it fails. Used by the multimodal pipeline, the parser registry
and the video parser.
"""

from __future__ import annotations

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
    # The speech-to-text model that produced the transcript, its provider label
    # and where the choice came from (registry_preference / env_pin / …).
    model: str = ""
    provider: str = ""
    model_source: str = ""

    def to_chunks(self, chunk_duration_seconds: float = 60.0) -> list[dict[str, Any]]:
        if not self.segments:
            return (
                [
                    {
                        "content": self.transcript,
                        "chunk_index": 0,
                        "start_time": "00:00:00",
                        "source_name": self.source_name,
                        "content_type": "audio",
                    }
                ]
                if self.transcript
                else []
            )
        chunks: list[dict[str, Any]] = []
        window_start = self.segments[0].start
        window_texts: list[str] = []
        chunk_idx = 0
        for seg in self.segments:
            if seg.start - window_start >= chunk_duration_seconds and window_texts:
                chunks.append(
                    {
                        "content": " ".join(window_texts),
                        "chunk_index": chunk_idx,
                        "start_time": _fmt(window_start),
                        "end_time": _fmt(seg.start),
                        "source_name": self.source_name,
                        "content_type": "audio",
                    }
                )
                chunk_idx += 1
                window_start = seg.start
                window_texts = [seg.text]
            else:
                window_texts.append(seg.text)
        if window_texts:
            chunks.append(
                {
                    "content": " ".join(window_texts),
                    "chunk_index": chunk_idx,
                    "start_time": _fmt(window_start),
                    "end_time": _fmt(self.segments[-1].end) if self.segments else "unknown",
                    "source_name": self.source_name,
                    "content_type": "audio",
                }
            )
        return chunks


def _fmt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


@dataclass
class Transcription:
    """A normalised transcription and the model that produced it."""

    text: str
    segments: list[AudioSegment] = field(default_factory=list)
    language: str = "en"
    model: str = ""
    provider: str = ""
    source: str = ""


def _segments(raw: Any) -> list[AudioSegment]:
    out: list[AudioSegment] = []
    for seg in raw or []:
        get = seg.get if isinstance(seg, dict) else (lambda k, s=seg: getattr(s, k, None))
        try:
            out.append(
                AudioSegment(
                    start=float(get("start") or 0.0),
                    end=float(get("end") or 0.0),
                    text=str(get("text") or ""),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


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
            transcription = await self._transcribe(audio_bytes, source_name, mime_type)
            return AudioParseResult(
                source_name=source_name,
                transcript=str(getattr(transcription, "text", "") or ""),
                segments=_segments(getattr(transcription, "segments", None)),
                language=str(getattr(transcription, "language", "") or "en"),
                model=str(getattr(transcription, "model", "") or ""),
                provider=str(getattr(transcription, "provider", "") or ""),
                model_source=str(getattr(transcription, "source", "") or ""),
            )
        except Exception as exc:
            return AudioParseResult(source_name=source_name, error=str(exc))

    async def _transcribe(self, audio_bytes: bytes, filename: str, mime_type: str) -> Transcription:
        """Transcribe with the resolved speech-to-text chain, failing over in order.

        Raises ``ModelNotConfiguredError`` (with a hint) when no model is
        configured, or ``RuntimeError`` naming every model's failure.
        """
        from app.ai_router.resolve import resolve_stt

        resolution = resolve_stt()
        errors: list[str] = []
        for target in resolution.targets:
            try:
                return await _transcribe_with(target, audio_bytes, filename, mime_type)
            except Exception as exc:
                errors.append(f"{target.label}: {type(exc).__name__}: {str(exc)[:200]}")
        raise RuntimeError("every speech-to-text model failed: " + "; ".join(errors))

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


async def _transcribe_with(
    target: Any, audio_bytes: bytes, filename: str, mime_type: str
) -> Transcription:
    """One speech-to-text model: an OpenAI-compatible endpoint (segment
    timestamps via ``verbose_json``) or an in-process / vendor engine."""
    if target.kind == "endpoint":
        from app.ai_router.speech import speech_api_key, transcribe_via_endpoint

        data = await transcribe_via_endpoint(
            base_url=str(target.base_url),
            api_key=speech_api_key(target.provider, target.entry),
            model=target.model,
            audio=audio_bytes,
            filename=filename,
            mime_type=mime_type,
            timestamps=True,
        )
        return Transcription(
            text=str(data.get("text") or ""),
            segments=_segments(data.get("segments")),
            language=str(data.get("language") or "en"),
            model=target.model,
            provider=target.provider,
            source=target.source,
        )
    from app.voice.providers import stt_provider_for

    result = await stt_provider_for(target).transcribe(audio_bytes, mime_type)
    return Transcription(
        text=result.transcript,
        segments=_segments(result.segments),
        language=result.language or "en",
        model=target.model,
        provider=target.provider,
        source=target.source,
    )
