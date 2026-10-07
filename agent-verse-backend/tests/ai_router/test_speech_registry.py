"""Speech models come from the Model Registry like every other capability.

* ``resolve_stt`` / ``resolve_tts``: registry preference order → env pins →
  installed local engine → ``ModelNotConfiguredError`` (never a vendor literal);
* the seeder registers the env / settings speech pins, merging into an existing
  entry of the same model instead of replacing it;
* selection maps the speech tasks to their capabilities;
* the preferences API accepts ``speech_to_text`` / ``text_to_speech``;
* Test connection probes ``/audio/transcriptions`` with a generated WAV and
  ``/audio/speech`` checking that audio bytes come back (MockTransport only).
"""

from __future__ import annotations

import io
import wave

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai_router import speech
from app.ai_router.models import ModelCapability, ModelEndpoint, TaskType
from app.ai_router.registry import ModelRegistry, model_registry
from app.ai_router.registry_store import ModelRegistryStore, set_model_registry_store
from app.ai_router.resolve import ModelNotConfiguredError, resolve_stt, resolve_tts
from app.api.model_registry import router as models_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_ISOLATE_PROVIDER_ENV = True

_STT = ModelCapability.SPEECH_TO_TEXT
_TTS = ModelCapability.TEXT_TO_SPEECH
_LAN = "http://192.168.63.104:8000/v1"
_ADMIN_CTX = TenantContext(
    tenant_id="tid-owner", plan=PlanTier.ENTERPRISE, api_key_id="kid-a", roles=("admin",)
)
_ADMIN = {"X-API-Key": "ak_admin"}


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v):
        self.store[k] = v

    def incr(self, k):
        self.store[k] = str(int(self.store.get(k) or 0) + 1)
        return int(self.store[k])


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    for var in ("VOICE_DEVICE", "ELEVENLABS_MODEL_ID", "NVIDIA_AUDIO_MODEL"):
        monkeypatch.delenv(var, raising=False)
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})
    from app.core.config import get_settings

    get_settings.cache_clear()  # never leave Settings built with this test's pins


def _speech_model(model_id, cap, *, provider="custom", base_url=_LAN, cost=0.0, source="override"):
    return ModelEndpoint(
        provider=provider,
        model_id=model_id,
        display_name=model_id,
        capabilities=[cap],
        cost_per_1k_input=cost,
        base_url=base_url,
        extra={"source": source},
    )


# ── resolver order ───────────────────────────────────────────────────────────


def test_stt_registry_model_comes_first_with_its_own_endpoint(monkeypatch):
    monkeypatch.setenv("VOICE_STT_MODEL", "base")  # an env pin exists, the registry wins
    models = [_speech_model("whisper-large-v3", _STT)]
    res = resolve_stt(models=models, preferences=[], local_available=True)
    assert (res.model, res.provider, res.base_url) == ("whisper-large-v3", "custom", _LAN)
    assert res.source == "registry_cheapest"
    assert res.targets[0].kind == "endpoint"
    # Then the env pin, then the local tier — the failover chain.
    assert res.fallbacks == ("local/base",)


def test_stt_preference_order_decides_and_is_labelled(monkeypatch):
    cheap = _speech_model("whisper-small", _STT, cost=0.0)
    pricey = _speech_model("whisper-large-v3", _STT, cost=0.01, base_url="http://10.0.0.9:8000/v1")
    res = resolve_stt(
        models=[cheap, pricey], preferences=["custom/whisper-large-v3"], local_available=False
    )
    assert res.model == "whisper-large-v3"
    assert res.source == "registry_preference"
    assert res.base_url == "http://10.0.0.9:8000/v1"
    assert res.fallbacks == ("custom/whisper-small",)


def test_stt_env_pins_follow_an_empty_registry(monkeypatch):
    monkeypatch.setenv("AUDIO_MODEL", "whisper-large-v3-turbo")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://10.1.1.1:9000/v1")
    res = resolve_stt(models=[], local_available=False)
    assert res.model == "whisper-large-v3-turbo"
    assert res.source == "env_pin"
    assert res.base_url == "http://10.1.1.1:9000/v1"


