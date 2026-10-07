"""Speech models (speech-to-text / text-to-speech) in the Model Registry.

A registry speech model runs in one of three places:

* an **OpenAI-compatible ``/audio`` endpoint** — ``POST {base}/audio/transcriptions``
  (STT) or ``POST {base}/audio/speech`` (TTS) at the model's own ``base_url``
  (e.g. a vLLM / speaches / LocalAI server on the LAN), else at its provider's
  API (OpenAI, Groq, NVIDIA, Ollama), with the model's own saved key, else the
  provider's env key;
* an **in-process engine** — provider ``local``: faster-whisper for STT (the
  model id is the Whisper size, e.g. ``tiny`` / ``large-v3-turbo``); for TTS the
  model id names the engine (``kokoro-v1.0``, ``macos-say``, ``browser``, else an
  OmniVoice checkpoint such as ``k2-fsa/OmniVoice``). Only engines that are
  already installed count — nothing is downloaded to decide;
* a **vendor API with its own protocol** — provider ``elevenlabs`` (TTS),
  ``assemblyai`` (STT), ``azure_tts`` (TTS).

This module holds those placement rules, the env/settings pins (the voice_*
settings, ``AUDIO_MODEL`` / ``TRANSCRIPTION_MODEL`` / ``NVIDIA_AUDIO_MODEL``) that
the seeder registers and the resolvers fall back to, the ``/audio`` calls and the
Test-connection probes. The resolvers themselves are
:func:`app.ai_router.resolve.resolve_stt` / :func:`~app.ai_router.resolve.resolve_tts`.
"""

from __future__ import annotations

import importlib.util
import io
import math
import os
import struct
import wave
from typing import Any

STT = "speech_to_text"
TTS = "text_to_speech"

LOCAL_PROVIDER = "local"
# Registry providers whose speech models are NOT served on an OpenAI-compatible
# /audio endpoint: the in-process engines and vendor protocols.
ENGINE_PROVIDERS = frozenset({LOCAL_PROVIDER, "elevenlabs", "assemblyai", "azure_tts"})

# The local faster-whisper size used when nothing else is configured: the small
# (~75 MB) model that the dev setup already caches. VOICE_STT_MODEL overrides it.
LOCAL_STT_DEFAULT = "tiny"
# Local TTS engine model ids (asset names, not vendor models).
KOKORO_MODEL = "kokoro-v1.0"
MACOS_SAY_MODEL = "macos-say"
BROWSER_MODEL = "browser"
OMNIVOICE_DEFAULT = "k2-fsa/OmniVoice"

_PROBE_TEXT = "Test connection."


# ── settings (env first, then typed Settings / .env) ─────────────────────────


def voice_setting(name: str, settings: Any = None) -> str:
    """A ``voice_*`` setting: the process env (``VOICE_STT_MODEL`` …) first — so a
    change takes effect without a settings-cache reset — then ``Settings``."""
    value = (os.getenv(name.upper()) or "").strip()
    if value:
        return value
    if settings is None:
        try:
            from app.core.config import get_settings

            settings = get_settings()
        except Exception:  # pragma: no cover - defensive
            return ""
    return str(getattr(settings, name, "") or "").strip()


def configured_transcription_model() -> str:
    """``AUDIO_MODEL`` → ``TRANSCRIPTION_MODEL`` → ``NVIDIA_AUDIO_MODEL`` (or "")."""
    from app.providers.model_defaults import configured_audio_model

    return configured_audio_model("")


# ── local engines (installed only; nothing is downloaded to decide) ──────────


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # pragma: no cover - broken install
        return False


def local_stt_available() -> bool:
    """Whether the in-process faster-whisper engine is installed."""
    return _installed("faster_whisper")


def kokoro_model_dir() -> str:
    return os.getenv("MODEL_CACHE_DIR", "/app/models")


def local_tts_engine(model_id: str) -> str:
    """The in-process TTS engine a ``local`` registry model id names."""
    mid = (model_id or "").strip().lower()
    if mid.startswith("kokoro"):
        return "kokoro"
    if mid in (MACOS_SAY_MODEL, "macos_say", "say"):
        return "macos_say"
    if mid == BROWSER_MODEL:
        return "browser"
    return "omnivoice"


def local_tts_engine_available(engine: str) -> bool:
    """Whether a local TTS engine can run now without downloading anything."""
    if engine == "macos_say":
        return os.path.exists("/usr/bin/say")
    if engine == "kokoro":
        model_dir = kokoro_model_dir()
        return _installed("kokoro_onnx") and all(
            os.path.exists(os.path.join(model_dir, f))
            for f in ("kokoro-v1.0.onnx", "voices-v1.0.bin")
        )
    if engine == "omnivoice":
        return _installed("omnivoice")
    return engine == "browser"


