"""macOS System TTS — uses the built-in `say` command, no downloads required.

Works instantly on any macOS machine. Uses the system voice (Samantha by default).
Quality is good enough for greetings and alerts. Zero dependencies.

Set VOICE_TTS_PROVIDER=macos_say for local development on macOS.
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import tempfile
from collections.abc import AsyncGenerator

import soundfile as sf

SAMPLE_RATE = 22_050
CHUNK_FRAMES = 4_400  # 200 ms at 22 kHz
WAV_HEADER_SZ = 44


class MacOSSayTTS:
    """macOS built-in TTS via the `say` command. No model files required."""

    provider_name: str = "macos_say"
    sample_rate: int = SAMPLE_RATE
    supports_voice_cloning: bool = False
    supports_nonverbal: bool = False
    max_text_length: int = 4096

    def __init__(self) -> None:
        self._ready = os.path.exists("/usr/bin/say")

    async def warmup(self) -> None:
        pass  # No model to load

    async def is_ready(self) -> bool:
        return self._ready

    async def synthesize(
        self,
        text: str,
        *,
        ref_audio: bytes | None = None,
        ref_text: str | None = None,
        language: str = "en",
        speed: float = 1.0,
        voice_id: str | None = None,
    ) -> bytes:
        voice = voice_id or os.getenv("MACOS_SAY_VOICE", "Samantha")
        rate = int(180 * speed)  # default 180 words/min; scale with speed

        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as f:
            aiff_path = f.name

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["say", "-v", voice, "-r", str(rate), "-o", aiff_path, text],
                    check=True,
                    capture_output=True,
                ),
            )
            data, sr = sf.read(aiff_path, dtype="float32", always_2d=False)
            buf = io.BytesIO()
            sf.write(buf, data, sr, format="WAV", subtype="PCM_16")
            return buf.getvalue()
        finally:
            if os.path.exists(aiff_path):
                os.unlink(aiff_path)

    async def synthesize_streaming(
        self,
        text: str,
        *,
        ref_audio: bytes | None = None,
        ref_text: str | None = None,
        language: str = "en",
        voice_id: str | None = None,
    ) -> AsyncGenerator[bytes, None]:
        wav = await self.synthesize(text, language=language, voice_id=voice_id)
        pcm = wav[WAV_HEADER_SZ:]
        for pos in range(0, len(pcm), CHUNK_FRAMES * 2):
            yield pcm[pos : pos + CHUNK_FRAMES * 2]
            await asyncio.sleep(0)