def test_stt_whisper_api_pin_needs_a_model(monkeypatch):
    monkeypatch.setenv("VOICE_STT_PROVIDER", "whisper_api")
    # No VOICE_STT_MODEL / AUDIO_MODEL: no vendor default is invented.
    with pytest.raises(ModelNotConfiguredError):
        resolve_stt(models=[], local_available=False)
    monkeypatch.setenv("VOICE_STT_MODEL", "gpt-4o-mini-transcribe")
    res = resolve_stt(models=[], local_available=False)
    assert (res.model, res.provider, res.source) == ("gpt-4o-mini-transcribe", "openai", "env_pin")


def test_stt_local_default_is_tiny_faster_whisper(monkeypatch):
    res = resolve_stt(models=[], local_available=True)
    assert (res.model, res.provider, res.source) == ("tiny", "local", "local_default")
    assert res.targets[0].engine == "faster_whisper"


def test_stt_local_default_honours_voice_stt_model_as_an_env_pin(monkeypatch):
    monkeypatch.setenv("VOICE_STT_MODEL", "large-v3-turbo")
    monkeypatch.setattr(speech, "local_stt_available", lambda: True)
    res = resolve_stt(models=[], local_available=True)
    assert (res.model, res.source) == ("large-v3-turbo", "env_pin")


def test_stt_nothing_configured_is_an_honest_error():
    with pytest.raises(ModelNotConfiguredError) as exc:
        resolve_stt(models=[], local_available=False)
    assert exc.value.capability == "speech_to_text"
    assert "Model Registry" in exc.value.hint


def test_stt_local_registry_entry_skipped_when_engine_missing(monkeypatch):
    monkeypatch.setattr(speech, "local_stt_available", lambda: False)
    local = _speech_model("large-v3", _STT, provider="local", base_url=None)
    with pytest.raises(ModelNotConfiguredError):
        resolve_stt(models=[local], local_available=False)
    monkeypatch.setattr(speech, "local_stt_available", lambda: True)
    res = resolve_stt(models=[local], local_available=False)
    assert (res.model, res.provider, res.targets[0].engine) == ("large-v3", "local", "faster_whisper")


def test_stt_resolves_from_the_global_registry():
    model_registry.register_configured(_speech_model("whisper-large-v3", _STT))
    res = resolve_stt(local_available=False)
    assert res.model == "whisper-large-v3"
    assert res.targets[0].entry is not None


def test_tts_order_registry_env_local_error(monkeypatch):
    reg = [_speech_model("kokoro-82m", _TTS)]
    monkeypatch.setenv("VOICE_TTS_PROVIDER", "openai_tts")
    monkeypatch.setenv("VOICE_TTS_MODEL", "gpt-4o-mini-tts")
    res = resolve_tts(models=reg, preferences=[], local_available=True)
    assert (res.model, res.base_url) == ("kokoro-82m", _LAN)
    assert res.fallbacks[0] == "openai/gpt-4o-mini-tts"
    assert set(res.fallbacks[1:]) == {"local/macos-say", "local/kokoro-v1.0"}

    res = resolve_tts(models=[], local_available=False)
    assert (res.model, res.provider, res.source) == ("gpt-4o-mini-tts", "openai", "env_pin")

    monkeypatch.delenv("VOICE_TTS_PROVIDER")
    monkeypatch.delenv("VOICE_TTS_MODEL")
    from app.core.config import get_settings

    get_settings.cache_clear()  # Settings captured the pins when first built
    monkeypatch.setattr(speech, "local_tts_engine_available", lambda e: e == "kokoro")
    res = resolve_tts(models=[])
    assert (res.model, res.source, res.targets[0].engine) == ("kokoro-v1.0", "local_default", "kokoro")

    monkeypatch.setattr(speech, "local_tts_engine_available", lambda e: False)
    with pytest.raises(ModelNotConfiguredError):
        resolve_tts(models=[])


