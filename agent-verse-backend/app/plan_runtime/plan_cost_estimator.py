from __future__ import annotations

from dataclasses import dataclass

_TOKENS_PER_STEP = {"low": 800, "medium": 1500, "high": 3000}
_COST_PER_1K = {"low": 0.0003, "medium": 0.003, "high": 0.015}


@dataclass
class CostEstimate:
    estimated_cost_usd: float
    estimated_tokens: int
    steps: int
    cost_class: str


class PlanCostEstimator:
    def estimate(self, plan: list[str], model_cost_class: str = "medium") -> CostEstimate:
        tokens_per = _TOKENS_PER_STEP.get(model_cost_class, 1500)
        cost_per_k = _COST_PER_1K.get(model_cost_class, 0.003)
        total_tokens = len(plan) * tokens_per
        total_cost = (total_tokens / 1000) * cost_per_k
        return CostEstimate(
            estimated_cost_usd=round(total_cost, 6),
            estimated_tokens=total_tokens,
            steps=len(plan),
            cost_class=model_cost_class,
        )
