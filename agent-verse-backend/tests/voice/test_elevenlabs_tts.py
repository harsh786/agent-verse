"""Tests for app/voice/providers/tts/elevenlabs.py — ElevenLabsTTS.

httpx.AsyncClient is patched so no real network call is ever made. `pydub` is
not installed in this sandbox, so the "pydub missing -> return raw mp3"
fallback is exercised naturally; the "pydub available -> convert to wav"
branch is exercised by injecting a fake `pydub` module.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.voice.providers.tts.elevenlabs import ElevenLabsTTS


def _patch_httpx_post(content: bytes = b"fake-mp3-bytes", status: int = 200):
    def _factory(*args, **kwargs):
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=False)
        resp = MagicMock()
        resp.content = content
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
    tts = ElevenLabsTTS()
    assert tts.provider_name == "elevenlabs"
    assert tts.sample_rate == 44_100
    assert tts.supports_voice_cloning is True
    assert tts.supports_nonverbal is False
    assert tts.max_text_length == 5000


@pytest.mark.asyncio
async def test_warmup_is_noop():
    tts = ElevenLabsTTS()
    await tts.warmup()


@pytest.mark.asyncio
async def test_is_ready_true_when_key_present(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "secret-key")
    tts = ElevenLabsTTS()
    assert await tts.is_ready() is True


@pytest.mark.asyncio
async def test_is_ready_false_when_key_missing(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    tts = ElevenLabsTTS()
    assert await tts.is_ready() is False


@pytest.mark.asyncio
async def test_synthesize_falls_back_to_mp3_when_pydub_missing(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "secret-key")
    tts = ElevenLabsTTS()
    with (
        _patch_httpx_post(content=b"raw-mp3-data"),
        patch.dict(sys.modules, {"pydub": None}),
    ):
        result = await tts.synthesize("hello world")
    assert result == b"raw-mp3-data"


@pytest.mark.asyncio
async def test_synthesize_converts_to_wav_when_pydub_available(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "secret-key")

    fake_pydub = types.ModuleType("pydub")

    class _FakeSegment:
        @staticmethod
        def from_mp3(buf):
            return _FakeSegment()

        def export(self, buf, format="wav"):  # noqa: A002 - matches pydub's real signature
            buf.write(b"RIFF-fake-wav-data")
            return buf

    fake_pydub.AudioSegment = _FakeSegment

    tts = ElevenLabsTTS()
    with (
        _patch_httpx_post(content=b"raw-mp3-data"),
        patch.dict(sys.modules, {"pydub": fake_pydub}),
    ):
        result = await tts.synthesize("hello world")

    assert result == b"RIFF-fake-wav-data"


@pytest.mark.asyncio
async def test_synthesize_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "secret-key")
    tts = ElevenLabsTTS()
    with _patch_httpx_post(status=401):
        with pytest.raises(httpx.HTTPStatusError):
            await tts.synthesize("hello world")


@pytest.mark.asyncio
async def test_synthesize_uses_voice_id_override(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "secret-key")
    tts = ElevenLabsTTS()

    captured = {}

    def _factory(*args, **kwargs):
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=False)
        resp = MagicMock()
        resp.content = b"raw-mp3"
        resp.raise_for_status = MagicMock(return_value=None)

        async def _post(url, **kw):
            captured["url"] = url
            captured["headers"] = kw.get("headers")
            captured["json"] = kw.get("json")
            return resp

        ctx.post = _post
        return ctx

    with patch("httpx.AsyncClient", _factory), patch.dict(sys.modules, {"pydub": None}):
        await tts.synthesize("hi", voice_id="custom-voice")

    assert captured["url"].endswith("/custom-voice")
    assert captured["headers"]["xi-api-key"] == "secret-key"
    assert captured["json"]["text"] == "hi"


@pytest.mark.asyncio
async def test_synthesize_uses_env_voice_default(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "secret-key")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "env-voice")
    tts = ElevenLabsTTS()

    captured = {}

    def _factory(*args, **kwargs):
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=False)
        resp = MagicMock()
        resp.content = b"raw-mp3"
        resp.raise_for_status = MagicMock(return_value=None)

        async def _post(url, **kw):
            captured["url"] = url
            return resp

        ctx.post = _post
        return ctx

    with patch("httpx.AsyncClient", _factory), patch.dict(sys.modules, {"pydub": None}):
        await tts.synthesize("hi")

    assert captured["url"].endswith("/env-voice")


@pytest.mark.asyncio
async def test_synthesize_streaming_chunks(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "secret-key")
    tts = ElevenLabsTTS()
    with (
        _patch_httpx_post(content=b"x" * 20_000),
        patch.dict(sys.modules, {"pydub": None}),
    ):
        chunks = [c async for c in tts.synthesize_streaming("hello there")]

    assert len(chunks) > 1
    assert all(isinstance(c, bytes) for c in chunks)
    assert b"".join(chunks) == b"x" * 20_000
