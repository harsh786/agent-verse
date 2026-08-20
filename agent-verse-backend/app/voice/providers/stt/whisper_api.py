"""OpenAI Whisper API STT — requires OPENAI_API_KEY."""
from __future__ import annotations

import os

from app.voice.providers.base import TranscriptResult


class WhisperAPISTT:
    provider_name:      str  = "whisper_api"
    supports_streaming: bool = False

    def __init__(self) -> None:
        self._ready = False

    async def warmup(self) -> None:
        self._ready = bool(os.getenv("OPENAI_API_KEY"))

    async def is_ready(self) -> bool:
        return self._ready

    async def transcribe(self, audio_bytes: bytes, content_type: str) -> TranscriptResult:
        import httpx
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set for whisper_api provider")
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                data={"model": "whisper-1"},
                files={"file": ("audio.wav", audio_bytes, content_type)},
            )
            r.raise_for_status()
            data = r.json()
        return TranscriptResult(
            transcript=data.get("text", ""),
            language=data.get("language", "en"),
            confidence=0.95,
            provider=self.provider_name,
        )
