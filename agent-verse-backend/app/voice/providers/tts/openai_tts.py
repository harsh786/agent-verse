"""OpenAI-compatible ``/audio/speech`` TTS (OpenAI, speaches, LocalAI, vLLM …).

The model, endpoint and credential come from the resolved Model Registry entry
(:func:`app.ai_router.resolve.resolve_tts`). Built with no arguments it uses the
env pin ``VOICE_TTS_MODEL`` on the OpenAI endpoint (``OPENAI_BASE_URL``, else the
official API) and refuses to guess a model when none is configured.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

SAMPLE_RATE = 24_000


class OpenAITTS:
    provider_name: str = "openai_tts"
    sample_rate: int = SAMPLE_RATE
    supports_voice_cloning: bool = False
    supports_nonverbal: bool = False
    max_text_length: int = 4096

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        provider: str = "openai",
        entry: Any = None,
    ) -> None:
        from app.ai_router.speech import voice_setting

        self.model = model or voice_setting("voice_tts_model")
        self._base_url = base_url
        self._provider = provider or "openai"
        self._entry = entry

    def _base(self) -> str | None:
        if self._base_url:
            return self._base_url.rstrip("/")
        from app.ai_router.speech import default_speech_base_url

        return default_speech_base_url(self._provider)

    def _api_key(self) -> str:
        from app.ai_router.speech import speech_api_key

        return speech_api_key(self._provider, self._entry)

    async def warmup(self) -> None:
        pass

    async def is_ready(self) -> bool:
        from app.ai_router.speech import has_real_key

        return bool(self.model and self._base()) and (
            bool(self._base_url) or has_real_key(self._api_key())
        )

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
        from app.ai_router.resolve import ModelNotConfiguredError
        from app.ai_router.speech import synthesize_via_endpoint, tts_voice

        base = self._base()
        if not self.model or not base:
            raise ModelNotConfiguredError(
                "text_to_speech",
                "set VOICE_TTS_MODEL for the openai_tts provider, or add a text_to_speech "
                "model in the Model Registry",
            )
        return await synthesize_via_endpoint(
            base_url=base,
            api_key=self._api_key(),
            model=self.model,
            text=text,
            voice=tts_voice(voice_id),
            response_format="wav",
            speed=speed,
        )

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
        chunk = 4800 * 2
        for pos in range(0, len(wav), chunk):
            yield wav[pos : pos + chunk]
