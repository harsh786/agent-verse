"""Tests for stt_engine.py — faster-whisper STT."""
from __future__ import annotations

import io

import numpy as np
import pytest
import soundfile as sf


def _make_wav(duration_s: float = 0.5, sr: int = 16_000) -> bytes:
    """Create a minimal silent WAV file for testing."""
    silence = np.zeros(int(sr * duration_s), dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, silence, sr, format='WAV', subtype='PCM_16')
    return buf.getvalue()


@pytest.mark.asyncio
async def test_transcribe_returns_correct_schema():
    """transcribe() returns dict with required keys."""
    from app.voice.stt_engine import transcribe
    wav = _make_wav()
    result = await transcribe(wav, 'audio/wav')
    assert 'transcript' in result
    assert 'language' in result
    assert 'confidence' in result
    assert 'segments' in result
    assert isinstance(result['segments'], list)
    assert isinstance(result['confidence'], float)
    assert 0.0 <= result['confidence'] <= 1.0


@pytest.mark.asyncio
async def test_transcribe_silence_returns_empty_or_short():
    """Silence returns empty or near-empty transcript."""
    from app.voice.stt_engine import transcribe
    wav = _make_wav(duration_s=0.5)
    result = await transcribe(wav, 'audio/wav')
    # Silence should produce empty or very short transcript
    assert len(result['transcript']) < 50


@pytest.mark.asyncio
async def test_stt_provider_singleton():
    """STT provider is loaded as singleton — get_stt() returns same instance."""
    import os
    os.environ['VOICE_STT_PROVIDER'] = 'faster_whisper'
    from app.voice.providers import get_stt, reset_providers
    reset_providers()
    p1 = await get_stt()
    p2 = await get_stt()
    assert p1 is p2
    reset_providers()


@pytest.mark.asyncio
async def test_faster_whisper_provider_interface():
    """FasterWhisperSTT implements STTProvider protocol."""
    from app.voice.providers.base import STTProvider
    from app.voice.providers.stt.faster_whisper import FasterWhisperSTT
    p = FasterWhisperSTT()
    assert isinstance(p, STTProvider)
    assert p.provider_name == 'faster_whisper'
    assert hasattr(p, 'transcribe')
    assert hasattr(p, 'warmup')
    assert hasattr(p, 'is_ready')


@pytest.mark.asyncio
async def test_whisper_api_provider_interface():
    """WhisperAPISTT implements STTProvider protocol."""
    from app.voice.providers.base import STTProvider
    from app.voice.providers.stt.whisper_api import WhisperAPISTT
    p = WhisperAPISTT()
    assert isinstance(p, STTProvider)
    assert p.provider_name == 'whisper_api'


@pytest.mark.asyncio
async def test_assemblyai_provider_interface():
    """AssemblyAISTT implements STTProvider protocol."""
    from app.voice.providers.base import STTProvider
    from app.voice.providers.stt.assemblyai import AssemblyAISTT
    p = AssemblyAISTT()
    assert isinstance(p, STTProvider)
    assert p.provider_name == 'assemblyai'


@pytest.mark.asyncio
async def test_override_stt_for_testing():
    """override_stt() allows injecting a mock provider."""
    from unittest.mock import AsyncMock, MagicMock

    from app.voice.providers import get_stt, override_stt, reset_providers
    from app.voice.providers.base import TranscriptResult

    mock_provider = MagicMock()
    mock_provider.provider_name = 'mock_stt'
    mock_provider.supports_streaming = False
    mock_provider.transcribe = AsyncMock(return_value=TranscriptResult(
        transcript='hello world', language='en', confidence=0.99, provider='mock_stt'
    ))
    mock_provider.warmup = AsyncMock()
    mock_provider.is_ready = AsyncMock(return_value=True)

    reset_providers()
    override_stt(mock_provider)
    provider = await get_stt()
    assert provider is mock_provider
    result = await provider.transcribe(b'audio', 'audio/wav')
    assert result.transcript == 'hello world'
    reset_providers()
