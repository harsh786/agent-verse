"""Tests for app/voice/providers/stt/whisper_api.py — WhisperAPISTT.

httpx.AsyncClient is patched; no real network call is made.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.voice.providers.stt.whisper_api import WhisperAPISTT


def _patch_httpx_post(json_body, status: int = 200):
    def _factory(*args, **kwargs):
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=False)
        resp = MagicMock()
        resp.json = MagicMock(return_value=json_body)
        if status >= 400:
            resp.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError(
                    f"HTTP {status}", request=MagicMock(), response=MagicMock(status_code=status)
                )
            )
        else:
            resp.raise_for_status = MagicMock(return_value=None)
        ctx.post = AsyncMock(return_value=resp)
        return ctx

    return patch("httpx.AsyncClient", _factory)


def test_metadata():
    stt = WhisperAPISTT()
    assert stt.provider_name == "whisper_api"
    assert stt.supports_streaming is False


@pytest.mark.asyncio
async def test_is_ready_false_before_warmup():
    stt = WhisperAPISTT()
    assert await stt.is_ready() is False


@pytest.mark.asyncio
async def test_warmup_sets_ready_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    stt = WhisperAPISTT()
    await stt.warmup()
    assert await stt.is_ready() is True


@pytest.mark.asyncio
async def test_warmup_not_ready_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    stt = WhisperAPISTT()
    await stt.warmup()
    assert await stt.is_ready() is False


@pytest.mark.asyncio
async def test_transcribe_raises_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    stt = WhisperAPISTT()
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        await stt.transcribe(b"audio", "audio/wav")


@pytest.mark.asyncio
async def test_transcribe_success_returns_transcript(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    stt = WhisperAPISTT()
    with _patch_httpx_post({"text": "hello world", "language": "fr"}):
        result = await stt.transcribe(b"audio-bytes", "audio/wav")

    assert result.transcript == "hello world"
    assert result.language == "fr"
    assert result.confidence == 0.95
    assert result.provider == "whisper_api"


@pytest.mark.asyncio
async def test_transcribe_defaults_language_when_missing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    stt = WhisperAPISTT()
    with _patch_httpx_post({"text": "hi"}):
        result = await stt.transcribe(b"audio-bytes", "audio/wav")

    assert result.language == "en"
    assert result.transcript == "hi"


@pytest.mark.asyncio
async def test_transcribe_defaults_transcript_when_missing_text(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    stt = WhisperAPISTT()
    with _patch_httpx_post({}):
        result = await stt.transcribe(b"audio-bytes", "audio/wav")

    assert result.transcript == ""


@pytest.mark.asyncio
async def test_transcribe_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    stt = WhisperAPISTT()
    with _patch_httpx_post({}, status=429):
        with pytest.raises(httpx.HTTPStatusError):
            await stt.transcribe(b"audio-bytes", "audio/wav")


@pytest.mark.asyncio
async def test_transcribe_sends_expected_request(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    stt = WhisperAPISTT()

    captured = {}

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=ctx)
    ctx.__aexit__ = AsyncMock(return_value=False)

    async def _post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["data"] = kwargs.get("data")
        captured["files"] = kwargs.get("files")
        resp = MagicMock()
        resp.raise_for_status = MagicMock(return_value=None)
        resp.json = MagicMock(return_value={"text": "hi"})
        return resp

    ctx.post = _post

    with patch("httpx.AsyncClient", lambda *a, **kw: ctx):
        await stt.transcribe(b"raw-audio", "audio/mpeg")

    assert captured["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert captured["headers"]["Authorization"] == "Bearer secret-key"
    assert captured["data"]["model"] == "whisper-1"
    assert captured["files"]["file"][1] == b"raw-audio"
    assert captured["files"]["file"][2] == "audio/mpeg"
