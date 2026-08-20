"""Provider registry — single source of truth for STT and TTS providers.

Usage (everywhere in the voice layer):
    from app.voice.providers import get_stt, get_tts

Swapping providers:
    Set VOICE_STT_PROVIDER or VOICE_TTS_PROVIDER env var. No code changes required.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
from typing import Any

from app.voice.providers.base import STTProvider, TTSProvider

log = logging.getLogger(__name__)

# ── Provider registries ───────────────────────────────────────────────────────

STT_REGISTRY: dict[str, str] = {
    "faster_whisper": "app.voice.providers.stt.faster_whisper.FasterWhisperSTT",
    "whisper_api":    "app.voice.providers.stt.whisper_api.WhisperAPISTT",
    "assemblyai":     "app.voice.providers.stt.assemblyai.AssemblyAISTT",
}

TTS_REGISTRY: dict[str, str] = {
    "omnivoice":   "app.voice.providers.tts.omnivoice.OmniVoiceTTS",
    "kokoro":      "app.voice.providers.tts.kokoro.KokoroTTS",
    "browser":     "app.voice.providers.tts.browser_fallback.BrowserFallbackTTS",
    "elevenlabs":  "app.voice.providers.tts.elevenlabs.ElevenLabsTTS",
    "openai_tts":  "app.voice.providers.tts.openai_tts.OpenAITTS",
    "azure_tts":   "app.voice.providers.tts.azure_tts.AzureTTS",
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


async def get_stt() -> STTProvider:
    """Return the configured STT provider singleton."""
    global _stt_instance
    if _stt_instance is not None:
        return _stt_instance
    async with _stt_lock:
        if _stt_instance is not None:
            return _stt_instance
        provider_name = os.getenv("VOICE_STT_PROVIDER", "faster_whisper")
        dotted = STT_REGISTRY.get(provider_name)
        if dotted is None:
            raise ValueError(
                f"Unknown STT provider {provider_name!r}. Available: {list(STT_REGISTRY)}"
            )
        cls = _import_class(dotted)
        _stt_instance = cls()
        log.info("voice.stt.provider_loaded provider=%s", provider_name)
        return _stt_instance


async def get_tts() -> TTSProvider:
    """Return the configured TTS provider singleton.

    Falls back through kokoro → browser if omnivoice is not installed.
    """
    global _tts_instance
    if _tts_instance is not None:
        return _tts_instance
    async with _tts_lock:
        if _tts_instance is not None:
            return _tts_instance
        provider_name = os.getenv("VOICE_TTS_PROVIDER", "kokoro")
        dotted = TTS_REGISTRY.get(provider_name)
        if dotted is None:
            raise ValueError(
                f"Unknown TTS provider {provider_name!r}. Available: {list(TTS_REGISTRY)}"
            )
        try:
            cls = _import_class(dotted)
            _tts_instance = cls()
            log.info("voice.tts.provider_loaded provider=%s", provider_name)
        except ImportError as exc:
            # Auto-fallback: try kokoro, then browser
            log.warning("voice.tts.fallback provider=%s error=%s", provider_name, exc)
            for fallback in ("kokoro", "browser"):
                if fallback == provider_name:
                    continue
                try:
                    cls = _import_class(TTS_REGISTRY[fallback])
                    _tts_instance = cls()
                    log.info("voice.tts.fallback_loaded provider=%s", fallback)
                    break
                except ImportError:
                    continue
            if _tts_instance is None:
                raise RuntimeError(f"No TTS provider could be loaded (tried {provider_name})")
        return _tts_instance


async def warmup_providers() -> None:
    """Preload both providers at worker startup."""
    stt = await get_stt()
    tts = await get_tts()
    await asyncio.gather(stt.warmup(), tts.warmup(), return_exceptions=True)
    log.info("voice.providers.warmup_complete stt=%s tts=%s",
             stt.provider_name, tts.provider_name)


async def get_capabilities() -> dict:
    """Return capabilities of the currently loaded STT + TTS providers."""
    stt = await get_stt()
    tts = await get_tts()
    return {
        "stt": {
            "provider":  stt.provider_name,
            "ready":     await stt.is_ready(),
            "streaming": stt.supports_streaming,
        },
        "tts": {
            "provider":       tts.provider_name,
            "ready":          await tts.is_ready(),
            "voice_cloning":  tts.supports_voice_cloning,
            "nonverbal":      tts.supports_nonverbal,
            "sample_rate":    tts.sample_rate,
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
