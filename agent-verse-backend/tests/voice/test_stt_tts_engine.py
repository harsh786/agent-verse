"""Tests for app/voice/stt_engine.py and app/voice/tts_engine.py shims."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.voice.stt_engine as stt_engine
import app.voice.tts_engine as tts_engine


@pytest.fixture(autouse=True)
def _reset_module_state():
    stt_engine._model = None
    tts_engine._model = None
    yield
    stt_engine._model = None
    tts_engine._model = None


# ── stt_engine.get_model ──────────────────────────────────────────────────────


class TestSttGetModel:
    @pytest.mark.asyncio
    async def test_lazy_loads_and_caches(self) -> None:
        provider = AsyncMock()
        provider.warmup = AsyncMock()
        provider._model = "the-real-model"

        with patch("app.voice.providers.get_stt", AsyncMock(return_value=provider)) as mock_get:
            model1 = await stt_engine.get_model()
            model2 = await stt_engine.get_model()

        assert model1 == "the-real-model"
        assert model2 == "the-real-model"
        # provider fetched + warmed up only once due to caching
        mock_get.assert_awaited_once()
        provider.warmup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_provider_when_no_internal_model_attr(self) -> None:
        provider = AsyncMock(spec=["warmup"])
        provider.warmup = AsyncMock()

        with patch("app.voice.providers.get_stt", AsyncMock(return_value=provider)):
            model = await stt_engine.get_model()

        assert model is provider

    @pytest.mark.asyncio
    async def test_returns_cached_model_without_reacquiring_lock(self) -> None:
        stt_engine._model = "already-cached"
        with patch("app.voice.providers.get_stt", AsyncMock()) as mock_get:
            model = await stt_engine.get_model()
        assert model == "already-cached"
        mock_get.assert_not_called()


# ── stt_engine.transcribe ─────────────────────────────────────────────────────


class TestSttTranscribe:
    @pytest.mark.asyncio
    async def test_transcribe_delegates_to_provider(self) -> None:
        result_obj = MagicMock()
        result_obj.to_dict.return_value = {
            "transcript": "hello world",
            "language": "en",
            "confidence": 0.95,
            "segments": [],
        }
        provider = AsyncMock()
        provider.transcribe = AsyncMock(return_value=result_obj)

        with patch("app.voice.providers.get_stt", AsyncMock(return_value=provider)):
            result = await stt_engine.transcribe(b"audiobytes", "audio/wav")

        assert result["transcript"] == "hello world"
        provider.transcribe.assert_awaited_once_with(b"audiobytes", "audio/wav")


# ── tts_engine.get_model ──────────────────────────────────────────────────────


class TestTtsGetModel:
    @pytest.mark.asyncio
    async def test_lazy_loads_using_model_attr(self) -> None:
        provider = AsyncMock()
        provider.warmup = AsyncMock()
        provider._model = "tts-model"

        with patch("app.voice.providers.get_tts", AsyncMock(return_value=provider)) as mock_get:
            model1 = await tts_engine.get_model()
            model2 = await tts_engine.get_model()

        assert model1 == "tts-model"
        assert model2 == "tts-model"
        mock_get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_kokoro_attr(self) -> None:
        provider = AsyncMock(spec=["warmup", "_kokoro"])
        provider.warmup = AsyncMock()
        provider._kokoro = "kokoro-model"

        with patch("app.voice.providers.get_tts", AsyncMock(return_value=provider)):
            model = await tts_engine.get_model()

        assert model == "kokoro-model"

    @pytest.mark.asyncio
    async def test_falls_back_to_provider_itself(self) -> None:
        provider = AsyncMock(spec=["warmup"])
        provider.warmup = AsyncMock()

        with patch("app.voice.providers.get_tts", AsyncMock(return_value=provider)):
            model = await tts_engine.get_model()

        assert model is provider

    @pytest.mark.asyncio
    async def test_returns_cached_model(self) -> None:
        tts_engine._model = "cached"
        with patch("app.voice.providers.get_tts", AsyncMock()) as mock_get:
            model = await tts_engine.get_model()
        assert model == "cached"
        mock_get.assert_not_called()


# ── tts_engine.synthesize ─────────────────────────────────────────────────────


class TestTtsSynthesize:
    @pytest.mark.asyncio
    async def test_synthesize_delegates_with_kwargs(self) -> None:
        provider = AsyncMock()
        provider.synthesize = AsyncMock(return_value=b"WAVDATA")

        with patch("app.voice.providers.get_tts", AsyncMock(return_value=provider)):
            result = await tts_engine.synthesize(
                "hello", ref_audio=b"ref", ref_text="ref text", language="es", speed=1.2
            )

        assert result == b"WAVDATA"
        provider.synthesize.assert_awaited_once_with(
            "hello", ref_audio=b"ref", ref_text="ref text", language="es", speed=1.2
        )


# ── tts_engine.synthesize_streaming ────────────────────────────────────────────


class TestTtsSynthesizeStreaming:
    @pytest.mark.asyncio
    async def test_yields_chunks_from_provider(self) -> None:
        async def fake_stream(text, ref_audio=None, ref_text=None, language="en"):
            yield b"chunk1"
            yield b"chunk2"

        provider = MagicMock()
        provider.synthesize_streaming = fake_stream

        chunks = []
        with patch("app.voice.providers.get_tts", AsyncMock(return_value=provider)):
            async for c in tts_engine.synthesize_streaming("hi", language="en"):
                chunks.append(c)

        assert chunks == [b"chunk1", b"chunk2"]


# ── tts_engine.warmup ──────────────────────────────────────────────────────────


class TestTtsWarmup:
    @pytest.mark.asyncio
    async def test_warmup_sets_module_model(self) -> None:
        provider = AsyncMock()
        provider.warmup = AsyncMock()
        provider._model = "warmed-model"

        with patch("app.voice.providers.get_tts", AsyncMock(return_value=provider)):
            await tts_engine.warmup()

        assert tts_engine._model == "warmed-model"
        provider.warmup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_warmup_falls_back_to_kokoro(self) -> None:
        provider = AsyncMock(spec=["warmup", "_kokoro"])
        provider.warmup = AsyncMock()
        provider._kokoro = "kokoro"

        with patch("app.voice.providers.get_tts", AsyncMock(return_value=provider)):
            await tts_engine.warmup()

        assert tts_engine._model == "kokoro"
