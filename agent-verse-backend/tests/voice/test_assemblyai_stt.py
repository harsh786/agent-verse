"""Tests for app/voice/providers/stt/assemblyai.py — AssemblyAISTT.

httpx.AsyncClient is patched; no real network call is made. asyncio.sleep is
patched to no-op so the poll loop and the 30-attempt timeout run instantly.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.voice.providers.stt.assemblyai import AssemblyAISTT


def _client_factory(upload_json, submit_json, poll_json_sequence, statuses=None):
    """Build a fake httpx.AsyncClient() context manager.

    poll_json_sequence: list of dicts returned on successive GET polls.
    """
    poll_iter = iter(poll_json_sequence)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=ctx)
    ctx.__aexit__ = AsyncMock(return_value=False)

    async def _post(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock(return_value=None)
        if "upload" in url:
            resp.json = MagicMock(return_value=upload_json)
        else:
            resp.json = MagicMock(return_value=submit_json)
        return resp

    async def _get(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock(return_value=None)
        resp.json = MagicMock(return_value=next(poll_iter))
        return resp

    ctx.post = _post
    ctx.get = _get
    return lambda *a, **kw: ctx


def test_metadata():
    stt = AssemblyAISTT()
    assert stt.provider_name == "assemblyai"
    assert stt.supports_streaming is False


@pytest.mark.asyncio
async def test_is_ready_false_before_warmup():
    stt = AssemblyAISTT()
    assert await stt.is_ready() is False


@pytest.mark.asyncio
async def test_warmup_sets_ready_from_env(monkeypatch):
    monkeypatch.setenv("ASSEMBLY_AI_KEY", "secret")
    stt = AssemblyAISTT()
    await stt.warmup()
    assert await stt.is_ready() is True


@pytest.mark.asyncio
async def test_warmup_not_ready_without_key(monkeypatch):
    monkeypatch.delenv("ASSEMBLY_AI_KEY", raising=False)
    stt = AssemblyAISTT()
    await stt.warmup()
    assert await stt.is_ready() is False


@pytest.mark.asyncio
async def test_transcribe_raises_without_key(monkeypatch):
    monkeypatch.delenv("ASSEMBLY_AI_KEY", raising=False)
    stt = AssemblyAISTT()
    with pytest.raises(RuntimeError, match="ASSEMBLY_AI_KEY"):
        await stt.transcribe(b"audio", "audio/wav")


@pytest.mark.asyncio
async def test_transcribe_success_after_polling(monkeypatch):
    monkeypatch.setenv("ASSEMBLY_AI_KEY", "secret")
    factory = _client_factory(
        upload_json={"upload_url": "https://cdn.example/upload/abc"},
        submit_json={"id": "job-1"},
        poll_json_sequence=[
            {"status": "processing"},
            {"status": "processing"},
            {"status": "completed", "text": "hello world"},
        ],
    )
    stt = AssemblyAISTT()
    with patch("httpx.AsyncClient", factory), patch("asyncio.sleep", new=AsyncMock()):
        result = await stt.transcribe(b"audio-bytes", "audio/wav")

    assert result.transcript == "hello world"
    assert result.language == "en"
    assert result.confidence == 0.9
    assert result.provider == "assemblyai"


@pytest.mark.asyncio
async def test_transcribe_raises_on_error_status(monkeypatch):
    monkeypatch.setenv("ASSEMBLY_AI_KEY", "secret")
    factory = _client_factory(
        upload_json={"upload_url": "https://cdn.example/upload/abc"},
        submit_json={"id": "job-1"},
        poll_json_sequence=[{"status": "error", "error": "bad audio format"}],
    )
    stt = AssemblyAISTT()
    with patch("httpx.AsyncClient", factory), patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(RuntimeError, match="bad audio format"):
            await stt.transcribe(b"audio-bytes", "audio/wav")


@pytest.mark.asyncio
async def test_transcribe_times_out_after_max_polls(monkeypatch):
    monkeypatch.setenv("ASSEMBLY_AI_KEY", "secret")
    # Always "processing" -> exhausts the 30-attempt loop -> TimeoutError.
    factory = _client_factory(
        upload_json={"upload_url": "https://cdn.example/upload/abc"},
        submit_json={"id": "job-1"},
        poll_json_sequence=[{"status": "processing"}] * 30,
    )
    stt = AssemblyAISTT()
    with patch("httpx.AsyncClient", factory), patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(TimeoutError):
            await stt.transcribe(b"audio-bytes", "audio/wav")


@pytest.mark.asyncio
async def test_transcribe_raises_on_upload_http_error(monkeypatch):
    monkeypatch.setenv("ASSEMBLY_AI_KEY", "secret")

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=ctx)
    ctx.__aexit__ = AsyncMock(return_value=False)

    async def _post(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock(side_effect=RuntimeError("HTTP 500"))
        return resp

    ctx.post = _post

    stt = AssemblyAISTT()
    with patch("httpx.AsyncClient", lambda *a, **kw: ctx):
        with pytest.raises(RuntimeError, match="HTTP 500"):
            await stt.transcribe(b"audio-bytes", "audio/wav")
