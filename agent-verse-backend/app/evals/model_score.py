"""ModelScorer — scores model efficiency: cost and latency."""
from __future__ import annotations


class ModelScorer:
    def score(self, *, cost_usd: float, latency_ms: float, budget_usd: float = 10.0) -> float:
        if budget_usd <= 0:
            return 0.5
        cost_ratio = cost_usd / budget_usd
        cost_score = max(0.0, 1.0 - cost_ratio)
        latency_s = latency_ms / 1000.0
        if latency_s <= 5:
            latency_score = 1.0
        elif latency_s <= 30:
            latency_score = 1.0 - (latency_s - 5) / 25
        else:
            latency_score = 0.1
        return 0.5 * cost_score + 0.5 * latency_score
