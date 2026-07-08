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


_TASK_DEFAULTS: dict[str, tuple[str, str, float, float]] = {
    "planning": ("gpt-5.2", "openai", 0.003, 2000),
    "execution": ("gpt-4o-mini", "openai", 0.0003, 500),
    "verification": ("gpt-4o-mini", "openai", 0.0003, 500),
    "summarization": ("claude-haiku-3-5", "anthropic", 0.0002, 400),
    "classification": ("gpt-4o-mini", "openai", 0.0003, 300),
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
        if props.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL) or props.complexity == Complexity.EXPERT:
            return ModelOptimizationDecision(
                "high", False, f"risk={props.risk.value}"
            )
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
        defaults = _TASK_DEFAULTS.get(task_type, ("gpt-4o-mini", "openai", 0.0003, 500))
        model_id, provider, cost, latency = defaults
        return ModelRecommendation(
            model_id=model_id,
            provider=provider,
            reason=f"Default for {task_type}",
            estimated_cost_usd=cost,
            estimated_latency_ms=latency,
        )
