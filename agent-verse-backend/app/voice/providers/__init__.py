"""Provider registry — single source of truth for STT and TTS providers.

Usage (everywhere in the voice layer):
    from app.voice.providers import get_stt, get_tts

Which model runs:
    The speech models come from the Model Registry (capabilities speech_to_text /
    text_to_speech) via ``app.ai_router.resolve.resolve_stt`` / ``resolve_tts``;
    VOICE_STT_PROVIDER / VOICE_TTS_PROVIDER (+ VOICE_STT_MODEL / VOICE_TTS_MODEL)
    are the env-pin tier after the registry, and the installed local engines
    (faster-whisper; macOS say / Kokoro) the last tier.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from typing import Any

from app.voice.providers.base import STTProvider, TTSProvider

log = logging.getLogger(__name__)

# ── Provider registries ───────────────────────────────────────────────────────

STT_REGISTRY: dict[str, str] = {
    "faster_whisper": "app.voice.providers.stt.faster_whisper.FasterWhisperSTT",
    "whisper_api": "app.voice.providers.stt.whisper_api.WhisperAPISTT",
    "assemblyai": "app.voice.providers.stt.assemblyai.AssemblyAISTT",
}

TTS_REGISTRY: dict[str, str] = {
    "omnivoice": "app.voice.providers.tts.omnivoice.OmniVoiceTTS",
    "kokoro": "app.voice.providers.tts.kokoro.KokoroTTS",
    "macos_say": "app.voice.providers.tts.macos_say.MacOSSayTTS",
    "browser": "app.voice.providers.tts.browser_fallback.BrowserFallbackTTS",
    "elevenlabs": "app.voice.providers.tts.elevenlabs.ElevenLabsTTS",
    "openai_tts": "app.voice.providers.tts.openai_tts.OpenAITTS",
    "azure_tts": "app.voice.providers.tts.azure_tts.AzureTTS",
}

# ── Singletons ────────────────────────────────────────────────────────────────

_stt_instance: STTProvider | None = None
_tts_instance: TTSProvider | None = None
_stt_lock = asyncio.Lock()
_tts_lock = asyncio.Lock()


def _import_class(dotted_path: str) -> Any:
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def _str_attr(obj: Any, name: str) -> str:
    value = getattr(obj, name, "")
    return value if isinstance(value, str) else ""


def _tag(provider: Any, target: Any) -> Any:
    """Record which model the provider runs and where that choice came from."""
    provider.resolved_model = target.model
    provider.resolved_source = target.source
    provider.resolved_provider = target.provider
    return provider


def build_stt_provider(target: Any) -> STTProvider:
    """The STT provider for one resolved :class:`~app.ai_router.resolve.SpeechTarget`."""
    if target.kind == "endpoint":
        from app.voice.providers.stt.whisper_api import WhisperAPISTT

        return _tag(
            WhisperAPISTT(
                model=target.model,
                base_url=target.base_url,
                provider=target.provider,
                entry=target.entry,
            ),
            target,
        )
    if target.engine == "faster_whisper":
        from app.voice.providers.stt.faster_whisper import FasterWhisperSTT

        return _tag(FasterWhisperSTT(model_name=target.model), target)
    dotted = STT_REGISTRY.get(target.engine)
    if dotted is None:
        raise ValueError(f"Unknown STT engine {target.engine!r}. Available: {list(STT_REGISTRY)}")
    return _tag(_import_class(dotted)(), target)


def build_tts_provider(target: Any) -> TTSProvider:
    """The TTS provider for one resolved :class:`~app.ai_router.resolve.SpeechTarget`."""
    if target.kind == "endpoint":
        from app.voice.providers.tts.openai_tts import OpenAITTS

        return _tag(
            OpenAITTS(
                model=target.model,
                base_url=target.base_url,
                provider=target.provider,
                entry=target.entry,
            ),
            target,
        )
    if target.engine == "elevenlabs":
        from app.voice.providers.tts.elevenlabs import ElevenLabsTTS

        return _tag(ElevenLabsTTS(model=target.model, entry=target.entry), target)
    if target.engine == "omnivoice":
        from app.voice.providers.tts.omnivoice import OmniVoiceTTS

        return _tag(OmniVoiceTTS(model_name=target.model), target)
    dotted = TTS_REGISTRY.get(target.engine)
    if dotted is None:
        raise ValueError(f"Unknown TTS engine {target.engine!r}. Available: {list(TTS_REGISTRY)}")
    return _tag(_import_class(dotted)(), target)


_engine_cache: dict[tuple[str, str, str, str, str], Any] = {}


def stt_provider_for(target: Any) -> STTProvider:
    """A cached STT provider per target (a local engine loads its model once)."""
    key = ("stt", target.kind, target.provider, target.model, str(target.base_url or ""))
    cached = _engine_cache.get(key)
    if cached is None:
        cached = build_stt_provider(target)
        _engine_cache[key] = cached
    return cached


async def get_stt() -> STTProvider:
    """Return the STT provider singleton for the resolved speech-to-text model.

    The model comes from :func:`app.ai_router.resolve.resolve_stt`: the Model
    Registry order, then the env / settings pins (``VOICE_STT_PROVIDER`` /
    ``VOICE_STT_MODEL`` / ``AUDIO_MODEL``…), then the local faster-whisper engine.
    Raises ``ModelNotConfiguredError`` (with a hint) when nothing is configured.
    """
    global _stt_instance
    if _stt_instance is not None:
        return _stt_instance
    async with _stt_lock:
        if _stt_instance is not None:
            return _stt_instance
        from app.ai_router.resolve import resolve_stt

        resolution = resolve_stt()
        target = resolution.targets[0]
        _stt_instance = stt_provider_for(target)
        log.info(
            "voice.stt.provider_loaded provider=%s model=%s source=%s",
            _stt_instance.provider_name,
            target.model,
            target.source,
        )
        return _stt_instance


def _browser_fallback(reason: str) -> TTSProvider:
    from app.voice.providers.tts.browser_fallback import BrowserFallbackTTS

    log.warning("voice.tts.degraded_to_browser reason=%s", reason[:300])
    provider: Any = BrowserFallbackTTS()
    provider.resolved_model = ""
    provider.resolved_source = "degraded"
    provider.resolved_provider = "browser"
    return provider  # type: ignore[no-any-return]


async def get_tts() -> TTSProvider:
    """Return the TTS provider singleton for the resolved text-to-speech model.

    The chain comes from :func:`app.ai_router.resolve.resolve_tts` (registry →
    ``VOICE_TTS_PROVIDER`` / ``VOICE_TTS_MODEL`` → local macOS say / Kokoro); a
    target whose engine fails to load is skipped. With nothing usable the
    browser's own speech synthesis answers (``resolved_source="degraded"``).
    """
    global _tts_instance
    if _tts_instance is not None:
        return _tts_instance
    async with _tts_lock:
        if _tts_instance is not None:
            return _tts_instance
        from app.ai_router.resolve import ModelNotConfiguredError, resolve_tts

        try:
            targets = resolve_tts().targets
        except ModelNotConfiguredError as exc:
            _tts_instance = _browser_fallback(str(exc))
            return _tts_instance
        errors: list[str] = []
        for target in targets:
            try:
                _tts_instance = build_tts_provider(target)
            except ImportError as exc:
                errors.append(f"{target.label}: {exc}")
                log.warning("voice.tts.fallback target=%s error=%s", target.label, exc)
                continue
            log.info(
                "voice.tts.provider_loaded provider=%s model=%s source=%s",
                _tts_instance.provider_name,
                target.model,
                target.source,
            )
            return _tts_instance
        _tts_instance = _browser_fallback("; ".join(errors) or "no TTS engine could be loaded")
        return _tts_instance


async def warmup_providers() -> None:
    """Preload both providers at worker startup."""
    stt = await get_stt()
    tts = await get_tts()
    await asyncio.gather(stt.warmup(), tts.warmup(), return_exceptions=True)
    log.info("voice.providers.warmup_complete stt=%s tts=%s", stt.provider_name, tts.provider_name)


async def get_capabilities() -> dict:
    """Return capabilities of the currently loaded STT + TTS providers."""
    stt = await get_stt()
    tts = await get_tts()
    return {
        "stt": {
            "provider": stt.provider_name,
            "model": _str_attr(stt, "resolved_model"),
            "source": _str_attr(stt, "resolved_source"),
            "ready": await stt.is_ready(),
            "streaming": stt.supports_streaming,
        },
        "tts": {
            "provider": tts.provider_name,
            "model": _str_attr(tts, "resolved_model"),
            "source": _str_attr(tts, "resolved_source"),
            "ready": await tts.is_ready(),
            "voice_cloning": tts.supports_voice_cloning,
            "nonverbal": tts.supports_nonverbal,
            "sample_rate": tts.sample_rate,
        },
    }


def override_stt(provider: STTProvider) -> None:
    global _stt_instance
    _stt_instance = provider


def override_tts(provider: TTSProvider) -> None:
    global _tts_instance
    _tts_instance = provider


def reset_providers() -> None:
    global _stt_instance, _tts_instance
    _stt_instance = None
    _tts_instance = None
    _engine_cache.clear()
