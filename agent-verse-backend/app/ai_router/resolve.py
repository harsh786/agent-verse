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
* ``vision`` — :func:`resolve_vision`.
* ``ocr`` — :func:`resolve_ocr`.

A vision / OCR call goes through :func:`dispatch_provider`: the
:class:`~app.providers.model_dispatch.ModelDispatchProvider` sends each model
(the head, then every fallback) to its own registry endpoint and key.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from app.rag_platform.registry_reranker import FailoverReranker

# Where a resolution came from.
ResolutionSource = Literal[
    "registry_preference",  # the operator's saved preference order for the capability
    "registry_cheapest",  # a configured registry model, by cost (no preference saved)
    "env_pin",  # an explicit env/settings model or endpoint
    "local_default",  # the in-process local model tier
    "deployment_role_map",  # the deployment's per-role map (reasoning roles)
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


__all__ = [
    "TESSERACT_MODEL",
    "ModelNotConfiguredError",
    "RerankTier",
    "RerankerResolution",
    "Resolution",
    "ResolutionSource",
    "dispatch_provider",
    "ocr_chain",
    "resolve_ocr",
    "resolve_reranker",
    "resolve_vision",
    "vision_chain",
    "vision_configured",
]
