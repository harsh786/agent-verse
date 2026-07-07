from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass
class ModelOptimizationDecision:
    recommended_cost_class: str
    downgrade_safe: bool
    reason: str


class ModelOptimizer:
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