def test_tts_elevenlabs_registry_entry_is_an_engine_target():
    entry = _speech_model("eleven_multilingual_v2", _TTS, provider="elevenlabs", base_url=None)
    res = resolve_tts(models=[entry], local_available=False)
    head = res.targets[0]
    assert (head.kind, head.engine, head.model) == ("engine", "elevenlabs", "eleven_multilingual_v2")


def test_kokoro_counts_only_with_its_model_files(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(speech, "_installed", lambda m: True)
    assert speech.local_tts_engine_available("kokoro") is False  # nothing downloaded
    (tmp_path / "kokoro-v1.0.onnx").write_bytes(b"x")
    (tmp_path / "voices-v1.0.bin").write_bytes(b"x")
    assert speech.local_tts_engine_available("kokoro") is True


# ── selection / seeding ──────────────────────────────────────────────────────


def test_selection_maps_speech_tasks_to_their_capabilities():
    from app.ai_router.selection import _TASK_ALIASES, _TASK_CAPABILITY

    assert _TASK_CAPABILITY[TaskType.SPEECH] is _STT
    assert _TASK_CAPABILITY[TaskType.SPEECH_TO_TEXT] is _STT
    assert _TASK_CAPABILITY[TaskType.TEXT_TO_SPEECH] is _TTS
    assert _TASK_ALIASES["stt"] is TaskType.SPEECH_TO_TEXT
    assert _TASK_ALIASES["tts"] is TaskType.TEXT_TO_SPEECH


def test_seeder_registers_env_speech_pins(monkeypatch):
    from app.ai_router.seeder import seed_registry_from_config

    monkeypatch.setenv("TRANSCRIPTION_MODEL", "whisper-large-v3")
    monkeypatch.setenv("VOICE_STT_MODEL", "small")
    monkeypatch.setenv("VOICE_TTS_PROVIDER", "openai_tts")
    monkeypatch.setenv("VOICE_TTS_MODEL", "gpt-4o-mini-tts")
    set_model_registry_store(ModelRegistryStore(_FakeRedis()))
    reg = ModelRegistry()
    seed_registry_from_config(reg)
    stt = {(m.provider, m.model_id) for m in reg.list_configured(_STT)}
    tts = {(m.provider, m.model_id) for m in reg.list_configured(_TTS)}
    assert ("openai", "whisper-large-v3") in stt
    assert ("local", "small") in stt
    assert tts == {("openai", "gpt-4o-mini-tts")}
    assert all((m.extra or {}).get("source") == "env" for m in reg.list_configured(_STT))


def test_seeder_merges_speech_into_an_existing_entry(monkeypatch):
    """AUDIO_MODEL naming the deployment's reasoning model adds the capability —
    it never replaces the reasoning entry (tools, structured output stay)."""
    from app.ai_router.seeder import seed_registry_from_config

    monkeypatch.setenv("DEFAULT_MODEL", "gpt-4o-audio-preview")
    monkeypatch.setenv("AUDIO_MODEL", "gpt-4o-audio-preview")
    set_model_registry_store(ModelRegistryStore(_FakeRedis()))
    reg = ModelRegistry()
    seed_registry_from_config(reg)
    entry = reg.get_configured("openai", "gpt-4o-audio-preview")
    assert entry is not None
    assert ModelCapability.TEXT_GENERATION in entry.capabilities
    assert ModelCapability.TOOL_USE in entry.capabilities
    assert _STT in entry.capabilities
    assert entry.supports_tools is True


def test_seeded_env_pin_is_labelled_env_pin_by_the_resolver(monkeypatch):
    from app.ai_router.seeder import seed_registry_from_config

    monkeypatch.setenv("AUDIO_MODEL", "whisper-large-v3")
    set_model_registry_store(ModelRegistryStore(_FakeRedis()))
    seed_registry_from_config(model_registry)
    res = resolve_stt(local_available=False)
    assert (res.model, res.source) == ("whisper-large-v3", "env_pin")


# ── preferences API / configured listing ─────────────────────────────────────


def _client():
    set_model_registry_store(ModelRegistryStore(_FakeRedis()))
    app = FastAPI()

    async def _resolve(key):
        return _ADMIN_CTX if key == "ak_admin" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(models_router)
    return TestClient(app)


def _add(client, model_id, cap, **extra):
    r = client.post(
        "/models/configured",
        headers=_ADMIN,
        json={"provider": "custom", "model_id": model_id, "capabilities": [cap], **extra},
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("cap", ["speech_to_text", "text_to_speech"])
def test_preferences_api_accepts_speech_capabilities(cap):
    client = _client()
    _add(client, "speech-a", cap, base_url=_LAN)
    _add(client, "speech-b", cap, base_url="http://10.0.0.9:8000/v1")
    r = client.put(
        f"/models/preferences/{cap}",
        headers=_ADMIN,
        json={"order": ["custom/speech-b", "custom/speech-a"]},
    )
    assert r.status_code == 200, r.text
    prefs = client.get("/models/preferences", headers=_ADMIN).json()["preferences"]
    assert prefs[cap] == ["custom/speech-b", "custom/speech-a"]

    groups = {g["capability"]: g for g in client.get("/models/configured", headers=_ADMIN).json()[
        "capabilities"
    ]}
    assert groups[cap]["selected_model_id"] == "speech-b"
    assert groups[cap]["fallback_model_ids"] == ["speech-a"]
    assert groups[cap]["note"]
    # The resolver follows the saved order.
    resolver = resolve_stt if cap == "speech_to_text" else resolve_tts
    assert resolver(local_available=False).model == "speech-b"

    assert client.delete(f"/models/preferences/{cap}", headers=_ADMIN).status_code == 200


def test_local_speech_provider_can_be_saved():
    client = _client()
    r = client.post(
        "/models/configured",
        headers=_ADMIN,
        json={"provider": "local", "model_id": "small", "capabilities": ["speech_to_text"]},
    )
    assert r.status_code == 200, r.text


# ── probes ───────────────────────────────────────────────────────────────────


def test_tiny_wav_is_a_valid_short_wav():
    data = speech.tiny_wav()
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16_000)
        assert 0 < w.getnframes() / w.getframerate() <= 1.0


async def _probe(fn, handler, **kw):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await fn(client, base=_LAN, headers={"Authorization": "Bearer k"}, **kw)


async def test_stt_probe_posts_a_wav_and_reads_the_transcript():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "beep"})

    out = await _probe(speech.probe_speech_to_text, handler, model_id="whisper-large-v3")
    assert out["ok"] is True and "beep" in out["detail"]
    assert seen["url"] == f"{_LAN}/audio/transcriptions"
    assert b"RIFF" in seen["body"] and b"whisper-large-v3" in seen["body"]


