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

import dataclasses
import os
from typing import Any

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import ModelRegistry, model_registry
from app.observability.logging import get_logger
from app.providers.model_defaults import (
    configured_default_model,
    configured_embed_model,
    configured_ocr_model,
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
_STT = ModelCapability.SPEECH_TO_TEXT
_TTS = ModelCapability.TEXT_TO_SPEECH


def _provider_for_model(model_id: str) -> str:
    """Infer a provider label from a model slug / configured env."""
    onprem_ids = {
        (os.getenv("ONPREM_QWEN_MODEL") or "").strip(),
        (os.getenv("ONPREM_GEMMA_MODEL") or "").strip(),
    } - {""}
    try:
        from app.core.config import get_settings

        _s = get_settings()
        if _s.onprem_enabled:
            onprem_ids |= {
                _s.onprem_qwen_model,
                _s.onprem_gemma_model,
                _s.onprem_embedding_model if _s.onprem_embedding_base_url else "",
                _s.onprem_reranker_model if _s.onprem_reranker_url else "",
            } - {""}
    except Exception:  # pragma: no cover
        pass
    if model_id in onprem_ids:
        return "onprem"
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
    """Every distinct reasoning/tooling model this deployment is configured with.

    Includes the on-prem cluster's models — they used to be missing, leaving the
    hosted NVIDIA model as the only candidate for every role.
    """
    try:
        from app.ai_router.deployment_roles import deployment_role_models

        role_models = list(deployment_role_models().values())
    except Exception:  # pragma: no cover - never block seeding
        role_models = []
    return _dedupe(
        *role_models,
        configured_default_model(),
        os.getenv("DEFAULT_PLANNING_MODEL", ""),
        os.getenv("DEFAULT_EXECUTION_MODEL", ""),
        os.getenv("DEFAULT_VERIFICATION_MODEL", ""),
        os.getenv("DEFAULT_CLASSIFICATION_MODEL", ""),
        os.getenv("DEFAULT_SUMMARIZATION_MODEL", ""),
    )


def _register(
    registry: ModelRegistry,
    model_id: str,
    capabilities: list[ModelCapability],
    *,
    provider: str | None = None,
) -> None:
    """Register an env-configured model, MERGING into an entry already seeded.

    One model can be configured for several roles (e.g. ``VISION_MODEL`` equal to
    the reasoning model). Its capabilities and ``supports_*`` flags are the
    union: a later role never replaces an earlier one (the vision seed used to
    overwrite the reasoning model's entry with ``supports_tools=False``,
    removing it from step execution).
    """
    if not model_id:
        return
    ci, co = registry.price_for(model_id)
    provider = provider or _provider_for_model(model_id)
    existing = registry.get_configured(provider, model_id)
    if existing is not None:
        # Merge, never replace: the same model seeded for another capability keeps
        # its entry (and everything it supports); it gains the new capabilities
        # and the matching ``supports_*`` flags.
        merged = list(existing.capabilities)
        merged += [c for c in capabilities if c not in merged]
        registry.register_configured(
            dataclasses.replace(
                existing,
                capabilities=merged,
                supports_tools=existing.supports_tools or _TU in merged,
                supports_vision=existing.supports_vision or _VI in merged,
                supports_structured_output=existing.supports_structured_output
                or _SO in merged,
            )
        )
        return
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
            # Env-seeded: the deployment is configured for it, so it is always
            # eligible (provider readiness is checked for registry overrides only).
            extra={"source": "env"},
        )
    )


