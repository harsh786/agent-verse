"""Seed the model registry from the deployment's ACTUAL configuration.

The registry's static ``BUILTIN_MODELS`` is a reference price catalog. What a
deployment can actually serve comes from env / ``Settings`` / ``LLMConfigStore``
(and, later, the model-registry UI). This module registers a ``ModelEndpoint``
per *configured* model — tagged with its capabilities and a cost (real per-1k
price for known cloud slugs, ``0.0`` for self-hosted/unknown) — into the
registry's separate "configured" set, which capability selection reads from.

Behavior-preserving: with exactly one model configured per capability, selection
returns that model — identical to today. Cheapest-wins only changes anything once
a second model is configured for the same capability.
"""

from __future__ import annotations

import os

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import ModelRegistry, model_registry
from app.observability.logging import get_logger
from app.providers.model_defaults import (
    configured_default_model,
    configured_embed_model,
    configured_vision_model,
)

logger = get_logger(__name__)

_TG = ModelCapability.TEXT_GENERATION
_TU = ModelCapability.TOOL_USE
_SO = ModelCapability.STRUCTURED_OUTPUT
_VI = ModelCapability.VISION
_OC = ModelCapability.OCR
_EM = ModelCapability.EMBEDDING
_RR = ModelCapability.RERANK


def _provider_for_model(model_id: str) -> str:
    """Infer a provider label from a model slug / configured env."""
    mid = model_id.lower()
    if os.getenv("NVIDIA_API_KEY") and (
        mid.startswith(("nvidia/", "meta/", "openai/gpt-oss")) or os.getenv("NVIDIA_MODEL")
    ):
        # NVIDIA build endpoint is OpenAI-compatible and serves these slugs.
        return "nvidia"
    if mid.startswith("claude") or "anthropic" in mid:
        return "anthropic"
    if mid.startswith(("gemini", "google")):
        return "gemini"
    if mid.startswith(("voyage",)):
        return "voyage"
    if mid.startswith(("llama", "groq")) and os.getenv("GROQ_API_KEY"):
        return "groq"
    return "openai"


def _dedupe(*names: str) -> list[str]:
    seen: list[str] = []
    for n in names:
        n = (n or "").strip()
        if n and n not in seen:
            seen.append(n)
    return seen


def _reasoning_model_ids() -> list[str]:
    """Every distinct reasoning/tooling model this deployment is configured with."""
    return _dedupe(
        configured_default_model(),
        os.getenv("DEFAULT_PLANNING_MODEL", ""),
        os.getenv("DEFAULT_EXECUTION_MODEL", ""),
        os.getenv("DEFAULT_VERIFICATION_MODEL", ""),
        os.getenv("DEFAULT_CLASSIFICATION_MODEL", ""),
        os.getenv("DEFAULT_SUMMARIZATION_MODEL", ""),
    )


def _register(registry: ModelRegistry, model_id: str, capabilities: list[ModelCapability]) -> None:
    if not model_id:
        return
    ci, co = registry.price_for(model_id)
    provider = _provider_for_model(model_id)
    registry.register_configured(
        ModelEndpoint(
            provider=provider,
            model_id=model_id,
            display_name=model_id,
            capabilities=capabilities,
            cost_per_1k_input=ci,
            cost_per_1k_output=co,
            supports_tools=_TU in capabilities,
            supports_vision=_VI in capabilities,
            supports_structured_output=_SO in capabilities,
            is_available=True,
        )
    )


def seed_registry_from_config(registry: ModelRegistry | None = None) -> int:
    """(Re)seed the registry's configured set from the current environment.

    Returns the number of configured models registered. Safe to call multiple
    times (idempotent) — it clears and rebuilds the configured set.
    """
    reg = registry or model_registry
    try:
        reg.clear_configured()
        # Reasoning / tooling models — capability TEXT_GENERATION (+tool/structured).
        for mid in _reasoning_model_ids():
            _register(reg, mid, [_TG, _TU, _SO])
        # Embeddings (used from P2 onward; harmless to register now).
        _register(reg, configured_embed_model(), [_EM])
        # Vision / OCR (used from P3 onward).
        _vision = configured_vision_model()
        if _vision:
            _register(reg, _vision, [_TG, _VI, _OC])
        # Reranker (used from P4 onward) — only when a hosted reranker is set.
        _rr = (os.getenv("RAG_HOSTED_RERANKER_MODEL", "") or "").strip()
        if _rr and (os.getenv("RAG_HOSTED_RERANKER_URL", "") or "").strip():
            _register(reg, _rr, [_RR])
        count = len(reg.list_configured())
        logger.info("model_registry_seeded", configured_models=count)
        return count
    except Exception as exc:  # pragma: no cover - defensive; never block startup
        logger.warning("model_registry_seed_failed error=%s", str(exc)[:120])
        return 0
