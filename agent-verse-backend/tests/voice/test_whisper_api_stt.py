"""Tests for app/voice/providers/stt/whisper_api.py — WhisperAPISTT.

The OpenAI-compatible ``/audio/transcriptions`` STT runs the model, endpoint and
key resolved from the Model Registry (or the env pin), never a hardcoded
"whisper-1". The network edge is an ``httpx.MockTransport`` (no real call).
"""
from __future__ import annotations

import httpx
import pytest

from app.voice.providers.stt.whisper_api import WhisperAPISTT

_ISOLATE_PROVIDER_ENV = True


@pytest.fixture
def transport(monkeypatch):
    """Route the SSRF-pinned endpoint client through a MockTransport."""
    calls: list[httpx.Request] = []
    state = {"status": 200, "json": {"text": "hello world", "language": "fr"}}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(state["status"], json=state["json"])

    def _client(**kwargs):
        kwargs.pop("follow_redirects", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.ai_router.model_endpoints.endpoint_http_client", _client)
    return calls, state


def test_metadata():
    stt = WhisperAPISTT()
    assert stt.provider_name == "whisper_api"
    assert stt.supports_streaming is False


@pytest.mark.asyncio
async def test_is_ready_false_before_warmup():
    stt = WhisperAPISTT(model="whisper-large-v3")
    assert await stt.is_ready() is False


@pytest.mark.asyncio
async def test_warmup_ready_with_model_and_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    stt = WhisperAPISTT(model="whisper-large-v3")
    await stt.warmup()
    assert await stt.is_ready() is True


@pytest.mark.asyncio
async def test_warmup_not_ready_without_a_model(monkeypatch):
    """No configured model → not ready (it never guesses a vendor model)."""
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    stt = WhisperAPISTT()
    await stt.warmup()
    assert await stt.is_ready() is False


@pytest.mark.asyncio
async def test_warmup_not_ready_without_key_on_the_public_api(monkeypatch):
    stt = WhisperAPISTT(model="whisper-large-v3")
    await stt.warmup()
    assert await stt.is_ready() is False


@pytest.mark.asyncio
async def test_own_endpoint_needs_no_key():
    stt = WhisperAPISTT(model="whisper-large-v3", base_url="http://10.0.0.5:8000/v1")
    await stt.warmup()
    assert await stt.is_ready() is True


@pytest.mark.asyncio
async def test_transcribe_raises_without_key(monkeypatch):
    stt = WhisperAPISTT(model="whisper-large-v3")
    with pytest.raises(RuntimeError, match="no API key"):
        await stt.transcribe(b"audio", "audio/wav")


@pytest.mark.asyncio
async def test_transcribe_without_a_model_is_an_honest_error(monkeypatch):
    from app.ai_router.resolve import ModelNotConfiguredError

    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    with pytest.raises(ModelNotConfiguredError, match="speech_to_text"):
        await WhisperAPISTT().transcribe(b"audio", "audio/wav")


@pytest.mark.asyncio
async def test_transcribe_success_returns_transcript(monkeypatch, transport):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    stt = WhisperAPISTT(model="whisper-large-v3")
    result = await stt.transcribe(b"audio-bytes", "audio/wav")
    assert result.transcript == "hello world"
    assert result.language == "fr"
    assert result.confidence == 0.95
    assert result.provider == "whisper_api"


@pytest.mark.asyncio
async def test_transcribe_defaults_language_when_missing(monkeypatch, transport):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    _calls, state = transport
    state["json"] = {"text": "hi"}
    result = await WhisperAPISTT(model="m").transcribe(b"audio-bytes", "audio/wav")
    assert result.language == "en"
    assert result.transcript == "hi"


@pytest.mark.asyncio
async def test_transcribe_without_text_is_an_error(monkeypatch, transport):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    _calls, state = transport
    state["json"] = {}
    with pytest.raises(ValueError, match="no transcription"):
        await WhisperAPISTT(model="m").transcribe(b"audio-bytes", "audio/wav")


@pytest.mark.asyncio
async def test_transcribe_raises_on_http_error(monkeypatch, transport):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    _calls, state = transport
    state["status"] = 429
    with pytest.raises(httpx.HTTPStatusError):
        await WhisperAPISTT(model="m").transcribe(b"audio-bytes", "audio/wav")


@pytest.mark.asyncio
async def test_transcribe_sends_the_resolved_model_to_its_endpoint(transport):
    """A registry model is called at its own base_url with its model id."""
    calls, _state = transport
    stt = WhisperAPISTT(
        model="Systran/faster-whisper-small", base_url="http://10.0.0.5:8000/v1", provider="custom"
    )
    await stt.transcribe(b"raw-audio", "audio/mpeg")

    request = calls[0]
    assert str(request.url) == "http://10.0.0.5:8000/v1/audio/transcriptions"
    body = request.content
    assert b'name="model"' in body and b"Systran/faster-whisper-small" in body
    assert b"raw-audio" in body
    assert b"audio/mpeg" in body
    assert b"whisper-1" not in body


@pytest.mark.asyncio
async def test_env_pin_model_on_the_openai_endpoint(monkeypatch, transport):
    calls, _state = transport
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    monkeypatch.setenv("VOICE_STT_MODEL", "gpt-4o-mini-transcribe")
    await WhisperAPISTT().transcribe(b"raw-audio", "audio/wav")
    request = calls[0]
    assert str(request.url) == "https://api.openai.com/v1/audio/transcriptions"
    assert request.headers["Authorization"] == "Bearer secret-key"
    assert b"gpt-4o-mini-transcribe" in request.content
