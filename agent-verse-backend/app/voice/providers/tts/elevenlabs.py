"""ElevenLabs TTS — paid API, voice cloning. Requires ELEVENLABS_API_KEY."""
from __future__ import annotations

import io
import os
from collections.abc import AsyncGenerator

SAMPLE_RATE = 44_100


class ElevenLabsTTS:
    provider_name:          str  = "elevenlabs"
    sample_rate:            int  = SAMPLE_RATE
    supports_voice_cloning: bool = True
    supports_nonverbal:     bool = False
    max_text_length:        int  = 5000

    def __init__(self) -> None:
        self._key = os.getenv("ELEVENLABS_API_KEY", "")

    async def warmup(self) -> None:
        pass

    async def is_ready(self) -> bool:
        return bool(self._key)

    async def synthesize(
        self, text: str, *, ref_audio: bytes | None = None, ref_text: str | None = None,
        language: str = "en", speed: float = 1.0, voice_id: str | None = None,
    ) -> bytes:
        import httpx
        vid = voice_id or os.getenv("ELEVENLABS_VOICE_ID", "rachel")
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
                headers={"xi-api-key": self._key, "Content-Type": "application/json"},
                json={"text": text, "model_id": "eleven_turbo_v2_5",
                      "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            )
            r.raise_for_status()
        # Convert mp3 → wav
        try:
            import pydub
            seg = pydub.AudioSegment.from_mp3(io.BytesIO(r.content))
            buf = io.BytesIO()
            seg.export(buf, format="wav")
            return buf.getvalue()
        except ImportError:
            return r.content   # return mp3 if pydub not available

    async def synthesize_streaming(
        self, text: str, *, ref_audio: bytes | None = None, ref_text: str | None = None,
        language: str = "en", voice_id: str | None = None,
    ) -> AsyncGenerator[bytes, None]:
        wav = await self.synthesize(text, language=language, voice_id=voice_id)
        CHUNK = 9600
        for pos in range(0, len(wav), CHUNK):
            yield wav[pos:pos + CHUNK]