def _reranker_models() -> list[tuple[str, str | None]]:
    """``(model_id, provider)`` of every reranker the env / Settings configure.

    The hosted endpoint's model when ``RAG_HOSTED_RERANKER_URL`` is set (env, or
    Settings — ``create_app`` copies an on-prem reranker there), and the on-prem
    reranker when ``ONPREM_RERANKER_URL`` is set. Provider None = inferred.
    """
    out: list[tuple[str, str | None]] = []
    hosted_url = (os.getenv("RAG_HOSTED_RERANKER_URL", "") or "").strip()
    hosted_model = (os.getenv("RAG_HOSTED_RERANKER_MODEL", "") or "").strip()
    onprem_url = onprem_model = ""
    try:
        from app.core.config import get_settings

        _s = get_settings()
        hosted_url = hosted_url or str(_s.rag_hosted_reranker_url or "").strip()
        if hosted_url and not hosted_model:
            hosted_model = str(_s.rag_hosted_reranker_model or "").strip()
        onprem_url = str(_s.onprem_reranker_url or "").strip()
        onprem_model = str(_s.onprem_reranker_model or "").strip()
    except Exception:  # pragma: no cover - never block seeding
        pass
    if onprem_url and onprem_model:
        out.append((onprem_model, "onprem"))
    if hosted_url and hosted_model and (hosted_model, "onprem") not in out:
        provider = "onprem" if onprem_url and hosted_url == onprem_url else None
        out.append((hosted_model, provider))
    return out


def _settings_embed_models() -> list[tuple[str, str | None]]:
    """``(model_id, provider)`` of every embedding model the typed Settings configure.

    ``configured_embed_model()`` reads the process env only, so an embedding
    model set only in Settings / ``.env`` (the on-prem ``ONPREM_EMBEDDING_MODEL``
    behind ``ONPREM_EMBEDDING_BASE_URL``, ``NVIDIA_EMBED_MODEL``, a dedicated
    ``EMBEDDING_BASE_URL`` + ``EMBEDDING_MODEL``) never reached the registry.
    Provider None = inferred.
    """
    out: list[tuple[str, str | None]] = []
    try:
        from app.core.config import get_settings

        _s = get_settings()
        nvidia_model = str(_s.nvidia_embed_model or "").strip()
        if str(_s.nvidia_api_key or "").strip() and nvidia_model:
            out.append((nvidia_model, "nvidia"))
        onprem_model = str(_s.onprem_embedding_model or "").strip()
        if _s.onprem_enabled and str(_s.onprem_embedding_base_url or "").strip() and onprem_model:
            out.append((onprem_model, "onprem"))
        dedicated = str(_s.embedding_model or "").strip()
        if (
            str(_s.embedding_base_url or "").strip()
            and dedicated
            and dedicated not in {m for m, _ in out}
        ):
            out.append((dedicated, None))
    except Exception:  # pragma: no cover - never block seeding
        pass
    return out


def _speech_models(capability: ModelCapability) -> list[tuple[str, str]]:
    """``(model_id, provider)`` of the speech models env / Settings pin."""
    try:
        from app.ai_router.speech import env_speech_models

        return env_speech_models(capability.value)
    except Exception as exc:  # pragma: no cover - never block seeding
        logger.warning("model_registry_speech_seed_failed error=%s", str(exc)[:120])
        return []


def _override_extra(e: dict[str, Any], *, from_env: bool, origin: str) -> dict[str, Any]:
    """``ModelEndpoint.extra`` of a persisted override.

    Carries the endpoint credential as the vault CIPHERTEXT only (decrypted at
    call time by ``app.ai_router.model_endpoints.endpoint_api_key``), the
    embedding width measured by a probe, the requested embedding output width
    (``output_dimensions``) and the thinking-model setting.
    """
    extra: dict[str, Any] = {"source": "env" if from_env else "override", "origin": origin}
    secret = str(e.get("api_key_encrypted") or "").strip()
    if secret:
        extra["api_key_encrypted"] = secret
    dims = e.get("dimensions")
    if isinstance(dims, int) and not isinstance(dims, bool) and dims > 0:
        extra["dimensions"] = dims
    out_dims = e.get("output_dimensions")
    if isinstance(out_dims, int) and not isinstance(out_dims, bool) and out_dims > 0:
        extra["output_dimensions"] = out_dims
    # Thinking-model control; absent (entries saved before it existed) = auto.
    thinking = str(e.get("thinking") or "").strip().lower()
    if thinking in ("auto", "off", "on"):
        extra["thinking"] = thinking
    budget = e.get("thinking_budget_tokens")
    if isinstance(budget, int) and not isinstance(budget, bool) and budget > 0:
        extra["thinking_budget_tokens"] = budget
    return extra


