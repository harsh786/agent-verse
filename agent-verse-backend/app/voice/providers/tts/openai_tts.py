"""OpenAI TTS — requires OPENAI_API_KEY."""
from __future__ import annotations

import io
import os
from collections.abc import AsyncGenerator

SAMPLE_RATE = 24_000


class OpenAITTS:
    provider_name:          str  = "openai_tts"
    sample_rate:            int  = SAMPLE_RATE
    supports_voice_cloning: bool = False
    supports_nonverbal:     bool = False
    max_text_length:        int  = 4096

    def __init__(self) -> None:
        self._key = os.getenv("OPENAI_API_KEY", "")

    async def warmup(self) -> None:
        pass

    async def is_ready(self) -> bool:
        return bool(self._key)

    async def synthesize(
        self, text: str, *, ref_audio: bytes | None = None, ref_text: str | None = None,
        language: str = "en", speed: float = 1.0, voice_id: str | None = None,
    ) -> bytes:
        import httpx
        voice = voice_id or os.getenv("OPENAI_TTS_VOICE", "nova")
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
                json={"model": "tts-1", "input": text, "voice": voice,
                      "response_format": "wav", "speed": speed},
            )
            r.raise_for_status()
            return r.content

    async def synthesize_streaming(
        self, text: str, *, ref_audio: bytes | None = None, ref_text: str | None = None,
        language: str = "en", voice_id: str | None = None,
    ) -> AsyncGenerator[bytes, None]:
        wav = await self.synthesize(text, language=language, voice_id=voice_id)
        CHUNK = 4800 * 2
        for pos in range(0, len(wav), CHUNK):
            yield wav[pos:pos + CHUNK]
