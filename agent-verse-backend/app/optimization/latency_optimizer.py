from __future__ import annotations
from dataclasses import dataclass


@dataclass
class LatencyConfig:
    recommended_tier: str
    recommendation: str


class LatencyOptimizer:
    def optimize_for_latency(
        self,
        current_tier: str,
        latency_requirement: str,
        current_latency_ms: float,
    ) -> LatencyConfig:
        if latency_requirement == "realtime" or current_latency_ms > 500:
            return LatencyConfig("low", "realtime requires low-latency model")
        if current_latency_ms < 300:
            return LatencyConfig(current_tier, "latency acceptable")
        return LatencyConfig("medium", "reduce tier for better latency")