def local_engine_available(capability: str, model_id: str) -> bool:
    if capability == STT:
        return local_stt_available()
    return local_tts_engine_available(local_tts_engine(model_id))


# ── endpoints ────────────────────────────────────────────────────────────────

_ALIASES = {"google": "gemini", "openai_compatible": "openai"}


def _norm(provider: str) -> str:
    p = (provider or "").strip().lower()
    return _ALIASES.get(p, p)


def default_speech_base_url(provider: str, *, own_key: bool = False) -> str | None:
    """The OpenAI-compatible base URL serving *provider*'s ``/audio`` API, or
    ``None`` when the provider has none (the model then needs its own base_url)."""
    from app.core.config import get_provider_env

    p = (provider or "").strip().lower()
    if p in ("openai", "openai_compatible", "custom"):
        configured = (os.getenv("OPENAI_BASE_URL") or "").strip()
        if own_key and p == "openai":
            return "https://api.openai.com/v1"  # the model's own key belongs to the official API
        if configured:
            return configured.rstrip("/")
        return "https://api.openai.com/v1" if p != "custom" else None
    if p == "groq":
        return "https://api.groq.com/openai/v1"
    if p == "nvidia":
        return (get_provider_env("NVIDIA_BASE_URL") or "https://integrate.api.nvidia.com/v1").rstrip(
            "/"
        )
    if p == "ollama":
        base = (get_provider_env("OLLAMA_BASE_URL") or "").strip().rstrip("/")
        if not base:
            return None
        return base if base.endswith("/v1") else f"{base}/v1"
    return None


def speech_endpoint_base(model: Any) -> str | None:
    """Where a registry speech model's ``/audio`` calls go (None = nowhere)."""
    own = str(getattr(model, "base_url", "") or "").strip().rstrip("/")
    if own:
        return own
    from app.providers.model_dispatch import has_own_api_key

    return default_speech_base_url(
        str(getattr(model, "provider", "") or ""), own_key=has_own_api_key(model)
    )


def speech_api_key(provider: str, entry: Any = None) -> str:
    """The credential for a speech call: the entry's saved key, else the
    provider's env key (``EMPTY`` when there is none — self-hosted servers
    ignore auth)."""
    from app.ai_router.model_endpoints import endpoint_api_key

    return endpoint_api_key(provider, entry)


def has_real_key(api_key: str) -> bool:
    return bool(api_key) and api_key != "EMPTY"


# ── env / settings pins ──────────────────────────────────────────────────────


def env_speech_models(capability: str, settings: Any = None) -> list[tuple[str, str]]:
    """``(model_id, provider)`` of every speech model the env / Settings pin for
    *capability*, in order. The seeder registers them (merging, never replacing)
    and the resolvers use them when the registry is empty.

    STT: ``VOICE_STT_PROVIDER`` (``whisper_api`` → an OpenAI ``/audio`` model named
    by ``VOICE_STT_MODEL`` / ``AUDIO_MODEL``; ``assemblyai``; ``faster_whisper`` →
    local), then ``AUDIO_MODEL`` / ``TRANSCRIPTION_MODEL`` / ``NVIDIA_AUDIO_MODEL``,
    then ``VOICE_STT_MODEL`` (local faster-whisper).
    TTS: ``VOICE_TTS_PROVIDER`` (``openai_tts`` / ``elevenlabs`` with
    ``VOICE_TTS_MODEL``; ``omnivoice`` / ``kokoro`` / ``macos_say`` / ``browser``
    → local; ``azure_tts``), then ``VOICE_TTS_MODEL`` (local OmniVoice).
    A pin that names an API provider but no model is skipped (no vendor default).
    """
    out: list[tuple[str, str]] = []

    def _add(model_id: str, provider: str) -> None:
        model_id = (model_id or "").strip()
        if model_id and (model_id, provider) not in out:
            out.append((model_id, provider))

    if capability == STT:
        provider = voice_setting("voice_stt_provider", settings).lower()
        model = voice_setting("voice_stt_model", settings)
        audio = configured_transcription_model()
        if provider == "whisper_api":
            _add(model or audio, "openai")
        elif provider == "assemblyai":
            _add(model or "assemblyai-default", "assemblyai")
        elif provider == "faster_whisper":
            _add(model or LOCAL_STT_DEFAULT, LOCAL_PROVIDER)
        if audio:
            from app.ai_router.seeder import _provider_for_model

            _add(audio, _provider_for_model(audio))
        if model and provider in ("", "faster_whisper"):
            _add(model, LOCAL_PROVIDER)
        return out

    provider = voice_setting("voice_tts_provider", settings).lower()
    model = voice_setting("voice_tts_model", settings)
    if provider == "openai_tts":
        _add(model, "openai")
    elif provider == "elevenlabs":
        _add(model or (os.getenv("ELEVENLABS_MODEL_ID") or ""), "elevenlabs")
    elif provider == "omnivoice":
        _add(model or OMNIVOICE_DEFAULT, LOCAL_PROVIDER)
    elif provider == "kokoro":
        _add(KOKORO_MODEL, LOCAL_PROVIDER)
    elif provider == "macos_say":
        _add(MACOS_SAY_MODEL, LOCAL_PROVIDER)
    elif provider == "browser":
        _add(BROWSER_MODEL, LOCAL_PROVIDER)
    elif provider == "azure_tts":
        _add(model or "azure-neural", "azure_tts")
    if model and provider == "":
        _add(model, LOCAL_PROVIDER)
    return out