def _load_overrides(reg: ModelRegistry) -> None:
    """Register user-added/overridden models from the persistent store."""
    try:
        from app.ai_router.registry_store import get_model_registry_store

        store = get_model_registry_store()
        if store is None:
            return
        for e in store.list():
            caps = [ModelCapability(c) for c in (e.get("capabilities") or []) if c]
            if not e.get("model_id") or not caps:
                continue
            provider = str(e.get("provider") or _provider_for_model(str(e["model_id"])))
            # The same model the deployment is configured with (e.g. a catalog
            # import of the env NVIDIA model) keeps that model's standing: always
            # eligible and never demoted as a catalog import.
            seeded = reg.get_configured(provider, str(e["model_id"]))
            from_env = seeded is not None and (seeded.extra or {}).get("source") == "env"
            origin = "deployment" if from_env else str(e.get("origin") or "manual")
            reg.register_configured(
                ModelEndpoint(
                    provider=provider,
                    model_id=str(e["model_id"]),
                    display_name=str(e.get("display_name") or e["model_id"]),
                    capabilities=caps,
                    cost_per_1k_input=float(e.get("cost_per_1k_input", 0.0) or 0.0),
                    cost_per_1k_output=float(e.get("cost_per_1k_output", 0.0) or 0.0),
                    supports_tools=bool(e.get("supports_tools", _TU in caps)),
                    supports_vision=bool(e.get("supports_vision", _VI in caps)),
                    supports_structured_output=bool(
                        e.get("supports_structured_output", _SO in caps)
                    ),
                    quality_score=float(e.get("quality_score", 0.7) or 0.7),
                    is_available=bool(e.get("is_available", True)),
                    base_url=str(e.get("base_url") or "").strip() or None,
                    extra=_override_extra(e, from_env=from_env, origin=origin),
                )
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("model_registry_overrides_load_failed error=%s", str(exc)[:120])


def _load_preferences(reg: ModelRegistry) -> None:
    """Load the operator's per-capability preference order from the store."""
    try:
        from app.ai_router.registry_store import get_model_registry_store

        store = get_model_registry_store()
        reg.set_preferences(store.get_preferences() if store is not None else {})
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("model_registry_preferences_load_failed error=%s", str(exc)[:120])


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
        for _em, _em_provider in _settings_embed_models():
            _register(reg, _em, [_EM], provider=_em_provider)  # merges capabilities
        # Vision / OCR — only when EXPLICITLY pinned (VISION_MODEL /
        # NVIDIA_VISION_MODEL, OCR_MODEL), never the reasoning model by default.
        # Merged into an existing entry, so a vision pin equal to the reasoning
        # model keeps its tool use. A dedicated vision model is not a
        # text-generation candidate (it would be picked for planning by cost).
        _register(reg, configured_vision_model(), [_VI, _OC])
        _register(reg, configured_ocr_model(), [_OC])
        # Rerankers the deployment is configured with: the hosted endpoint
        # (RAG_HOSTED_RERANKER_URL/MODEL) and the on-prem reranker
        # (ONPREM_RERANKER_URL/MODEL, e.g. a vLLM Qwen3-Reranker).
        for _rr, _rr_provider in _reranker_models():
            _register(reg, _rr, [_RR], provider=_rr_provider)
        # Speech models the env / Settings pin (AUDIO_MODEL / TRANSCRIPTION_MODEL /
        # NVIDIA_AUDIO_MODEL, VOICE_STT_PROVIDER + VOICE_STT_MODEL,
        # VOICE_TTS_PROVIDER + VOICE_TTS_MODEL). Merged into an existing entry of
        # the same model (it gains the capability), never replacing it.
        for _cap in (_STT, _TTS):
            for _sp_model, _sp_provider in _speech_models(_cap):
                _register(reg, _sp_model, [_cap], provider=_sp_provider)
        # Overlay user-registered overrides from the persistent store (UI/API).
        # These win over env-seeded models with the same provider/model_id.
        _load_overrides(reg)
        _load_preferences(reg)
        count = len(reg.list_configured())
        logger.info("model_registry_seeded", configured_models=count)
        return count
    except Exception as exc:  # pragma: no cover - defensive; never block startup
        logger.warning("model_registry_seed_failed error=%s", str(exc)[:120])
        return 0