async def test_stt_probe_failures_are_reported():
    out = await _probe(
        speech.probe_speech_to_text,
        lambda r: httpx.Response(404, text="no such route"),
        model_id="m",
    )
    assert out["ok"] is False and out["error"].startswith("HTTP 404")
    out = await _probe(
        speech.probe_speech_to_text, lambda r: httpx.Response(200, json={"x": 1}), model_id="m"
    )
    assert out["ok"] is False and "text" in out["error"]


async def test_tts_probe_requires_audio_bytes():
    wav = speech.tiny_wav()
    ok = await _probe(
        speech.probe_text_to_speech,
        lambda r: httpx.Response(200, content=wav, headers={"content-type": "audio/wav"}),
        model_id="kokoro-82m",
    )
    assert ok["ok"] is True and ok["audio_bytes"] == len(wav)
    assert ok["audio_content_type"] == "audio/wav"

    not_audio = await _probe(
        speech.probe_text_to_speech,
        lambda r: httpx.Response(200, json={"error": "bad voice"}),
        model_id="kokoro-82m",
    )
    assert not_audio["ok"] is False and "instead of audio" in not_audio["error"]


def _mock_endpoint_client(monkeypatch, handler):
    def _client(**kwargs):
        kwargs.pop("follow_redirects", None)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.ai_router.model_endpoints.endpoint_http_client", _client)


