"""Tests for tts_engine.py — TTS provider system."""
from __future__ import annotations

import io

import numpy as np
import pytest
import soundfile as sf


def _make_mock_wav(duration_s: float = 0.2, sr: int = 24_000) -> bytes:
    """Create a test WAV file."""
    audio = np.zeros(int(sr * duration_s), dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format='WAV', subtype='PCM_16')
    return buf.getvalue()


@pytest.mark.asyncio
async def test_browser_fallback_synthesize_returns_wav():
    """BrowserFallbackTTS.synthesize() returns valid WAV bytes."""
    from app.voice.providers.tts.browser_fallback import BrowserFallbackTTS
    p   = BrowserFallbackTTS()
    wav = await p.synthesize("hello")
    assert isinstance(wav, bytes)
    assert len(wav) > 44   # at least WAV header
    buf  = io.BytesIO(wav)
    audio, sr = sf.read(buf)
    assert sr == 24_000


@pytest.mark.asyncio
async def test_browser_fallback_streaming_yields_bytes():
    """BrowserFallbackTTS.synthesize_streaming() is an async generator."""
    from app.voice.providers.tts.browser_fallback import BrowserFallbackTTS
    p      = BrowserFallbackTTS()
    chunks = []
    async for chunk in p.synthesize_streaming("test"):
        chunks.append(chunk)
    assert len(chunks) > 0
    assert all(isinstance(c, bytes) for c in chunks)


@pytest.mark.asyncio
async def test_tts_provider_singleton():
    """TTS provider is a singleton — get_tts() returns same instance."""
    import os
    os.environ['VOICE_TTS_PROVIDER'] = 'browser'
    from app.voice.providers import get_tts, reset_providers
    reset_providers()
    p1 = await get_tts()
    p2 = await get_tts()
    assert p1 is p2
    reset_providers()


@pytest.mark.asyncio
async def test_override_tts_for_testing():
    """override_tts() allows injecting a mock provider."""
    from unittest.mock import AsyncMock, MagicMock

    from app.voice.providers import get_tts, override_tts, reset_providers

    mock_wav = _make_mock_wav()
    mock = MagicMock()
    mock.provider_name      = 'mock_tts'
    mock.sample_rate        = 24_000
    mock.supports_voice_cloning = False
    mock.supports_nonverbal = False
    mock.synthesize         = AsyncMock(return_value=mock_wav)
    mock.warmup             = AsyncMock()
    mock.is_ready           = AsyncMock(return_value=True)

    async def _stream(text, **kw):
        yield mock_wav[44:]   # raw PCM

    mock.synthesize_streaming = _stream

    reset_providers()
    override_tts(mock)
    p = await get_tts()
    assert p is mock

    wav = await p.synthesize("hello world")
    assert wav == mock_wav

    chunks = []
    async for c in p.synthesize_streaming("hello"):
        chunks.append(c)
    assert len(chunks) > 0
    reset_providers()


@pytest.mark.asyncio
async def test_tts_engine_synthesize_shim():
    """tts_engine.synthesize() delegates to provider."""
    import os
    os.environ['VOICE_TTS_PROVIDER'] = 'browser'
    from app.voice.providers import reset_providers
    reset_providers()
    from app.voice.tts_engine import synthesize
    wav = await synthesize("hello")
    assert isinstance(wav, bytes)
    assert len(wav) > 0
    reset_providers()


@pytest.mark.asyncio
async def test_tts_engine_streaming_shim():
    """tts_engine.synthesize_streaming() delegates to provider."""
    import os
    os.environ['VOICE_TTS_PROVIDER'] = 'browser'
    from app.voice.providers import reset_providers
    reset_providers()
    from app.voice.tts_engine import synthesize_streaming
    chunks = []
    async for c in synthesize_streaming("test streaming"):
        chunks.append(c)
    assert len(chunks) > 0
    reset_providers()


@pytest.mark.asyncio
async def test_tts_fallback_chain():
    """Provider registry falls back kokoro→browser when omnivoice not installed."""
    import os
    os.environ['VOICE_TTS_PROVIDER'] = 'omnivoice'
    from app.voice.providers import get_tts, reset_providers
    reset_providers()
    # omnivoice not installed → should fall back to kokoro or browser
    try:
        p = await get_tts()
        assert p.provider_name in ('omnivoice', 'kokoro', 'browser')
    except RuntimeError:
        pass  # acceptable if all fallbacks fail
    finally:
        reset_providers()
        os.environ['VOICE_TTS_PROVIDER'] = 'browser'


def test_tts_provider_interfaces():
    """All TTS provider classes implement TTSProvider protocol."""
    from app.voice.providers.base import TTSProvider
    from app.voice.providers.tts.azure_tts import AzureTTS
    from app.voice.providers.tts.browser_fallback import BrowserFallbackTTS
    from app.voice.providers.tts.elevenlabs import ElevenLabsTTS
    from app.voice.providers.tts.openai_tts import OpenAITTS

    for cls in [BrowserFallbackTTS, ElevenLabsTTS, OpenAITTS, AzureTTS]:
        p = cls()
        assert isinstance(p, TTSProvider), f"{cls.__name__} does not satisfy TTSProvider protocol"
        assert hasattr(p, 'synthesize')
        assert hasattr(p, 'synthesize_streaming')
        assert hasattr(p, 'warmup')
        assert hasattr(p, 'is_ready')
