"""Native STT engine — thin shim over the abstract provider system.

Public API (backwards-compatible):
    await transcribe(audio_bytes, content_type) → dict
    await get_model() → the underlying model object

The actual implementation is in app.voice.providers.stt.faster_whisper.
"""
from __future__ import annotations

import asyncio
from typing import Any

# Keep module-level _model ref for status checks (app/voice/router.py checks stt_engine._model)
_model: Any | None = None
_lock = asyncio.Lock()


async def get_model() -> Any:
    """Return the underlying STT model (lazy-loads via provider)."""
    global _model
    if _model is not None:
        return _model
    async with _lock:
        if _model is not None:
            return _model
        from app.voice.providers import get_stt
        provider = await get_stt()
        await provider.warmup()
        # Expose the internal model ref for status checks
        _model = getattr(provider, "_model", provider)
        return _model


async def transcribe(audio_bytes: bytes, content_type: str) -> dict:
    """Transcribe audio bytes to text.

    Returns:
        {transcript, language, confidence, segments: [{start, end, text}]}
    """
    from app.voice.providers import get_stt
    provider = await get_stt()
    result   = await provider.transcribe(audio_bytes, content_type)
    return result.to_dict()