def test_test_endpoint_runs_the_speech_probes(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "whisper-large-v3"}, {"id": "tts"}]})
        if path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": ""})
        if path.endswith("/audio/speech"):
            return httpx.Response(
                200, content=speech.tiny_wav(), headers={"content-type": "audio/wav"}
            )
        return httpx.Response(404)

    _mock_endpoint_client(monkeypatch, handler)
    client = _client()
    stt = client.post(
        "/models/configured/test-endpoint",
        headers=_ADMIN,
        json={"provider": "custom", "model_id": "whisper-large-v3", "base_url": _LAN,
              "capabilities": ["speech_to_text"]},
    ).json()
    assert stt["ok"] is True, stt
    assert stt["probe"] == "speech_to_text"
    assert stt["model_listed"] is True

    tts = client.post(
        "/models/configured/test-endpoint",
        headers=_ADMIN,
        json={"provider": "custom", "model_id": "tts", "base_url": _LAN,
              "capabilities": ["text_to_speech"]},
    ).json()
    assert tts["ok"] is True, tts
    assert tts["probe"] == "text_to_speech"
    assert tts["audio_bytes"] > 44


def test_test_endpoint_local_engine_needs_no_url(monkeypatch):
    monkeypatch.setattr(speech, "local_stt_available", lambda: True)
    out = _client().post(
        "/models/configured/test-endpoint",
        headers=_ADMIN,
        json={"provider": "local", "model_id": "small", "capabilities": ["speech_to_text"]},
    ).json()
    assert out["ok"] is True and out["probe"] == "speech_to_text"


def test_models_test_probes_a_saved_speech_model(monkeypatch):
    _mock_endpoint_client(
        monkeypatch,
        lambda r: httpx.Response(200, content=b"ID3fake-mp3", headers={"content-type": "audio/mpeg"}),
    )
    client = _client()
    _add(client, "tts-model", "text_to_speech", base_url=_LAN)
    out = client.post(
        "/models/test", headers=_ADMIN, json={"provider": "custom", "model_id": "tts-model"}
    ).json()
    assert out["status"] == "ok", out
    assert out["probe"] == "text_to_speech"


def test_test_endpoint_probes_each_speech_capability(monkeypatch):
    """A model registered for both speech capabilities gets one check per capability."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "tone"})
        if request.url.path.endswith("/audio/speech"):
            return httpx.Response(200, json={"error": "no such voice"})  # not audio
        return httpx.Response(404)

    _mock_endpoint_client(monkeypatch, handler)
    out = _client().post(
        "/models/configured/test-endpoint",
        headers=_ADMIN,
        json={"provider": "custom", "model_id": "omni-speech", "base_url": _LAN,
              "capabilities": ["speech_to_text", "text_to_speech"]},
    ).json()
    checks = {c["probe"]: c for c in out["checks"]}
    assert checks["speech_to_text"]["ok"] is True
    assert checks["text_to_speech"]["ok"] is False
    assert "instead of audio" in checks["text_to_speech"]["error"]
    assert out["ok"] is False  # every capability must pass
    assert out["probe"] == "speech_to_text"
    assert f"{_LAN.split('8000', 1)[1]}/audio/speech" in paths


def test_resolution_report_lists_the_speech_capabilities(monkeypatch):
    model_registry.register_configured(_speech_model("whisper-large-v3", _STT))
    monkeypatch.setattr(speech, "local_tts_engine_available", lambda e: False)
    caps = {
        c["capability"]: c
        for c in _client().get("/models/resolution", headers=_ADMIN).json()["capabilities"]
    }
    stt = caps["speech_to_text"]
    assert stt["model"]["model_id"] == "whisper-large-v3"
    assert stt["source"] == "registry_cheapest"
    tts = caps["text_to_speech"]
    assert tts["model"] is None and tts["source"] == "none"
    assert "browser" in tts["warning"]
