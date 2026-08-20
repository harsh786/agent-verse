"""Kokoro TTS — MIT-licensed, 82 M params, high quality, no API key.

Requires: pip install kokoro-onnx misaki[en]
"""
from __future__ import annotations

import asyncio
import io
import os
from collections.abc import AsyncGenerator
from typing import Any

import soundfile as sf

SAMPLE_RATE   = 24_000
CHUNK_FRAMES  = 4_800
WAV_HEADER_SZ = 44


class KokoroTTS:
    provider_name:          str  = "kokoro"
    sample_rate:            int  = SAMPLE_RATE
    supports_voice_cloning: bool = False
    supports_nonverbal:     bool = False
    max_text_length:        int  = 2048

    def __init__(self) -> None:
        self._kokoro: Any | None = None
        self._lock = asyncio.Lock()

    async def warmup(self) -> None:
        await self._get_kokoro()

    async def is_ready(self) -> bool:
        return self._kokoro is not None

    async def synthesize(
        self, text: str, *, ref_audio: bytes | None = None, ref_text: str | None = None,
        language: str = "en", speed: float = 1.0, voice_id: str | None = None,
    ) -> bytes:
        kokoro = await self._get_kokoro()
        voice  = voice_id or os.getenv("KOKORO_VOICE", "af_heart")
        loop   = asyncio.get_event_loop()

        samples, sample_rate = await loop.run_in_executor(
            None,
            lambda: kokoro.create(text, voice=voice, speed=speed, lang=language[:2]),
        )
        import numpy as np
        if not isinstance(samples, np.ndarray):
            samples = np.array(samples, dtype=np.float32)
        buf = io.BytesIO()
        sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    async def synthesize_streaming(
        self, text: str, *, ref_audio: bytes | None = None, ref_text: str | None = None,
        language: str = "en", voice_id: str | None = None,
    ) -> AsyncGenerator[bytes, None]:
        wav = await self.synthesize(text, language=language, voice_id=voice_id)
        pcm = wav[WAV_HEADER_SZ:]
        for pos in range(0, len(pcm), CHUNK_FRAMES * 2):
            yield pcm[pos:pos + CHUNK_FRAMES * 2]
            await asyncio.sleep(0)

    async def _get_kokoro(self) -> Any:
        if self._kokoro:
            return self._kokoro
        async with self._lock:
            if self._kokoro:
                return self._kokoro
            from kokoro_onnx import Kokoro
            model_dir  = os.getenv("MODEL_CACHE_DIR", "/app/models")
            model_path = os.path.join(model_dir, "kokoro-v1.0.onnx")
            voices_path = os.path.join(model_dir, "voices-v1.0.bin")
            # If model files not found, download them
            if not os.path.exists(model_path):
                import urllib.request
                os.makedirs(model_dir, exist_ok=True)
                urllib.request.urlretrieve(
                    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
                    model_path,
                )
                urllib.request.urlretrieve(
                    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
                    voices_path,
                )
            self._kokoro = Kokoro(model_path, voices_path)
            return self._kokoro
