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
"""

from __future__ import annotations

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
    # What runs next if this model fails, in order (labels, e.g. ``onprem/<model>``).
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


__all__ = [
    "ModelNotConfiguredError",
    "RerankTier",
    "RerankerResolution",
    "Resolution",
    "ResolutionSource",
    "resolve_reranker",
]
