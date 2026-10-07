"""GET /v1/voice/status reports the speech models that actually run, and why.

It used to report VOICE_STT_MODEL's Settings default "large-v3-turbo" while
faster-whisper loaded "tiny". Now it reports the resolved model and its source
(Model Registry → env pins → local engine). No model is loaded here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.voice.providers import reset_providers
from app.voice.router import router as voice_router

_ISOLATE_PROVIDER_ENV = True


def _app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Any, call_next: Any) -> Any:
        tenant = MagicMock()
        tenant.tenant_id = "t-1"
        request.state.tenant = tenant
        return await call_next(request)

    app.include_router(voice_router)
    return app


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    import app.ai_router.selection as sel
    from app.ai_router import speech

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    monkeypatch.setattr(speech, "local_stt_available", lambda: True)
    monkeypatch.setattr(speech, "local_tts_engine_available", lambda e: e == "macos_say")
    model_registry.clear_configured()
    model_registry.set_preferences({})
    reset_providers()
    yield
    reset_providers()
    model_registry.clear_configured()
    from app.core.config import get_settings

    get_settings.cache_clear()


async def _status() -> dict:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        resp = await c.get("/v1/voice/status")
    assert resp.status_code == 200
    return resp.json()


async def test_status_reports_the_local_default_that_actually_loads():
    data = await _status()
    assert data["stt_model"] == "tiny"  # not the old "large-v3-turbo" default
    assert data["stt_source"] == "local_default"
    assert data["stt_provider"] == "faster_whisper"
    assert data["tts_model"] == "macos-say"
    assert data["tts_source"] == "local_default"
    assert data["tts_provider"] == "macos_say"


async def test_status_reports_the_env_pinned_whisper_size(monkeypatch):
    monkeypatch.setenv("VOICE_STT_MODEL", "large-v3-turbo")
    data = await _status()
    assert (data["stt_model"], data["stt_source"]) == ("large-v3-turbo", "env_pin")


async def test_status_reports_the_registry_model():
    model_registry.register_configured(
        ModelEndpoint(
            provider="custom",
            model_id="whisper-large-v3",
            display_name="whisper-large-v3",
            capabilities=[ModelCapability.SPEECH_TO_TEXT],
            base_url="http://192.168.1.20:8000/v1",
            extra={"source": "override"},
        )
    )
    model_registry.set_preferences({"speech_to_text": ["custom/whisper-large-v3"]})
    data = await _status()
    assert data["stt_model"] == "whisper-large-v3"
    assert data["stt_source"] == "registry_preference"
    assert data["stt_provider"] == "whisper_api"


async def test_status_without_any_stt_model_says_so(monkeypatch):
    from app.ai_router import speech

    monkeypatch.setattr(speech, "local_stt_available", lambda: False)
    data = await _status()
    assert data["stt_model"] == ""
    assert data["stt_source"] == "not_configured"
    assert data["stt_status"] == "error"
    assert "speech_to_text" in (data["stt_error"] or "")


async def test_tts_with_nothing_installed_is_flagged_degraded(monkeypatch):
    from app.ai_router import speech

    monkeypatch.setattr(speech, "local_tts_engine_available", lambda e: False)
    data = await _status()
    assert data["tts_provider"] == "browser"
    assert data["tts_source"] == "degraded"


# ── provider selection follows the resolver ──────────────────────────────────


def _tts_entry(provider: str, model_id: str, base_url: str | None) -> ModelEndpoint:
    return ModelEndpoint(
        provider=provider,
        model_id=model_id,
        display_name=model_id,
        capabilities=[ModelCapability.TEXT_TO_SPEECH],
        base_url=base_url,
        extra={"source": "override"},
    )


async def test_get_tts_builds_the_registry_endpoint_model():
    from app.voice.providers import get_tts

    model_registry.register_configured(
        _tts_entry("custom", "kokoro-82m", "http://192.168.1.20:8880/v1")
    )
    tts = await get_tts()
    assert tts.provider_name == "openai_tts"
    assert tts.model == "kokoro-82m"  # type: ignore[attr-defined]
    assert tts._base() == "http://192.168.1.20:8880/v1"  # type: ignore[attr-defined]
    assert tts.resolved_source == "registry_cheapest"  # type: ignore[attr-defined]


async def test_get_tts_builds_an_elevenlabs_registry_model():
    from app.voice.providers import get_tts

    model_registry.register_configured(_tts_entry("elevenlabs", "eleven_flash_v2_5", None))
    tts = await get_tts()
    assert tts.provider_name == "elevenlabs"
    assert tts.model == "eleven_flash_v2_5"  # type: ignore[attr-defined]


async def test_get_stt_builds_local_faster_whisper_with_the_resolved_size(monkeypatch):
    from app.voice.providers import get_stt

    monkeypatch.setenv("VOICE_STT_MODEL", "base")
    stt = await get_stt()
    assert stt.provider_name == "faster_whisper"
    assert stt.model_name == "base"  # type: ignore[attr-defined]
    assert await stt.is_ready() is False  # nothing loaded
