"""ModelOptimizer — selects optimal model per task type and runtime profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ModelOptimizationDecision:
    recommended_cost_class: str
    downgrade_safe: bool
    reason: str


@dataclass
class ModelRecommendation:
    model_id: str
    provider: str
    reason: str
    estimated_cost_usd: float
    estimated_latency_ms: float


def _get_task_defaults() -> dict[str, tuple[str, str, float, float]]:
    """Load task defaults from Settings so they can be overridden via env vars."""
    from app.core.config import get_settings

    s = get_settings()
    return {
        "planning": (s.default_planning_model, s.default_planning_provider, 0.003, 2000),
        "execution": (s.default_execution_model, s.default_execution_provider, 0.0003, 500),
        "verification": (
            s.default_verification_model,
            s.default_verification_provider,
            0.0003,
            500,
        ),
        "summarization": (
            s.default_summarization_model,
            s.default_summarization_provider,
            0.0002,
            400,
        ),
        "classification": (
            s.default_classification_model,
            s.default_classification_provider,
            0.0003,
            300,
        ),
    }


class ModelOptimizer:
    def __init__(self, budget_usd_per_goal: float = 0.10) -> None:
        self._budget = budget_usd_per_goal

    # ── Legacy interface (existing tests) ─────────────────────────────────────

    def optimize(self, profile: Any) -> ModelOptimizationDecision:
        from app.orchestration.runtime_profile import Complexity, RiskLevel

        props = getattr(profile, "properties", None)
        if props is None:
            return ModelOptimizationDecision("medium", True, "no properties")
        if (
            props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
            or props.complexity == Complexity.EXPERT
        ):
            return ModelOptimizationDecision("high", False, f"risk={props.risk.value}")
        if props.complexity == Complexity.SIMPLE and props.risk == RiskLevel.LOW:
            return ModelOptimizationDecision("low", True, "simple low-risk goal")
        return ModelOptimizationDecision("medium", True, "medium complexity")

    # ── New interface (task-type based recommendations) ───────────────────────

    def recommend(
        self,
        task_type: str,
        *,
        quality_requirement: float = 0.7,
        max_latency_ms: float = 10_000,
    ) -> ModelRecommendation:
        defaults = _get_task_defaults().get(task_type, ("gpt-4o-mini", "openai", 0.0003, 500))
        model_id, provider, cost, latency = defaults
        return ModelRecommendation(
            model_id=model_id,
            provider=provider,
            reason=f"Default for {task_type}",
            estimated_cost_usd=cost,
            estimated_latency_ms=latency,
        )
