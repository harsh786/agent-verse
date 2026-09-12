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


def _ensure_seeded(reg: ModelRegistry) -> None:
    """Seed the configured set once per process from env/config (idempotent).

    Lets any process (API or Celery worker) select without explicit startup
    wiring; explicit re-seeds (e.g. after a config change) still work.
    """
    global _lazy_seeded
    # Only auto-seed the process-wide global registry; a caller-supplied registry
    # (tests, isolated contexts) is left exactly as provided.
    if _lazy_seeded or reg is not model_registry:
        return
    _lazy_seeded = True
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
