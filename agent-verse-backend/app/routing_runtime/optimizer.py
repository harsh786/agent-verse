"""Bounded rolling optimization recommendations."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from math import ceil


@dataclass(frozen=True, slots=True)
class MeasuredOutcome:
    candidate_id: str
    success: bool
    quality_score: int
    cost_usd: float
    latency_ms: int


@dataclass(frozen=True, slots=True)
class OptimizationRecommendation:
    candidate_id: str | None
    reason: str
    sample_count: int
    p95_latency_ms: int | None


class RoutingOptimizer:
    def __init__(self, *, window_size: int = 500, minimum_samples: int = 20) -> None:
        if window_size <= 0 or minimum_samples <= 0:
            raise ValueError("optimizer limits must be positive")
        self._window = window_size
        self._minimum = minimum_samples
        self._outcomes: dict[str, deque[MeasuredOutcome]] = defaultdict(
            lambda: deque(maxlen=self._window)
        )

    def record(self, outcome: MeasuredOutcome) -> None:
        self._outcomes[outcome.candidate_id].append(outcome)

    def recommend(
        self,
        eligible_candidate_ids: tuple[str, ...],
        *,
        quality_floor: int,
        cost_ceiling_usd: float,
        deadline_ms: int,
    ) -> OptimizationRecommendation:
        ranked: list[tuple[int, str, int]] = []
        for candidate_id in eligible_candidate_ids:
            samples = tuple(self._outcomes[candidate_id])
            if len(samples) < self._minimum:
                continue
            latencies = sorted(item.latency_ms for item in samples)
            p95 = latencies[min(len(latencies) - 1, ceil(len(latencies) * 0.95) - 1)]
            quality = sum(item.quality_score for item in samples) // len(samples)
            cost = sum(item.cost_usd for item in samples) / len(samples)
            success = sum(item.success for item in samples) * 10_000 // len(samples)
            if quality < quality_floor or cost > cost_ceiling_usd or p95 > deadline_ms:
                continue
            score = quality + success - int(cost * 1_000) - p95
            ranked.append((score, candidate_id, p95))
        if not ranked:
            return OptimizationRecommendation(None, "minimum_samples_or_limits", 0, None)
        _, selected, p95 = min(ranked, key=lambda item: (-item[0], item[1]))
        return OptimizationRecommendation(
            selected, "measured_quality_cost_latency", len(self._outcomes[selected]), p95
        )

    @staticmethod
    def may_hedge(
        *,
        idempotent: bool,
        remaining_budget_usd: float,
        hedge_cost_usd: float,
        primary_p95_ms: int,
        remaining_deadline_ms: int,
    ) -> bool:
        return (
            idempotent
            and hedge_cost_usd <= remaining_budget_usd
            and primary_p95_ms >= remaining_deadline_ms
        )


__all__ = ["MeasuredOutcome", "OptimizationRecommendation", "RoutingOptimizer"]
