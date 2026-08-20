"""Native TTS engine — thin shim over the abstract provider system.

Public API (backwards-compatible):
    await synthesize(text, ...) → WAV bytes
    async for chunk in synthesize_streaming(text, ...): ...
    await warmup()

The actual implementation is in app.voice.providers.tts.* (kokoro/omnivoice/...).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

# Module-level _model ref for status checks (app/voice/router.py checks tts_engine._model)
_model: Any | None = None
_lock = asyncio.Lock()

SAMPLE_RATE = 24_000   # default; actual rate from provider


async def get_model() -> Any:
    """Return the underlying TTS model (lazy-loads via provider)."""
    global _model
    if _model is not None:
        return _model
    async with _lock:
        if _model is not None:
            return _model
        from app.voice.providers import get_tts
        provider = await get_tts()
        await provider.warmup()
        _model = getattr(provider, "_model", None) or getattr(provider, "_kokoro", provider)
        return _model


async def synthesize(
    text: str,
    *,
    ref_audio: bytes | None = None,
    ref_text: str | None   = None,
    language: str          = "en",
    speed: float           = 1.0,
) -> bytes:
    """Synthesise text to WAV bytes."""
    from app.voice.providers import get_tts
    provider = await get_tts()
    return await provider.synthesize(
        text, ref_audio=ref_audio, ref_text=ref_text,
        language=language, speed=speed,
    )


async def synthesize_streaming(
    text: str,
    *,
    ref_audio: bytes | None = None,
    ref_text: str | None   = None,
    language: str          = "en",
) -> AsyncGenerator[bytes, None]:
    """Yield PCM16 audio chunks as an async generator."""
    from app.voice.providers import get_tts
    provider = await get_tts()
    async for chunk in provider.synthesize_streaming(
        text, ref_audio=ref_audio, ref_text=ref_text, language=language,
    ):
        yield chunk


async def warmup() -> None:
    """Preload TTS model at worker startup."""
    from app.voice.providers import get_tts
    provider = await get_tts()
    await provider.warmup()
    global _model
    _model = getattr(provider, "_model", None) or getattr(provider, "_kokoro", provider)
