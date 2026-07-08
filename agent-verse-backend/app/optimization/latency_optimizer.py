"""LatencyOptimizer — reduces goal execution latency through caching and early termination."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class LatencyConfig:
    recommended_tier: str
    recommendation: str


@dataclass
class LatencyOptimization:
    strategy: str
    expected_savings_ms: float
    description: str


class LatencyOptimizer:
    def __init__(self) -> None:
        self._latency_history: dict[str, list[float]] = {}

    # ── Legacy interface (existing tests) ─────────────────────────────────────

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

    # ── New interface (trend-based optimizations) ─────────────────────────────

    def record_latency(self, goal_type: str, latency_ms: float) -> None:
        self._latency_history.setdefault(goal_type, []).append(latency_ms)

    def get_optimizations(self, goal_type: str) -> list[LatencyOptimization]:
        history = self._latency_history.get(goal_type, [])
        if not history:
            return []
        avg = sum(history) / len(history)
        opts: list[LatencyOptimization] = []
        if avg > 30_000:
            opts.append(LatencyOptimization("cache_plan", avg * 0.3, "Cache plan for repeat goals"))
        if avg > 60_000:
            opts.append(LatencyOptimization("simplify_steps", avg * 0.2, "Reduce step count"))
        return opts

    def avg_latency_ms(self, goal_type: str) -> float:
        history = self._latency_history.get(goal_type, [])
        return sum(history) / len(history) if history else 0.0
