"""Browser fallback TTS — returns empty WAV; browser uses Web Speech API instead.

Used when no local TTS model is available. The frontend detects the empty response
and falls back to window.speechSynthesis.
"""
from __future__ import annotations

import io
from collections.abc import AsyncGenerator

import numpy as np
import soundfile as sf

SAMPLE_RATE = 24_000


class BrowserFallbackTTS:
    provider_name:          str  = "browser"
    sample_rate:            int  = SAMPLE_RATE
    supports_voice_cloning: bool = False
    supports_nonverbal:     bool = False
    max_text_length:        int  = 4096

    async def warmup(self) -> None:
        pass

    async def is_ready(self) -> bool:
        return True

    async def synthesize(
        self, text: str, *, ref_audio: bytes | None = None, ref_text: str | None = None,
        language: str = "en", speed: float = 1.0, voice_id: str | None = None,
    ) -> bytes:
        # Return minimal silent WAV (browser will handle speech)
        silence = np.zeros(SAMPLE_RATE // 10, dtype=np.float32)
        buf = io.BytesIO()
        sf.write(buf, silence, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    async def synthesize_streaming(
        self, text: str, *, ref_audio: bytes | None = None, ref_text: str | None = None,
        language: str = "en", voice_id: str | None = None,
    ) -> AsyncGenerator[bytes, None]:
        wav = await self.synthesize(text)
        yield wav[44:]