# ── audio helpers ────────────────────────────────────────────────────────────


def tiny_wav(duration_s: float = 0.4, rate: int = 16_000, freq_hz: float = 440.0) -> bytes:
    """A short mono 16-bit PCM WAV sine tone (stdlib only) for probes."""
    frames = int(duration_s * rate)
    amplitude = 0.2 * 32767
    data = b"".join(
        struct.pack("<h", int(amplitude * math.sin(2 * math.pi * freq_hz * i / rate)))
        for i in range(frames)
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)
    return buf.getvalue()


_AUDIO_MAGIC = (b"RIFF", b"ID3", b"OggS", b"fLaC", b"FORM", b"\x1aE\xdf\xa3")


def looks_like_audio(content: bytes, content_type: str = "") -> bool:
    """Whether a response body is audio (not a JSON / text error page)."""
    if not content:
        return False
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype.startswith("audio/"):
        return True
    if ctype.startswith(("application/json", "text/")):
        return False
    if content.startswith(_AUDIO_MAGIC):
        return True
    # MPEG audio frame sync (mp3 without an ID3 tag) / AAC ADTS.
    return len(content) > 1 and content[0] == 0xFF and (content[1] & 0xE0) == 0xE0


# ── OpenAI-compatible /audio calls ───────────────────────────────────────────


async def transcribe_via_endpoint(
    *,
    base_url: str,
    api_key: str,
    model: str,
    audio: bytes,
    filename: str = "audio.wav",
    mime_type: str = "audio/wav",
    timestamps: bool = True,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """``POST {base_url}/audio/transcriptions`` → the JSON body.

    With *timestamps* it asks for ``verbose_json`` with segment timestamps and
    retries once with plain ``json`` when the server refuses that format.
    Connections are SSRF-pinned (the host is re-checked at connect time).
    """
    from app.ai_router.model_endpoints import endpoint_http_client

    url = f"{base_url.rstrip('/')}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}
    formats = ["verbose_json", "json"] if timestamps else ["json"]
    async with endpoint_http_client(timeout=timeout) as client:
        for i, fmt in enumerate(formats):
            data: dict[str, Any] = {"model": model, "response_format": fmt}
            if fmt == "verbose_json":
                data["timestamp_granularities[]"] = "segment"
            resp = await client.post(
                url, headers=headers, data=data, files={"file": (filename, audio, mime_type)}
            )
            retry = fmt == "verbose_json" and resp.status_code in (400, 422)
            if retry and i + 1 < len(formats):
                continue
            resp.raise_for_status()
            body = resp.json()
            if not isinstance(body, dict) or not isinstance(body.get("text"), str):
                raise ValueError(f"{url} returned no transcription ('text')")
            return body
    raise RuntimeError("unreachable")  # pragma: no cover


async def synthesize_via_endpoint(
    *,
    base_url: str,
    api_key: str,
    model: str,
    text: str,
    voice: str,
    response_format: str = "wav",
    speed: float = 1.0,
    timeout: float = 60.0,
) -> bytes:
    """``POST {base_url}/audio/speech`` → the audio bytes (raises when the
    endpoint answers anything but audio)."""
    from app.ai_router.model_endpoints import endpoint_http_client

    url = f"{base_url.rstrip('/')}/audio/speech"
    async with endpoint_http_client(timeout=timeout) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "input": text,
                "voice": voice,
                "response_format": response_format,
                "speed": speed,
            },
        )
        resp.raise_for_status()
        content = resp.content
        if not looks_like_audio(content, resp.headers.get("content-type", "")):
            raise ValueError(f"{url} returned no audio")
        return content


