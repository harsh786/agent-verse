"""Regression capture plus quantitative, version-matched promotion gates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.evals.regression_baseline import (
    AggregateMetrics,
    BaselineKey,
    RegressionBaseline,
)

if TYPE_CHECKING:
    from app.agent.state import AgentState
    from app.evals.runtime_scorecard import ScorecardResult
    from app.orchestration.runtime_profile import GoalRuntimeProfile

_FAILURE_THRESHOLD = 0.6


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    passed: bool
    reasons: tuple[str, ...]
    metric_deltas: dict[str, float] = field(default_factory=dict)
    recommendation: str = "hold"


class RegressionGate:
    def __init__(
        self,
        threshold: float = _FAILURE_THRESHOLD,
        *,
        minimum_samples: int = 30,
        minimum_coverage: float = 0.9,
        max_quality_regression: float = 0.02,
        max_cost_regression: float = 0.10,
        max_latency_regression: float = 0.15,
    ) -> None:
        self._threshold = threshold
        self._minimum_samples = minimum_samples
        self._minimum_coverage = minimum_coverage
        self._max_quality_regression = max_quality_regression
        self._max_cost_regression = max_cost_regression
        self._max_latency_regression = max_latency_regression

    def evaluate_promotion(
        self,
        *,
        baseline: RegressionBaseline,
        candidate_key: BaselineKey,
        candidate: AggregateMetrics,
    ) -> PromotionDecision:
        if candidate_key.identity != baseline.key.identity:
            return PromotionDecision(False, ("evidence_version_mismatch",))

        reference = baseline.metrics
        deltas = {
            "quality": candidate.quality - reference.quality,
            "safety": candidate.safety - reference.safety,
            "cost_ratio": self._ratio_delta(
                candidate.mean_cost_usd, reference.mean_cost_usd
            ),
            "latency_ratio": self._ratio_delta(
                candidate.p95_latency_ms, reference.p95_latency_ms
            ),
            "coverage": candidate.coverage - reference.coverage,
        }
        reasons: list[str] = []
        if deltas["quality"] < -self._max_quality_regression:
            reasons.append("quality_regression")
        if deltas["safety"] < 0:
            reasons.append("safety_regression")
        if deltas["cost_ratio"] > self._max_cost_regression:
            reasons.append("cost_regression")
        if deltas["latency_ratio"] > self._max_latency_regression:
            reasons.append("latency_regression")
        if candidate.coverage < self._minimum_coverage:
            reasons.append("insufficient_coverage")
        if candidate.sample_size < self._minimum_samples:
            reasons.append("insufficient_samples")
        if not candidate.policy_passed:
            reasons.append("policy_gate_failed")
        if not candidate.tenant_isolation_passed:
            reasons.append("tenant_isolation_gate_failed")
        return PromotionDecision(
            passed=not reasons,
            reasons=tuple(reasons),
            metric_deltas=deltas,
            recommendation="promote" if not reasons else "hold",
        )

    def evaluate_canary_windows(
        self,
        *,
        baseline: RegressionBaseline,
        candidate_key: BaselineKey,
        windows: list[AggregateMetrics],
    ) -> PromotionDecision:
        if not windows:
            return PromotionDecision(False, ("missing_canary_windows",))
        decisions = [
            self.evaluate_promotion(
                baseline=baseline,
                candidate_key=candidate_key,
                candidate=window,
            )
            for window in windows
        ]
        reasons = tuple(
            dict.fromkeys(reason for decision in decisions for reason in decision.reasons)
        )
        if reasons:
            return PromotionDecision(
                False,
                reasons,
                recommendation="freeze_and_recommend_kill_switch",
            )
        return PromotionDecision(True, (), recommendation="expand")

    @staticmethod
    def _ratio_delta(candidate: float, baseline: float) -> float:
        if baseline <= 0:
            return 0.0 if candidate <= 0 else float("inf")
        return (candidate - baseline) / baseline

    def maybe_create_regression(
        self,
        *,
        state: AgentState,
        scorecard: ScorecardResult,
        profile: GoalRuntimeProfile,
    ) -> dict[str, Any] | None:
        if scorecard.overall_score >= self._threshold:
            return None
        from app.agent.state import GoalStatus

        if state.status not in (GoalStatus.FAILED, GoalStatus.COMPLETE):
            return None
        return {
            "goal_id": state.goal_id,
            "goal_text": state.goal[:200],
            "tenant_id": state.tenant_ctx.tenant_id,
            "overall_score": scorecard.overall_score,
            "scores": scorecard.scores,
            "status": state.status.value,
            "improvement_suggestions": scorecard.improvement_suggestions,
            "profile_id": profile.profile_id,
            "profile_version": profile.profile_version,
            "strategy_id": scorecard.primary_strategy_id,
            "strategy_version": scorecard.primary_strategy_version,
            "evaluator_version": scorecard.evaluator_version,
            "coverage": scorecard.coverage,
        }
