"""One resolver per model capability (the single-resolver facade).

Every capability resolves through the same order: the Model Registry
preference order for that capability → an explicit env/settings pin → a local
default (where one exists) → an honest outcome (``ModelNotConfiguredError``, or
for capabilities with a documented degradation, a ``degraded`` resolution).
Never a hardcoded vendor model.

Each resolver returns a :class:`Resolution` naming the model it chose and WHERE
the choice came from (``source``), so callers and logs can say why a model ran.

Capabilities resolved here:

* ``rerank`` — :func:`resolve_reranker`.
* ``speech_to_text`` — :func:`resolve_stt`.
* ``text_to_speech`` — :func:`resolve_tts`.
* ``vision`` — :func:`resolve_vision`.
* ``ocr`` — :func:`resolve_ocr`.
* ``reasoning`` — :func:`resolve_reasoning` (every LLM role: agent graph,
  goal execution, knowledge / RAG, chat, connectors, workflows, org, evals,
  guardrails, ...). The role → task-type table is
  :data:`app.ai_router.role_preference.ROLE_TASK_TYPES`.

A vision / OCR call goes through :func:`dispatch_provider`: the
:class:`~app.providers.model_dispatch.ModelDispatchProvider` sends each model
(the head, then every fallback) to its own registry endpoint and key.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from app.observability.logging import get_logger

if TYPE_CHECKING:
    from app.rag_platform.registry_reranker import FailoverReranker

logger = get_logger(__name__)

# Where a resolution came from.
ResolutionSource = Literal[
    "override",  # a per-agent / per-goal model override (reasoning)
    "tenant_pin",  # the tenant's routing-policy role pin / BYOK model (reasoning)
    "registry_preference",  # the operator's saved preference order for the capability
    "registry_cheapest",  # a configured registry model, by cost (no preference saved)
    "env_pin",  # an explicit env/settings model or endpoint
    "local_default",  # the in-process local model tier
    "deployment_role_map",  # the deployment's per-role map (reasoning roles)
    "provider_default",  # the provider's own default model (empty registry only)
    "degraded",  # nothing configured; the capability's documented degradation
]


class ModelNotConfiguredError(LookupError):
    """No model is configured for *capability*; *hint* says how to configure one."""

    def __init__(self, capability: str, hint: str = "") -> None:
        self.capability = capability
        self.hint = hint
        message = f"no model is configured for the {capability!r} capability"
        super().__init__(f"{message}: {hint}" if hint else message)


@dataclass(frozen=True)
class Resolution:
    """The model a capability resolved to, and why."""

    capability: str
    model: str
    source: ResolutionSource
    provider: str = ""
    base_url: str | None = None
    # What runs next if this model fails, in order: labels for rerank (e.g.
    # ``onprem/<model>``), model ids for vision / OCR (each dispatched to its own
    # registry endpoint).
    fallbacks: tuple[str, ...] = ()
    # The whole ordered chain for capabilities that carry one (speech: one
    # :class:`SpeechTarget` per model, head first).
    targets: tuple[Any, ...] = field(default=(), compare=False, repr=False)


# ── rerank ───────────────────────────────────────────────────────────────────

RerankTier = Literal["hosted", "local", "degraded"]

_RERANK_HINT = (
    "add a rerank model in the Model Registry (capability 'rerank'), set "
    "RAG_HOSTED_RERANKER_URL/RAG_HOSTED_RERANKER_MODEL or ONPREM_RERANKER_URL, or "
    "install sentence-transformers for the local cross-encoder (RAG_CROSS_ENCODER_MODEL)"
)


@dataclass(frozen=True)
class RerankerResolution:
    """The reranker to use.

    * ``hosted`` — ``reranker`` is the ordered failover chain (registry rerank
      models in preference order, then the env/settings endpoints);
      ``resolution`` names its head. ``local_available`` says whether the local
      cross-encoder tier follows when the whole chain fails.
    * ``local`` — the local cross-encoder (``resolution.model`` is
      ``RAG_CROSS_ENCODER_MODEL``).
    * ``degraded`` — nothing is configured: score order, flagged
      ``rerank_degraded``.
    """

    tier: RerankTier
    resolution: Resolution
    reranker: FailoverReranker | None = None
    local_available: bool = False
    local_model: str = ""
    labels: tuple[str, ...] = field(default=())


def _model_key(m: Any) -> str:
    return f"{getattr(m, 'provider', '')}/{getattr(m, 'model_id', '')}"


def _registry_source(m: Any, preferences: Sequence[str]) -> ResolutionSource:
    if (getattr(m, "extra", None) or {}).get("source") == "env":
        # Seeded from the deployment's env (RAG_HOSTED_RERANKER_* / ONPREM_RERANKER_*).
        return "registry_preference" if _model_key(m) in preferences else "env_pin"
    return "registry_preference" if _model_key(m) in preferences else "registry_cheapest"


def _rerank_preferences() -> list[str]:
    try:
        from app.ai_router.models import ModelCapability
        from app.ai_router.registry import model_registry

        return model_registry.preference_order(ModelCapability.RERANK)
    except Exception:  # pragma: no cover - never block retrieval on the registry
        return []


def resolve_reranker(
    settings: Any = None,
    *,
    models: Sequence[Any] | None = None,
    preferences: Sequence[str] | None = None,
    local_available: bool | None = None,
    strict: bool = False,
) -> RerankerResolution:
    """The reranker to use, in order:

    1. the Model Registry ``rerank`` models in preference order (then cheapest):
       NVIDIA / Voyage / Cohere on their native rerank APIs, on-prem ``/v1/rerank``
       (e.g. Qwen3-Reranker) at the model's own endpoint, custom endpoints;
    2. the env/settings hosted endpoint (``RAG_HOSTED_RERANKER_URL``/``MODEL``,
       then ``ONPREM_RERANKER_URL``/``MODEL``);
    3. the local cross-encoder (``RAG_CROSS_ENCODER_MODEL``) when
       sentence-transformers is installed;
    4. score order, flagged ``rerank_degraded`` — or, with ``strict``,
       :class:`ModelNotConfiguredError`.

    Tiers 1+2 form ONE failover chain (``reranker``). *models*, *preferences*
    and *local_available* override the registry / installation lookups.
    """
    from app.rag.cross_encoder import configured_cross_encoder_model, local_cross_encoder_configured
    from app.rag_platform.registry_reranker import (
        FailoverReranker,
        env_rerank_targets,
        registry_rerank_targets,
    )

    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    local_model = configured_cross_encoder_model(settings)
    local = (
        local_cross_encoder_configured(settings) if local_available is None else local_available
    ) and bool(local_model)
    prefs = list(preferences) if preferences is not None else _rerank_preferences()

    registry = registry_rerank_targets(settings, models=models)
    env = env_rerank_targets(settings, exclude={target.key for _m, target in registry})
    targets = [target for _m, target in registry] + env
    labels = tuple(t.label for t in targets)
    local_label = (f"local/{local_model}",) if local else ()

    if targets:
        head = targets[0]
        if registry:
            head_model = registry[0][0]
            resolution = Resolution(
                capability="rerank",
                model=str(getattr(head_model, "model_id", "") or ""),
                source=_registry_source(head_model, prefs),
                provider=str(getattr(head_model, "provider", "") or ""),
                base_url=getattr(head_model, "base_url", None) or None,
                fallbacks=labels[1:] + local_label,
            )
        else:
            resolution = Resolution(
                capability="rerank",
                model=head.key[1],
                source="env_pin",
                provider="endpoint",
                base_url=head.key[0],
                fallbacks=labels[1:] + local_label,
            )
        return RerankerResolution(
            tier="hosted",
            resolution=resolution,
            reranker=FailoverReranker(targets),
            local_available=local,
            local_model=local_model if local else "",
            labels=labels,
        )

    if local:
        return RerankerResolution(
            tier="local",
            resolution=Resolution(
                capability="rerank",
                model=local_model,
                source="local_default",
                provider="local",
            ),
            local_available=True,
            local_model=local_model,
        )

    if strict:
        raise ModelNotConfiguredError("rerank", _RERANK_HINT)
    return RerankerResolution(
        tier="degraded",
        resolution=Resolution(capability="rerank", model="", source="degraded"),
    )


# ── speech (speech_to_text / text_to_speech) ─────────────────────────────────

SpeechKind = Literal["endpoint", "engine"]

_STT_HINT = (
    "add a speech_to_text model in the Model Registry (an OpenAI-compatible "
    "/audio/transcriptions endpoint, or provider 'local' for faster-whisper), set "
    "AUDIO_MODEL / TRANSCRIPTION_MODEL / NVIDIA_AUDIO_MODEL (served at OPENAI_BASE_URL), "
    "VOICE_STT_PROVIDER=whisper_api with VOICE_STT_MODEL, or install faster-whisper "
    "for the local tier (VOICE_STT_MODEL, default 'tiny')"
)
_TTS_HINT = (
    "add a text_to_speech model in the Model Registry (an OpenAI-compatible "
    "/audio/speech endpoint, provider 'elevenlabs', or provider 'local' with kokoro-v1.0 / "
    "macos-say / an OmniVoice checkpoint), set VOICE_TTS_PROVIDER (+ VOICE_TTS_MODEL "
    "for openai_tts / elevenlabs), or install a local engine (macOS say, or kokoro-onnx "
    "with its model files in MODEL_CACHE_DIR)"
)


@dataclass(frozen=True)
class SpeechTarget:
    """One speech model the chain may run, and how to reach it.

    * ``endpoint`` — an OpenAI-compatible ``/audio`` API at ``base_url``
      (credential: the registry ``entry``'s saved key, else the provider's env key);
    * ``engine`` — an in-process engine or vendor protocol named by ``engine``
      (``faster_whisper`` / ``assemblyai`` for STT; ``kokoro`` / ``macos_say`` /
      ``omnivoice`` / ``browser`` / ``elevenlabs`` / ``azure_tts`` for TTS).
    """

    capability: str
    kind: SpeechKind
    provider: str
    model: str
    source: ResolutionSource
    engine: str = ""
    base_url: str | None = None
    entry: Any = field(default=None, compare=False, repr=False)

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model}"

    @property
    def key(self) -> tuple[str, str]:
        return (_speech_provider_key(self.provider), self.model)


def _speech_provider_key(provider: str) -> str:
    p = (provider or "").strip().lower()
    return {"openai_compatible": "openai", "google": "gemini"}.get(p, p)


class _PreferenceShim:
    def __init__(self, preferences: Sequence[str]) -> None:
        self._prefs = list(preferences)

    def preference_order(self, _capability: Any) -> list[str]:
        return list(self._prefs)


def _speech_task(capability: str) -> Any:
    from app.ai_router.models import TaskType

    return TaskType.SPEECH_TO_TEXT if capability == "speech_to_text" else TaskType.TEXT_TO_SPEECH


def _speech_registry_models(
    capability: str, models: Sequence[Any] | None, preferences: Sequence[str] | None
) -> tuple[list[Any], list[str]]:
    """Registry models for the speech *capability* in execution order, and the
    preference order used to label their source."""
    from app.ai_router.models import ModelCapability

    cap = ModelCapability(capability)
    if models is not None:
        from app.ai_router.selection import order_models

        prefs = list(preferences or [])
        wanted = [m for m in models if cap in (getattr(m, "capabilities", None) or [])]
        return order_models(wanted, cap, _PreferenceShim(prefs)), prefs
    try:
        from app.ai_router.registry import model_registry
        from app.ai_router.selection import ordered_configured_models

        ordered = ordered_configured_models(_speech_task(capability))
        prefs = (
            list(preferences) if preferences is not None else model_registry.preference_order(cap)
        )
        return ordered, prefs
    except Exception:  # pragma: no cover - never block speech on the registry
        return [], list(preferences or [])


def _engine_target(
    capability: str, provider: str, model: str, source: ResolutionSource, entry: Any = None
) -> SpeechTarget | None:
    """A target for an engine provider (``local`` / vendor protocol), or None
    when the local engine is not installed here."""
    from app.ai_router import speech

    p = (provider or "").strip().lower()
    if p == speech.LOCAL_PROVIDER:
        if not speech.local_engine_available(capability, model):
            return None
        engine = "faster_whisper" if capability == speech.STT else speech.local_tts_engine(model)
    elif p == "assemblyai" and capability == speech.STT:
        engine = "assemblyai"
    elif p in ("elevenlabs", "azure_tts") and capability == speech.TTS:
        engine = p
    else:
        return None
    return SpeechTarget(
        capability=capability,
        kind="engine",
        provider=p,
        model=model,
        source=source,
        engine=engine,
        entry=entry,
    )


def _registry_speech_target(
    capability: str, m: Any, prefs: Sequence[str]
) -> SpeechTarget | None:
    from app.ai_router import speech

    provider = str(getattr(m, "provider", "") or "")
    model = str(getattr(m, "model_id", "") or "")
    if not model:
        return None
    source = _registry_source(m, prefs)
    if provider.strip().lower() in speech.ENGINE_PROVIDERS:
        return _engine_target(capability, provider, model, source, entry=m)
    base = speech.speech_endpoint_base(m)
    if not base:
        return None  # no /audio endpoint for this provider and no base_url of its own
    return SpeechTarget(
        capability=capability,
        kind="endpoint",
        provider=provider,
        model=model,
        source=source,
        base_url=base,
        entry=m,
    )


def _env_speech_targets(capability: str, settings: Any) -> list[SpeechTarget]:
    from app.ai_router import speech

    out: list[SpeechTarget] = []
    for model, provider in speech.env_speech_models(capability, settings):
        if provider in speech.ENGINE_PROVIDERS:
            target = _engine_target(capability, provider, model, "env_pin")
        else:
            base = speech.default_speech_base_url(provider)
            target = (
                SpeechTarget(
                    capability=capability,
                    kind="endpoint",
                    provider=provider,
                    model=model,
                    source="env_pin",
                    base_url=base,
                )
                if base
                else None
            )
        if target is not None:
            out.append(target)
    return out


def _local_speech_targets(
    capability: str, settings: Any, local_available: bool | None
) -> list[SpeechTarget]:
    """The local default tier: faster-whisper (``VOICE_STT_MODEL``, default
    ``tiny``) for STT; macOS ``say`` then Kokoro (model files present) for TTS."""
    from app.ai_router import speech

    if capability == speech.STT:
        available = speech.local_stt_available() if local_available is None else local_available
        if not available:
            return []
        model = speech.voice_setting("voice_stt_model", settings) or speech.LOCAL_STT_DEFAULT
        return [
            SpeechTarget(
                capability=capability,
                kind="engine",
                provider=speech.LOCAL_PROVIDER,
                model=model,
                source="local_default",
                engine="faster_whisper",
            )
        ]
    out: list[SpeechTarget] = []
    for model, engine in ((speech.MACOS_SAY_MODEL, "macos_say"), (speech.KOKORO_MODEL, "kokoro")):
        if local_available is None:
            available = speech.local_tts_engine_available(engine)
        else:
            available = local_available
        if available:
            out.append(
                SpeechTarget(
                    capability=capability,
                    kind="engine",
                    provider=speech.LOCAL_PROVIDER,
                    model=model,
                    source="local_default",
                    engine=engine,
                )
            )
    return out


def _resolve_speech(
    capability: str,
    hint: str,
    settings: Any,
    models: Sequence[Any] | None,
    preferences: Sequence[str] | None,
    local_available: bool | None,
) -> Resolution:
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    ordered, prefs = _speech_registry_models(capability, models, preferences)
    targets: list[SpeechTarget] = []
    seen: set[tuple[str, str]] = set()

    def _add(target: SpeechTarget | None) -> None:
        if target is not None and target.key not in seen:
            seen.add(target.key)
            targets.append(target)

    for m in ordered:
        _add(_registry_speech_target(capability, m, prefs))
    for target in _env_speech_targets(capability, settings):
        _add(target)
    for target in _local_speech_targets(capability, settings, local_available):
        _add(target)
    if not targets:
        raise ModelNotConfiguredError(capability, hint)
    head = targets[0]
    return Resolution(
        capability=capability,
        model=head.model,
        source=head.source,
        provider=head.provider,
        base_url=head.base_url,
        fallbacks=tuple(t.label for t in targets[1:]),
        targets=tuple(targets),
    )


def resolve_stt(
    settings: Any = None,
    *,
    models: Sequence[Any] | None = None,
    preferences: Sequence[str] | None = None,
    local_available: bool | None = None,
) -> Resolution:
    """The speech-to-text model chain, in order:

    1. the Model Registry ``speech_to_text`` models in preference order (then
       cheapest): an OpenAI-compatible ``/audio/transcriptions`` endpoint at the
       model's own ``base_url`` (else its provider's API) with its own key, a
       ``local`` faster-whisper size, or ``assemblyai``;
    2. the env / settings pins (``VOICE_STT_PROVIDER`` + ``VOICE_STT_MODEL``,
       ``AUDIO_MODEL`` / ``TRANSCRIPTION_MODEL`` / ``NVIDIA_AUDIO_MODEL``) — the
       seeder also registers these, so they normally appear in tier 1 with
       ``source="env_pin"``;
    3. the local faster-whisper engine (``VOICE_STT_MODEL``, default ``tiny``)
       when it is installed;
    4. :class:`ModelNotConfiguredError` with a configuration hint.

    ``Resolution.targets`` is the whole chain (:class:`SpeechTarget`), head first.
    *models* / *preferences* / *local_available* override the registry and the
    installation lookups (tests, previews).
    """
    return _resolve_speech(
        "speech_to_text", _STT_HINT, settings, models, preferences, local_available
    )


def resolve_tts(
    settings: Any = None,
    *,
    models: Sequence[Any] | None = None,
    preferences: Sequence[str] | None = None,
    local_available: bool | None = None,
) -> Resolution:
    """The text-to-speech model chain, in order:

    1. the Model Registry ``text_to_speech`` models in preference order (then
       cheapest): an OpenAI-compatible ``/audio/speech`` endpoint, ``elevenlabs``,
       ``azure_tts``, or a ``local`` engine (``kokoro-v1.0`` / ``macos-say`` /
       ``browser`` / an OmniVoice checkpoint) that is installed here;
    2. the env / settings pins (``VOICE_TTS_PROVIDER`` + ``VOICE_TTS_MODEL``);
    3. the local engines already present: macOS ``say``, then Kokoro when its
       model files are in ``MODEL_CACHE_DIR`` (nothing is downloaded);
    4. :class:`ModelNotConfiguredError` with a configuration hint (the voice
       layer then degrades to the browser's own speech synthesis, flagged).
    """
    return _resolve_speech(
        "text_to_speech", _TTS_HINT, settings, models, preferences, local_available
    )


# ── shared helpers (vision / OCR) ───────────────────────────────────────────


def _preferences(capability: Any, registry: Any) -> list[str]:
    try:
        from app.ai_router.registry import model_registry

        return list((registry or model_registry).preference_order(capability))
    except Exception:  # pragma: no cover - never block a call on the registry
        return []


def _dedupe_ids(ids: Sequence[str], *, skip: str = "") -> tuple[str, ...]:
    out: list[str] = []
    for mid in ids:
        if mid and mid != skip and mid not in out:
            out.append(mid)
    return tuple(out)


def _from_registry(
    capability: str,
    chain: Sequence[Any],
    *,
    preferences: Sequence[str],
    extra_fallbacks: Sequence[str] = (),
) -> Resolution:
    head = chain[0]
    model = str(getattr(head, "model_id", "") or "")
    rest = [str(getattr(m, "model_id", "") or "") for m in chain[1:]]
    return Resolution(
        capability=capability,
        model=model,
        source=_registry_source(head, preferences),
        provider=str(getattr(head, "provider", "") or ""),
        base_url=getattr(head, "base_url", None) or None,
        fallbacks=_dedupe_ids([*rest, *extra_fallbacks], skip=model),
    )


def _env_provider(model_id: str) -> str:
    try:
        from app.ai_router.seeder import _provider_for_model

        return _provider_for_model(model_id)
    except Exception:  # pragma: no cover - a label only
        return ""


# ── vision ───────────────────────────────────────────────────────────────────

_VISION_HINT = (
    "add a vision-capable model in the Model Registry (capability 'vision') or set "
    "VISION_MODEL / NVIDIA_VISION_MODEL"
)


def vision_chain(registry: Any = None) -> list[Any]:
    """Registry vision models (``supports_vision``) in execution order."""
    from app.ai_router.models import TaskType
    from app.ai_router.selection import ordered_configured_models

    return ordered_configured_models(TaskType.VISION, require_vision=True, registry=registry)


def resolve_vision(*, registry: Any = None) -> Resolution:
    """The image-understanding model, in order:

    1. the Model Registry vision models that support vision, in the operator's
       preference order (then cheapest) — the rest are the failover chain;
    2. the explicit env pin ``VISION_MODEL`` / ``NVIDIA_VISION_MODEL``;
    3. :class:`ModelNotConfiguredError` — never the reasoning model, never a
       vendor literal.
    """
    from app.ai_router.models import ModelCapability
    from app.providers.model_defaults import configured_vision_model

    env = configured_vision_model("")
    chain = vision_chain(registry)
    if chain:
        return _from_registry(
            "vision",
            chain,
            preferences=_preferences(ModelCapability.VISION, registry),
            extra_fallbacks=[env],
        )
    if env:
        return Resolution(
            capability="vision", model=env, source="env_pin", provider=_env_provider(env)
        )
    raise ModelNotConfiguredError("vision", _VISION_HINT)


def vision_configured(*, registry: Any = None) -> bool:
    """Whether :func:`resolve_vision` finds a model (the registry decides)."""
    try:
        resolve_vision(registry=registry)
    except ModelNotConfiguredError:
        return False
    return True


# ── ocr ──────────────────────────────────────────────────────────────────────

_OCR_HINT = (
    "add an OCR or vision model in the Model Registry, set OCR_MODEL, or enable the "
    "local Tesseract tier (OCR_TESSERACT_ENABLED=true)"
)

TESSERACT_MODEL = "tesseract"


def _ocr_env_pins() -> list[tuple[str, str, str | None]]:
    """``(model, provider, base_url)`` env pins for OCR, most specific first.

    ``OCR_MODEL``, then ``OLLAMA_OCR_MODEL`` (only when explicitly set — the
    settings default is catalogue metadata, not a selection).
    """
    from app.providers.model_defaults import configured_ocr_model

    pins: list[tuple[str, str, str | None]] = []
    ocr = configured_ocr_model("")
    if ocr:
        pins.append((ocr, _env_provider(ocr), None))
    ollama = (os.getenv("OLLAMA_OCR_MODEL") or "").strip()
    if ollama and ollama != ocr:
        base = (os.getenv("OLLAMA_BASE_URL") or "").strip() or None
        pins.append((ollama, "ollama", base))
    return pins


def ocr_chain(registry: Any = None) -> list[Any]:
    """Registry OCR models, then registry vision models — execution order, deduped."""
    from app.ai_router.models import TaskType
    from app.ai_router.selection import ordered_configured_models

    out: list[Any] = []
    seen: set[str] = set()
    for m in [
        *ordered_configured_models(TaskType.OCR, registry=registry),
        *vision_chain(registry),
    ]:
        mid = str(getattr(m, "model_id", "") or "")
        if mid and mid not in seen:
            seen.add(mid)
            out.append(m)
    return out


def _tesseract_enabled() -> bool:
    raw = os.getenv("OCR_TESSERACT_ENABLED", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def resolve_ocr(*, registry: Any = None, tesseract_enabled: bool | None = None) -> Resolution:
    """The OCR model, in order:

    1. the Model Registry OCR models (preference order, then cheapest);
    2. the Model Registry vision models;
    3. the env pin ``OCR_MODEL`` (then an explicitly set ``OLLAMA_OCR_MODEL``);
    4. the local Tesseract tier when ``OCR_TESSERACT_ENABLED`` is on
       (``model == "tesseract"``, ``source == "local_default"``);
    5. :class:`ModelNotConfiguredError`.
    """
    from app.ai_router.models import ModelCapability

    pins = _ocr_env_pins()
    chain = ocr_chain(registry)
    if chain:
        head_caps = getattr(chain[0], "capabilities", None) or []
        cap = ModelCapability.OCR if ModelCapability.OCR in head_caps else ModelCapability.VISION
        return _from_registry(
            "ocr",
            chain,
            preferences=_preferences(cap, registry),
            extra_fallbacks=[p[0] for p in pins],
        )
    if pins:
        model, provider, base_url = pins[0]
        return Resolution(
            capability="ocr",
            model=model,
            source="env_pin",
            provider=provider,
            base_url=base_url,
            fallbacks=_dedupe_ids([p[0] for p in pins[1:]], skip=model),
        )
    enabled = _tesseract_enabled() if tesseract_enabled is None else tesseract_enabled
    if enabled:
        return Resolution(
            capability="ocr", model=TESSERACT_MODEL, source="local_default", provider="local"
        )
    raise ModelNotConfiguredError("ocr", _OCR_HINT)


# ── dispatch ─────────────────────────────────────────────────────────────────


def dispatch_provider(provider: Any = None) -> Any:
    """The provider a vision / OCR call goes through.

    *provider* (an injected or tenant BYOK provider) — else the platform
    provider — wrapped in :class:`ModelDispatchProvider`, so the resolved model
    and every fallback run at their own registry endpoint with their own key.
    """
    if provider is None:
        from app.providers.registry import resolve_provider

        provider = resolve_provider()
    if provider is None or getattr(type(provider), "_agentverse_guarded", False):
        # A guarded wrapper (charging / budgeted provider) routes and meters its
        # own calls; wrapping it would hide that and charge twice.
        return provider
    from app.providers.model_dispatch import ModelDispatchProvider

    if isinstance(provider, ModelDispatchProvider):
        return provider
    # Wrapped even when the platform provider is a placeholder / canned fake: a
    # registry vision model with its own endpoint must still reach it.
    return ModelDispatchProvider(provider)


# ── reasoning ────────────────────────────────────────────────────────────────

# The LLM role of the call in flight (set by ``complete_decision``): a provider
# that receives a request with no model resolves it for this role.
_current_role: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agentverse_llm_role", default=""
)


@contextlib.contextmanager
def llm_role_scope(role: str) -> Iterator[None]:
    """Mark LLM calls made inside as *role* (see :func:`current_llm_role`)."""
    token = _current_role.set(str(role or ""))
    try:
        yield
    finally:
        _current_role.reset(token)


def current_llm_role(request: Any = None) -> str:
    """The role of the call in flight: the request's ``metadata["agentverse_role"]``,
    else the enclosing :func:`llm_role_scope`, else ``""``."""
    meta = getattr(request, "metadata", None)
    if isinstance(meta, dict):
        role = str(meta.get("agentverse_role") or "").strip()
        if role:
            return role
    return _current_role.get()


REASONING_HINT = (
    "add a text-generation model in the Model Registry (Models page), or set "
    "DEFAULT_MODEL / DEFAULT_<ROLE>_MODEL"
)

# Model ids that mean "the provider default" — never a real model.
UNSET_MODELS = frozenset({"", "default"})


def is_unset_model(model: Any) -> bool:
    """Whether *model* names no model (``""`` / ``"default"`` / None)."""
    return str(model or "").strip().lower() in UNSET_MODELS


def reasoning_task_type(role: str) -> str:
    """The routing task type deciding *role*'s model (unknown roles: ``planning``).

    ``planning`` is plain text generation with no tool requirement, so a role
    nobody registered still follows the operator's text-generation order.
    """
    from app.ai_router.role_preference import REASONING_ROLES, role_task_type

    task = role_task_type(role)
    if task:
        return task
    r = str(role or "").strip().lower()
    return r if r in REASONING_ROLES else "planning"


def _endpoint_meta(model: str) -> tuple[str, str | None]:
    """``(provider, base_url)`` of the registry entry for *model* (or ``("", None)``)."""
    try:
        from app.ai_router.registry import model_registry

        for m in model_registry.list_configured():
            if m.model_id == model:
                return str(m.provider or ""), (getattr(m, "base_url", None) or None)
    except Exception:  # pragma: no cover - never block resolution
        pass
    return "", None


def _ordered(task: str) -> list[Any]:
    try:
        from app.ai_router.selection import ordered_configured_models

        return list(ordered_configured_models(task))
    except Exception as exc:  # pragma: no cover - never block a call on the registry
        logger.warning("reasoning_registry_lookup_failed", task=task, error=str(exc)[:200])
        return []


def _registry_has_text_models() -> bool:
    try:
        from app.ai_router.models import ModelCapability
        from app.ai_router.registry import model_registry
        from app.ai_router.selection import _ensure_seeded, is_eligible

        _ensure_seeded(model_registry)
        return any(
            is_eligible(m) for m in model_registry.list_configured(ModelCapability.TEXT_GENERATION)
        )
    except Exception:  # pragma: no cover - never block resolution
        return False


def _role_map_model(task: str, router: Any, provider: Any) -> str:
    """The deployment role map's model for *task* (the router's per-goal map first)."""
    from app.ai_router.deployment_roles import ROLE_ALIASES

    role = ROLE_ALIASES.get(task, task)
    if role == "judge":
        role = "verification"
    role_map = getattr(router, "_role_map", None) if router is not None else None
    if isinstance(role_map, dict) and role_map:
        return str(role_map.get(role) or "")
    try:
        from app.ai_router.deployment_roles import deployment_role_models, servable_models

        servable = servable_models(provider) if provider is not None else None
        return str(deployment_role_models(servable=servable).get(role) or "")
    except Exception:  # pragma: no cover - never block resolution
        return ""


def _byok_default_model(provider: Any) -> str:
    """The configured model of a tenant BYOK provider (``""``: not BYOK)."""
    tenant = getattr(provider, "_byok_tenant_id", None) if provider is not None else None
    if not isinstance(tenant, str) or not tenant:
        return ""
    default = getattr(provider, "_default_model", "")
    return default if isinstance(default, str) and not is_unset_model(default) else ""


def _fallbacks(task: str, primary: str, provider: Any) -> tuple[str, ...]:
    out: list[str] = []
    try:
        from app.ai_router.selection import resolve_fallback_models

        out = list(resolve_fallback_models(task, primary, limit=3))
    except Exception:  # pragma: no cover - never block a call
        out = []
    default = getattr(provider, "_default_model", "") if provider is not None else ""
    if not isinstance(default, str):
        default = ""
    if default and not is_unset_model(default) and default != primary and default not in out:
        out.append(default)
    return tuple(out)


def resolve_reasoning(
    role: str,
    *,
    override: str = "",
    router: Any = None,
    provider: Any = None,
) -> Resolution:
    """THE reasoning model for one LLM *role* — the one resolver every role uses.

    Order:

    1. a per-agent / per-goal *override* (or the *router*'s ``with_override``);
       a tenant's own BYOK *provider* then keeps the model configured on it;
    2. the tenant's routing-policy role pin (on the goal's *router*);
    3. the Model Registry's saved text-generation preference order — the first
       model eligible for the role (execution needs tool use);
    4. the per-role env pin ``DEFAULT_<ROLE>_MODEL``;
    5. the deployment role map (hybrid / on-prem / NVIDIA, restricted to what
       *provider* serves);
    6. the env default model ``NVIDIA_MODEL`` / ``DEFAULT_MODEL`` / ``OPENAI_MODEL``;
    7. the registry's configured text models in execution order (a registry-only
       deployment: operator-added models, cheapest first);
    8. *provider*'s own default model — only when the registry has no text model;
    9. :class:`ModelNotConfiguredError` (never a vendor literal).

    ``fallbacks`` are the rest of the registry order for the role, then the
    provider's own default model.
    """
    from app.ai_router.role_preference import env_pin_model, preferred_role_model
    from app.providers.model_defaults import configured_default_model

    task = reasoning_task_type(role)
    if provider is None and router is not None:
        provider = getattr(router, "_bound_provider", None)

    def _done(model: str, source: ResolutionSource) -> Resolution:
        prov, base = _endpoint_meta(model)
        return Resolution(
            capability="reasoning",
            model=model,
            source=source,
            provider=prov,
            base_url=base,
            fallbacks=_fallbacks(task, model, provider),
        )

    explicit = str(override or "").strip()
    if not explicit and router is not None:
        explicit = str(getattr(router, "_override", "") or "").strip()
    if explicit and not is_unset_model(explicit):
        return _done(explicit, "override")

    # A tenant's own (BYOK) provider serves the model the tenant configured on it.
    byok_default = _byok_default_model(provider)
    if byok_default:
        return Resolution(capability="reasoning", model=byok_default, source="tenant_pin")

    if router is not None:
        from app.ai_router.deployment_roles import ROLE_ALIASES

        policy = getattr(router, "_policy_roles", None)
        if isinstance(policy, dict):
            pinned = str(policy.get(ROLE_ALIASES.get(task, task)) or "").strip()
            if pinned:
                return _done(pinned, "tenant_pin")

    preferred = preferred_role_model(task)
    if preferred:
        return _done(preferred, "registry_preference")

    pinned_env = env_pin_model(task)
    if pinned_env:
        return _done(pinned_env, "env_pin")

    mapped = _role_map_model(task, router, provider)
    if mapped:
        return _done(mapped, "deployment_role_map")

    env_default = configured_default_model("")
    if env_default:
        return _done(env_default, "env_pin")

    ordered = _ordered(task)
    if ordered:
        return _done(str(ordered[0].model_id), "registry_cheapest")

    if not _registry_has_text_models():
        default = getattr(provider, "_default_model", "") if provider is not None else ""
        if isinstance(default, str) and default and not is_unset_model(default):
            return Resolution(
                capability="reasoning", model=default, source="provider_default"
            )

    raise ModelNotConfiguredError("reasoning", f"role {role!r}: {REASONING_HINT}")


def reasoning_model(
    role: str, *, override: str = "", router: Any = None, provider: Any = None
) -> str:
    """:func:`resolve_reasoning`'s model, or ``""`` when nothing is configured.

    For call sites that hand the model to a provider which reports the honest
    "no LLM configured" error itself (``""`` = the provider default).
    """
    try:
        return resolve_reasoning(role, override=override, router=router, provider=provider).model
    except ModelNotConfiguredError:
        return ""


def cheapest_reasoning_model(role: str = "classification") -> str:
    """The cheapest configured text model for *role* (``""`` when none).

    For optimisation actions that recommend a cheaper model: the registry's
    cheapest eligible model, never a vendor literal.
    """
    from app.ai_router.selection import _cheapest

    task = reasoning_task_type(role)
    chosen = _cheapest(_ordered(task))
    return str(getattr(chosen, "model_id", "") or "") if chosen is not None else ""


def registry_text_model_ids(role: str = "chat") -> list[str]:
    """The registry's configured text models for *role*, in execution order."""
    seen: list[str] = []
    for m in _ordered(reasoning_task_type(role)):
        mid = str(m.model_id or "")
        if mid and mid not in seen:
            seen.append(mid)
    return seen


def validated_preference(model: str | None, role: str = "chat") -> str:
    """*model* when it is a configured registry text model eligible for *role*.

    A user preference (chat ``preferred_model``) is applied through the resolver
    only after this check: a stale or invented model id is ignored (``""``)
    instead of being sent to a provider that cannot serve it.
    """
    wanted = str(model or "").strip()
    if not wanted or is_unset_model(wanted):
        return ""
    return wanted if wanted in registry_text_model_ids(role) else ""


__all__ = [
    "REASONING_HINT",
    "TESSERACT_MODEL",
    "ModelNotConfiguredError",
    "RerankTier",
    "RerankerResolution",
    "Resolution",
    "ResolutionSource",
    "SpeechTarget",
    "cheapest_reasoning_model",
    "current_llm_role",
    "dispatch_provider",
    "is_unset_model",
    "llm_role_scope",
    "ocr_chain",
    "reasoning_model",
    "reasoning_task_type",
    "registry_text_model_ids",
    "resolve_ocr",
    "resolve_reasoning",
    "resolve_reranker",
    "resolve_stt",
    "resolve_tts",
    "resolve_vision",
    "validated_preference",
    "vision_chain",
    "vision_configured",
]