def tts_voice(voice_id: str | None = None) -> str:
    return voice_id or os.getenv("OPENAI_TTS_VOICE", "nova")


# ── Test-connection probes ───────────────────────────────────────────────────


async def probe_speech_to_text(
    client: Any, *, base: str, headers: dict[str, str], model_id: str
) -> dict[str, Any]:
    """Transcribe a generated 0.4 s WAV tone at ``{base}/audio/transcriptions``.

    ``ok`` = HTTP 2xx with a JSON ``text`` field (empty is fine: it is a tone).
    """
    out: dict[str, Any] = {"ok": False, "detail": "", "error": None}
    resp = await client.post(
        f"{base}/audio/transcriptions",
        headers=headers,
        data={"model": model_id, "response_format": "json"},
        files={"file": ("probe.wav", tiny_wav(), "audio/wav")},
    )
    if resp.status_code >= 400:
        out["error"] = f"HTTP {resp.status_code}: {resp.text[:300]}"
        return out
    try:
        body = resp.json()
    except ValueError:
        out["error"] = "the endpoint did not answer JSON (expected {\"text\": ...})"
        return out
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str):
        out["error"] = "the endpoint returned no transcription ('text' missing)"
        return out
    out["ok"] = True
    out["transcript"] = text[:200]
    out["detail"] = (
        f"transcribed a 0.4 s test tone: {text.strip()[:80]!r}"
        if text.strip()
        else "transcribed a 0.4 s test tone (empty transcript, as expected for a tone)"
    )
    return out


async def probe_text_to_speech(
    client: Any, *, base: str, headers: dict[str, str], model_id: str, voice: str | None = None
) -> dict[str, Any]:
    """Synthesize a short phrase at ``{base}/audio/speech``; ``ok`` = audio bytes."""
    out: dict[str, Any] = {"ok": False, "detail": "", "error": None}
    resp = await client.post(
        f"{base}/audio/speech",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "model": model_id,
            "input": _PROBE_TEXT,
            "voice": tts_voice(voice),
            "response_format": "wav",
        },
    )
    if resp.status_code >= 400:
        out["error"] = f"HTTP {resp.status_code}: {resp.text[:300]}"
        return out
    ctype = resp.headers.get("content-type", "")
    content = resp.content
    if not looks_like_audio(content, ctype):
        out["error"] = (
            f"the endpoint answered {ctype or 'an unknown type'} ({len(content)} bytes) "
            "instead of audio"
        )
        return out
    out["ok"] = True
    out["audio_bytes"] = len(content)
    out["audio_content_type"] = ctype.split(";")[0].strip() or "audio"
    out["detail"] = f"synthesized {len(content)} bytes of {out['audio_content_type']}"
    return out


def probe_local_engine(capability: str, model_id: str) -> dict[str, Any]:
    """Test connection for a ``local`` speech model: is the engine installed?
    (Checked without loading or downloading the model.)"""
    if capability == STT:
        ok = local_stt_available()
        engine = "faster-whisper"
    else:
        engine = local_tts_engine(model_id)
        ok = local_tts_engine_available(engine)
    return {
        "ok": ok,
        "detail": f"local {engine} engine installed (model {model_id!r})" if ok else "",
        "error": None if ok else f"the local {engine} engine is not installed here",
    }


__all__ = [
    "BROWSER_MODEL",
    "ENGINE_PROVIDERS",
    "KOKORO_MODEL",
    "LOCAL_PROVIDER",
    "LOCAL_STT_DEFAULT",
    "MACOS_SAY_MODEL",
    "OMNIVOICE_DEFAULT",
    "STT",
    "TTS",
    "default_speech_base_url",
    "env_speech_models",
    "has_real_key",
    "local_engine_available",
    "local_stt_available",
    "local_tts_engine",
    "local_tts_engine_available",
    "looks_like_audio",
    "probe_local_engine",
    "probe_speech_to_text",
    "probe_text_to_speech",
    "speech_api_key",
    "speech_endpoint_base",
    "synthesize_via_endpoint",
    "tiny_wav",
    "transcribe_via_endpoint",
    "tts_voice",
    "voice_setting",
]
