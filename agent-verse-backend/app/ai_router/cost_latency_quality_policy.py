"""CostLatencyQualityPolicy — selects a model quality tier based on complexity and risk."""
from __future__ import annotations

from app.agent.pattern_config import Complexity, RiskLevel


class CostLatencyQualityPolicy:
    def select_tier(
        self,
        complexity: Complexity,
        risk: RiskLevel,
        latency_requirement: str = "interactive",
    ) -> str:
        if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return "high"
        if latency_requirement == "realtime":
            return "low"
        if complexity == Complexity.EXPERT:
            return "high"
        elif complexity == Complexity.COMPLEX:
            return "medium"
        elif complexity == Complexity.SIMPLE and risk == RiskLevel.LOW:
            return "low"
        return "medium"
