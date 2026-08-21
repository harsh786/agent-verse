"""Abstract STT and TTS provider protocols.

Every concrete provider MUST satisfy these structural protocols.
No inheritance required — Python's structural subtyping (Protocol) is used.

Adding a new provider:
  1. Create app/voice/providers/stt/<name>.py or tts/<name>.py
  2. Implement the protocol methods (no base class needed)
  3. Add one entry to STT_REGISTRY or TTS_REGISTRY in providers/__init__.py
  4. Set env var VOICE_STT_PROVIDER=<name> or VOICE_TTS_PROVIDER=<name>
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ── STT Protocol ──────────────────────────────────────────────────────────────


@runtime_checkable
class STTProvider(Protocol):
    """Speech-to-Text provider contract."""

    provider_name: str
    supports_streaming: bool

    async def transcribe(
        self,
        audio_bytes: bytes,
        content_type: str,
    ) -> TranscriptResult: ...

    async def warmup(self) -> None: ...

    async def is_ready(self) -> bool: ...


# ── TTS Protocol ──────────────────────────────────────────────────────────────


@runtime_checkable
class TTSProvider(Protocol):
    """Text-to-Speech provider contract."""

    provider_name: str
    sample_rate: int
    supports_voice_cloning: bool
    supports_nonverbal: bool
    max_text_length: int

    async def synthesize(
        self,
        text: str,
        *,
        ref_audio: bytes | None = None,
        ref_text: str | None = None,
        language: str = "en",
        speed: float = 1.0,
        voice_id: str | None = None,
    ) -> bytes: ...

    async def synthesize_streaming(
        self,
        text: str,
        *,
        ref_audio: bytes | None = None,
        ref_text: str | None = None,
        language: str = "en",
        voice_id: str | None = None,
    ) -> AsyncGenerator[bytes, None]: ...

    async def warmup(self) -> None: ...

    async def is_ready(self) -> bool: ...


# ── Shared result types ───────────────────────────────────────────────────────


@dataclass
class TranscriptResult:
    """Normalised STT output — same shape regardless of provider."""

    transcript: str
    language: str
    confidence: float
    segments: list[dict[str, Any]] = field(default_factory=list)
    duration_s: float = 0.0
    provider: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "transcript": self.transcript,
            "language": self.language,
            "confidence": self.confidence,
            "segments": self.segments,
            "duration_s": self.duration_s,
        }
