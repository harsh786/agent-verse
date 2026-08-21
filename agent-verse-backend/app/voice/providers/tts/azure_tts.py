"""Azure Cognitive Services TTS — requires AZURE_TTS_KEY + AZURE_TTS_REGION."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

SAMPLE_RATE = 24_000


class AzureTTS:
    provider_name: str = "azure_tts"
    sample_rate: int = SAMPLE_RATE
    supports_voice_cloning: bool = False
    supports_nonverbal: bool = False
    max_text_length: int = 10_000

    def __init__(self) -> None:
        self._key = os.getenv("AZURE_TTS_KEY", "")
        self._region = os.getenv("AZURE_TTS_REGION", "eastus")

    async def warmup(self) -> None:
        pass

    async def is_ready(self) -> bool:
        return bool(self._key)

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
        import httpx

        voice = voice_id or os.getenv("AZURE_TTS_VOICE", "en-US-JennyNeural")
        ssml = (
            f'<speak version="1.0" xml:lang="{language}">'
            f'<voice name="{voice}">'
            f'<prosody rate="{speed}">{text}</prosody>'
            f"</voice></speak>"
        )
        url = f"https://{self._region}.tts.speech.microsoft.com/cognitiveservices/v1"
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                url,
                headers={
                    "Ocp-Apim-Subscription-Key": self._key,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
                },
                content=ssml.encode(),
            )
            r.raise_for_status()
            return r.content

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
        CHUNK = 4800 * 2
        for pos in range(0, len(wav), CHUNK):
            yield wav[pos : pos + CHUNK]
