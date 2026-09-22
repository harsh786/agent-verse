"""Tests for app/voice/providers/tts/azure_tts.py — Azure Cognitive Services TTS."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.voice.providers.tts.azure_tts import AzureTTS


class TestMetadata:
    def test_provider_metadata(self) -> None:
        tts = AzureTTS()
        assert tts.provider_name == "azure_tts"
        assert tts.sample_rate == 24_000
        assert tts.supports_voice_cloning is False
        assert tts.supports_nonverbal is False
        assert tts.max_text_length == 10_000

    def test_reads_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_TTS_KEY", "secret-key")
        monkeypatch.setenv("AZURE_TTS_REGION", "westus2")
        tts = AzureTTS()
        assert tts._key == "secret-key"
        assert tts._region == "westus2"

    def test_defaults_region_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AZURE_TTS_REGION", raising=False)
        monkeypatch.delenv("AZURE_TTS_KEY", raising=False)
        tts = AzureTTS()
        assert tts._region == "eastus"
        assert tts._key == ""


class TestWarmupAndReady:
    @pytest.mark.asyncio
    async def test_warmup_is_noop(self) -> None:
        tts = AzureTTS()
        await tts.warmup()  # should not raise

    @pytest.mark.asyncio
    async def test_is_ready_false_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AZURE_TTS_KEY", raising=False)
        tts = AzureTTS()
        assert await tts.is_ready() is False

    @pytest.mark.asyncio
    async def test_is_ready_true_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_TTS_KEY", "k")
        tts = AzureTTS()
        assert await tts.is_ready() is True


def _mock_async_client(response: MagicMock) -> MagicMock:
    mock_client_class = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.post = AsyncMock(return_value=response)
    mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
    mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_client_class


class TestSynthesize:
    @pytest.mark.asyncio
    async def test_synthesize_posts_ssml_and_returns_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AZURE_TTS_KEY", "the-key")
        monkeypatch.setenv("AZURE_TTS_REGION", "eastus")
        tts = AzureTTS()

        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.content = b"RIFF...audio-bytes"

        mock_client_class = _mock_async_client(response)
        with patch("httpx.AsyncClient", mock_client_class):
            result = await tts.synthesize("Hello there", language="en", speed=1.0)

        assert result == b"RIFF...audio-bytes"
        mock_ctx = mock_client_class.return_value.__aenter__.return_value
        _, kwargs = mock_ctx.post.call_args
        assert "eastus.tts.speech.microsoft.com" in mock_ctx.post.call_args.args[0]
        assert kwargs["headers"]["Ocp-Apim-Subscription-Key"] == "the-key"
        assert b"Hello there" in kwargs["content"]
        assert b'xml:lang="en"' in kwargs["content"]

    @pytest.mark.asyncio
    async def test_synthesize_uses_voice_id_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AZURE_TTS_KEY", "k")
        tts = AzureTTS()
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.content = b"data"
        mock_client_class = _mock_async_client(response)
        with patch("httpx.AsyncClient", mock_client_class):
            await tts.synthesize("hi", voice_id="en-GB-RyanNeural")
        mock_ctx = mock_client_class.return_value.__aenter__.return_value
        _, kwargs = mock_ctx.post.call_args
        assert b"en-GB-RyanNeural" in kwargs["content"]

    @pytest.mark.asyncio
    async def test_synthesize_raises_on_http_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AZURE_TTS_KEY", "bad-key")
        tts = AzureTTS()
        response = MagicMock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock()
        )
        mock_client_class = _mock_async_client(response)
        with patch("httpx.AsyncClient", mock_client_class):
            with pytest.raises(httpx.HTTPStatusError):
                await tts.synthesize("hi")


class TestSynthesizeStreaming:
    @pytest.mark.asyncio
    async def test_streams_chunks_of_expected_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AZURE_TTS_KEY", "k")
        tts = AzureTTS()
        wav_bytes = b"x" * (4800 * 2 * 2 + 100)  # a bit over two full chunks

        with patch.object(AzureTTS, "synthesize", AsyncMock(return_value=wav_bytes)):
            chunks = [c async for c in tts.synthesize_streaming("hello", language="en")]

        assert b"".join(chunks) == wav_bytes
        assert len(chunks[0]) == 4800 * 2
        assert len(chunks[-1]) == 100

    @pytest.mark.asyncio
    async def test_streams_empty_when_synthesize_returns_empty(self) -> None:
        tts = AzureTTS()
        with patch.object(AzureTTS, "synthesize", AsyncMock(return_value=b"")):
            chunks = [c async for c in tts.synthesize_streaming("hello")]
        assert chunks == []
