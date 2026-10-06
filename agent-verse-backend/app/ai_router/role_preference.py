"""The operator's saved REASONING order, as a per-role model choice.

The Model Registry's preference order for reasoning
(``PUT /models/preferences/text_generation``) is explicit operator intent. It
used to decide only the failover chain: both role routers (``ModelRouter`` and
``ModelOrchestratorAdapter``) returned the deployment's automatic role map
first — which every NVIDIA / on-prem deployment has (planning on NVIDIA,
execution and verification on Qwen) — so the model ranked first never ran.

Precedence in both routers is now: per-agent / per-goal override > the tenant's
own routing-policy pin (``PUT /models/routing-policies``) > saved reasoning
order > per-role env pin (``DEFAULT_*_MODEL``) > deployment role map > cheapest
configured model.
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Roles that run on a reasoning model.
REASONING_ROLES = frozenset(
    {"planning", "execution", "verification", "classification", "reflection", "think",
     "thinking", "judge"}
)


def preferred_role_model(task_type: str) -> str:
    """The first model of the saved reasoning order that can serve *task_type*.

    Respects the role's requirements (execution needs tool use) and skips
    models whose provider has no credentials. ``""`` when no order is saved or
    none of its models qualifies — the caller then keeps its existing choice.
    """
    if task_type not in REASONING_ROLES:
        return ""
    try:
        from app.ai_router.models import ModelCapability
        from app.ai_router.registry import model_registry
        from app.ai_router.selection import (
            _ensure_seeded,
            model_key,
            ordered_configured_models,
        )

        _ensure_seeded(model_registry)
        preferred = set(model_registry.preference_order(ModelCapability.TEXT_GENERATION))
        if not preferred:
            return ""
        ordered = ordered_configured_models(task_type)
        # ordered_configured_models puts ranked models first, so the head is a
        # ranked model exactly when one of them qualifies for this role.
        if ordered and model_key(ordered[0]) in preferred:
            return str(ordered[0].model_id)
    except Exception as exc:  # pragma: no cover - never block routing
        logger.warning("role_preference_lookup_failed role=%s error=%s", task_type, exc)
    return ""


def preferred_model_and_fallbacks(task_type: str, provider: Any = None) -> tuple[str, list[str]]:
    """``(model, fallback_models)`` for a single reasoning call that is not a graph
    role (answer synthesis, eval judges, …).

    With a saved reasoning order: its first eligible model for *task_type* and
    the rest of the order, then the provider's own default model as the last
    resort. Without one: ``("", [])`` — the call keeps the provider default
    exactly as before (cheapest-first is NOT applied here: on an NVIDIA
    deployment the cheapest text-capable model is the 11B vision model).
    """
    primary = preferred_role_model(task_type)
    if not primary:
        return "", []
    fallbacks: list[str] = []
    try:
        from app.ai_router.selection import resolve_fallback_models

        fallbacks = resolve_fallback_models(task_type, primary, limit=3)
    except Exception:  # pragma: no cover - never block the call
        fallbacks = []
    default = str(getattr(provider, "_default_model", "") or "")
    if default and default != primary and default not in fallbacks:
        fallbacks.append(default)
    return primary, fallbacks
