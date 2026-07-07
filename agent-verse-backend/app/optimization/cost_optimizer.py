from __future__ import annotations

_COST_PER_1K: dict[str, float] = {
    "low": 0.0003,
    "medium": 0.003,
    "high": 0.015,
    "free": 0.0,
}


class CostOptimizer:
    def estimate_savings(
        self,
        current_cost_class: str,
        proposed_cost_class: str,
        estimated_tokens: int,
    ) -> float:
        c = _COST_PER_1K.get(current_cost_class, 0.003) * (estimated_tokens / 1000)
        p = _COST_PER_1K.get(proposed_cost_class, 0.003) * (estimated_tokens / 1000)
        return max(0.0, c - p)
