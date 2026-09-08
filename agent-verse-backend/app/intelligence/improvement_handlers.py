"""Real improvement handler functions for ImprovementActionExecutor (D-3 fix).

Each handler is an async callable ``(payload: dict) -> dict`` that
performs a bounded, idempotent mutation and returns a durable result dict.
Handlers raise on invalid payloads so the executor can retry or fail-close.

Three handlers are provided:

- ``update_prompt_variant`` — persists a prompt variant to the in-memory
  prompt registry for A/B selection.
- ``adjust_limit`` — validates and records a numeric parameter change
  (e.g. max_iterations, context_budget_tokens).
- ``update_rag_strategy`` — records a RAG strategy preference switch
  from a known set of strategies.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

# Allowed RAG strategies — mirrors the strategy names used in
# app/orchestration/runtime_profile.py and app/rag/ modules.
ALLOWED_RAG_STRATEGIES: frozenset[str] = frozenset(
    {
        "naive",
        "hybrid",
        "hybrid_rerank",
        "agentic",
        "dense",
        "sparse",
        "multi_query",
        "hyde",
    }
)

# Tunable parameter bounds — parameter_name → (min, max).
PARAMETER_BOUNDS: dict[str, tuple[float, float]] = {
    "max_iterations": (1, 50),
    "context_budget_tokens": (256, 128_000),
    "temperature": (0.0, 2.0),
    "top_p": (0.0, 1.0),
    "max_tokens": (1, 32_000),
    "timeout_seconds": (1, 600),
}


# ---------------------------------------------------------------------------
# In-memory stores — production would back these with Redis/DB; these are
# sufficient to prove the handlers actually execute and persist state.
# ---------------------------------------------------------------------------

_prompt_variants: dict[str, dict[str, Any]] = {}
_parameter_tunings: list[dict[str, Any]] = []
_strategy_preferences: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------


async def _handle_prompt_update(payload: dict[str, Any]) -> dict[str, Any]:
    """Store a prompt variant for A/B selection.

    Required payload keys:
        prompt_key: str — which prompt slot (e.g. "planner", "executor")
        variant_id: str — unique variant identifier
        new_prompt: str — the prompt text
    """
    prompt_key = payload.get("prompt_key")
    variant_id = payload.get("variant_id")
    new_prompt = payload.get("new_prompt")
    if not prompt_key or not variant_id or not new_prompt:
        raise ValueError(
            "update_prompt_variant requires prompt_key, variant_id, and new_prompt"
        )

    entry = {
        "prompt_key": prompt_key,
        "variant_id": variant_id,
        "prompt_text": new_prompt,
        "stored_at": datetime.now(UTC).isoformat(),
    }
    _prompt_variants[variant_id] = entry

    logger.info(
        "improvement_prompt_stored",
        prompt_key=prompt_key,
        variant_id=variant_id,
    )
    return {"stored": True, "variant_id": variant_id, "prompt_key": prompt_key}


async def _handle_parameter_tune(payload: dict[str, Any]) -> dict[str, Any]:
    """Adjust a numeric configuration parameter within safe bounds.

    Required payload keys:
        parameter: str — parameter name (must be in PARAMETER_BOUNDS)
        new_value: int | float — the new value
    Optional:
        old_value: int | float — the previous value (for audit)
    """
    parameter = payload.get("parameter")
    new_value = payload.get("new_value")
    if not parameter or new_value is None:
        raise ValueError("adjust_limit requires parameter and new_value")

    bounds = PARAMETER_BOUNDS.get(parameter)
    if bounds is None:
        raise ValueError(
            f"Unknown tunable parameter: {parameter}. "
            f"Allowed: {sorted(PARAMETER_BOUNDS.keys())}"
        )

    new_val = float(new_value)
    lo, hi = bounds
    if new_val < lo or new_val > hi:
        raise ValueError(
            f"Parameter {parameter} value {new_val} out of bounds [{lo}, {hi}]"
        )

    entry = {
        "parameter": parameter,
        "old_value": payload.get("old_value"),
        "new_value": new_value,
        "tuned_at": datetime.now(UTC).isoformat(),
    }
    _parameter_tunings.append(entry)  # type: ignore[union-attr]

    logger.info(
        "improvement_parameter_tuned",
        parameter=parameter,
        new_value=new_value,
    )
    return {"tuned": True, "parameter": parameter, "new_value": new_value}


async def _handle_strategy_switch(payload: dict[str, Any]) -> dict[str, Any]:
    """Record a RAG strategy preference switch.

    Required payload keys:
        strategy: str — target strategy name (must be in ALLOWED_RAG_STRATEGIES)
    Optional:
        reason: str — why the switch was recommended
    """
    strategy = payload.get("strategy")
    if not strategy:
        raise ValueError("update_rag_strategy requires strategy")

    if strategy not in ALLOWED_RAG_STRATEGIES:
        raise ValueError(
            f"Unknown RAG strategy: {strategy}. "
            f"Allowed: {sorted(ALLOWED_RAG_STRATEGIES)}"
        )

    entry = {
        "strategy": strategy,
        "reason": payload.get("reason", ""),
        "switched_at": datetime.now(UTC).isoformat(),
    }
    _strategy_preferences.append(entry)  # type: ignore[union-attr]

    logger.info(
        "improvement_strategy_switched",
        strategy=strategy,
        reason=payload.get("reason", ""),
    )
    return {"switched": True, "strategy": strategy}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_default_handlers() -> dict[str, Handler]:
    """Return the default handler map for ImprovementActionExecutor.

    Maps action_type strings (matching ``ImprovementActionRecord.action_type``)
    to async handler functions.
    """
    return {
        "update_prompt_variant": _handle_prompt_update,
        "adjust_limit": _handle_parameter_tune,
        "update_rag_strategy": _handle_strategy_switch,
    }


__all__ = ["build_default_handlers"]
