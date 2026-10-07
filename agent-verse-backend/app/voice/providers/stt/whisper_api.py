"""OpenAI-compatible ``/audio/transcriptions`` STT (OpenAI, Groq, vLLM, speaches …).

The model, endpoint and credential come from the resolved Model Registry entry
(:func:`app.ai_router.resolve.resolve_stt`): the entry's own ``base_url`` (else
its provider's API) and its saved key (else the provider's env key). Built with
no arguments it uses the env pin: ``VOICE_STT_MODEL`` / ``AUDIO_MODEL`` on the
OpenAI endpoint (``OPENAI_BASE_URL``, else the official API) — and refuses to
guess a model when none is configured.
"""

from __future__ import annotations

from typing import Any

from app.voice.providers.base import TranscriptResult


class WhisperAPISTT:
    provider_name: str = "whisper_api"
    supports_streaming: bool = False

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        provider: str = "openai",
        entry: Any = None,
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._provider = provider or "openai"
        self._entry = entry
        self._ready = False

    def _model_id(self) -> str:
        if self._model:
            return self._model
        from app.ai_router.speech import configured_transcription_model, voice_setting

        return voice_setting("voice_stt_model") or configured_transcription_model()

    def _base(self) -> str | None:
        if self._base_url:
            return self._base_url.rstrip("/")
        from app.ai_router.speech import default_speech_base_url

        return default_speech_base_url(self._provider)

    def _key(self) -> str:
        from app.ai_router.speech import speech_api_key

        return speech_api_key(self._provider, self._entry)

    def _usable(self) -> bool:
        from app.ai_router.speech import has_real_key

        # A model with its own endpoint URL may run without a key (vLLM / LAN).
        return bool(self._model_id() and self._base()) and (
            bool(self._base_url) or has_real_key(self._key())
        )

    async def warmup(self) -> None:
        self._ready = self._usable()

    async def is_ready(self) -> bool:
        return self._ready

    async def transcribe(self, audio_bytes: bytes, content_type: str) -> TranscriptResult:
        from app.ai_router.resolve import ModelNotConfiguredError
        from app.ai_router.speech import has_real_key, transcribe_via_endpoint

        model = self._model_id()
        if not model:
            raise ModelNotConfiguredError(
                "speech_to_text",
                "set VOICE_STT_MODEL (or AUDIO_MODEL) for the whisper_api provider, or add a "
                "speech_to_text model in the Model Registry",
            )
        base = self._base()
        if not base:
            raise ModelNotConfiguredError(
                "speech_to_text", f"provider {self._provider!r} has no /audio endpoint URL"
            )
        key = self._key()
        if not self._base_url and not has_real_key(key):
            raise RuntimeError(f"no API key for the {self._provider} speech-to-text endpoint")
        data = await transcribe_via_endpoint(
            base_url=base,
            api_key=key,
            model=model,
            audio=audio_bytes,
            filename="audio.wav",
            mime_type=content_type,
            timestamps=False,
            timeout=60,
        )
        return TranscriptResult(
            transcript=str(data.get("text", "")),
            language=str(data.get("language") or "en"),
            confidence=0.95,
            provider=self.provider_name,
        )
