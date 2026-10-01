"""Capability-based, cost-aware model selection over CONFIGURED models.

The single entry point every capability uses to pick a model id. It selects from
the registry's *configured* set (seeded from real deployment config), filtered by
capability + requirements, and returns the lowest-cost qualifying model — ties
broken by higher quality then lower latency (unpriced/self-hosted = cost 0.0, so
they win over paid cloud). Returns ``""`` when nothing qualifies, so callers can
fall back to their existing single-model resolution (never a dead end).
"""

from __future__ import annotations

from app.ai_router.models import ModelCapability, TaskType
from app.ai_router.registry import ModelRegistry, model_registry

# Which capability a task requires.
_TASK_CAPABILITY: dict[TaskType, ModelCapability] = {
    TaskType.PLANNING: ModelCapability.TEXT_GENERATION,
    TaskType.EXECUTION: ModelCapability.TEXT_GENERATION,
    TaskType.VERIFICATION: ModelCapability.TEXT_GENERATION,
    TaskType.CLASSIFICATION: ModelCapability.TEXT_GENERATION,
    TaskType.JUDGE: ModelCapability.TEXT_GENERATION,
    TaskType.TEXT_GENERATION: ModelCapability.TEXT_GENERATION,
    TaskType.EMBEDDING: ModelCapability.EMBEDDING,
    TaskType.RERANK: ModelCapability.RERANK,
    TaskType.OCR: ModelCapability.OCR,
    TaskType.VISION: ModelCapability.VISION,
}

_TASK_ALIASES: dict[str, TaskType] = {
    "planning": TaskType.PLANNING,
    "plan": TaskType.PLANNING,
    "execution": TaskType.EXECUTION,
    "execute": TaskType.EXECUTION,
    "verification": TaskType.VERIFICATION,
    "verify": TaskType.VERIFICATION,
    "classification": TaskType.CLASSIFICATION,
    "reflection": TaskType.PLANNING,
    "think": TaskType.PLANNING,
    "thinking": TaskType.PLANNING,
    "judge": TaskType.JUDGE,
    "embedding": TaskType.EMBEDDING,
    "rerank": TaskType.RERANK,
    "ocr": TaskType.OCR,
    "vision": TaskType.VISION,
}


_lazy_seeded = False
# Version of the shared override set this process last seeded from, and when
# it last looked: an override POSTed to ANOTHER replica bumps the version, so
# this process re-seeds instead of serving a stale set until restart (PROV-17).
_seeded_version: int | None = None
_last_version_check = 0.0
_VERSION_CHECK_INTERVAL_S = 5.0


def _ensure_seeded(reg: ModelRegistry) -> None:
    """Seed the configured set from env/config + shared overrides (idempotent).

    Seeds once per process, then again whenever the shared store's override
    version changes (checked at most every ``_VERSION_CHECK_INTERVAL_S``).
    """
    global _lazy_seeded, _seeded_version, _last_version_check
    # Only auto-seed the process-wide global registry; a caller-supplied registry
    # (tests, isolated contexts) is left exactly as provided.
    if reg is not model_registry:
        return
    import time

    now = time.monotonic()
    if _lazy_seeded and now - _last_version_check < _VERSION_CHECK_INTERVAL_S:
        return
    from app.ai_router.registry_store import get_model_registry_store

    store = get_model_registry_store()
    version = store.version() if store is not None else None
    _last_version_check = now
    if _lazy_seeded and (version is None or version == _seeded_version):
        return
    _lazy_seeded = True
    _seeded_version = version
    try:
        from app.ai_router.seeder import seed_registry_from_config

        seed_registry_from_config(reg)
    except Exception:  # pragma: no cover - never block selection
        pass


def _cheapest(models: list) -> object | None:
    if not models:
        return None
    return min(
        models,
        key=lambda m: (
            m.cost_per_1k_input,
            -m.quality_score,
            m.avg_latency_ms or 1_000_000,
        ),
    )


def select_configured_model_id(
    task: str | TaskType,
    *,
    require_tools: bool = False,
    require_vision: bool = False,
    require_structured: bool = False,
    registry: ModelRegistry | None = None,
) -> str:
    """Return the cheapest configured model id for *task*, or ``""`` to fall back.

    ``task`` may be a TaskType or a role string ("planning", "execution", ...).
    """
    reg = registry or model_registry
    _ensure_seeded(reg)
    task_type = task if isinstance(task, TaskType) else _TASK_ALIASES.get(str(task).lower())
    if task_type is None:
        return ""
    capability = _TASK_CAPABILITY.get(task_type)
    if capability is None:
        return ""

    candidates = reg.list_configured(capability)
    if require_tools:
        candidates = [m for m in candidates if m.supports_tools]
    if require_vision:
        candidates = [m for m in candidates if m.supports_vision]
    if require_structured:
        candidates = [m for m in candidates if m.supports_structured_output]

    chosen = _cheapest(candidates)
    return chosen.model_id if chosen is not None else ""


# ── Capability resolvers (registry-first, env fallback) ──────────────────────
# Convenience wrappers used by every capability's call sites: prefer the cheapest
# CONFIGURED model, else fall back to the existing env-based resolver. Import the
# env resolvers lazily to avoid an import cycle with the seeder.


def resolve_embed_model(fallback: str = "") -> str:
    """Cheapest configured embedding model, else the env-configured one."""
    from app.providers.model_defaults import configured_embed_model

    return select_configured_model_id(TaskType.EMBEDDING) or configured_embed_model(fallback)


def resolve_vision_model(fallback: str = "") -> str:
    """Cheapest configured vision/OCR model, else the env-configured one."""
    from app.providers.model_defaults import configured_vision_model

    return (
        select_configured_model_id(TaskType.VISION, require_vision=True)
        or select_configured_model_id(TaskType.OCR)
        or configured_vision_model(fallback)
    )


def resolve_rerank_model(fallback: str = "") -> str:
    """Cheapest configured reranker model, else *fallback*."""
    return select_configured_model_id(TaskType.RERANK) or fallback
