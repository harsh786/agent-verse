"""Config-driven eval scoring constants.

Every weight, threshold and budget used by the 7-dimension eval scorer and the
self-improvement decision surfaces is sourced from :class:`app.core.config.Settings`
(12-factor, env-overridable) rather than hardcoded in the scorer bodies. The
defaults below are *honest* — they reproduce the historically shipped behaviour —
but an operator can now tune scoring per environment without a code change.

Two dataclasses:

* :class:`EvalScoringConfig` — dimension weights/budgets for the 7-dim runner.
* :class:`ImprovementThresholds` — the per-dimension floors that decide which
  self-improvement actions fire. These are shared by
  :class:`app.evals.self_improvement_engine.SelfImprovementEngine` and
  :meth:`app.intelligence.self_optimizer_v2.SelfOptimizerV2.plan_improvement_actions`
  so the two decision surfaces can never silently drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings


@dataclass(frozen=True)
class EvalScoringConfig:
    """Weights, budgets and neutral defaults for the 7-dimension eval scorer."""

    pass_threshold: float
    # efficiency dimension
    max_iterations_budget: float
    cost_budget_usd: float
    efficiency_iter_weight: float
    efficiency_cost_weight: float
    # accuracy dimension
    accuracy_partial_credit: float
    # safety dimension
    safety_violation_penalty: float
    # coherence dimension
    coherence_output_weight: float
    coherence_diversity_weight: float
    # sla dimension
    sla_budget_seconds: float
    sla_iteration_seconds: float
    # tool_relevance dimension
    tool_calls_per_step_target: float
    tool_efficiency_tolerance: float
    tool_relevance_success_weight: float
    tool_relevance_efficiency_weight: float
    # neutral defaults when evidence is missing
    neutral_no_data_score: float
    neutral_no_tool_calls_score: float

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> EvalScoringConfig:
        s = settings if settings is not None else _get_settings()
        return cls(
            pass_threshold=s.eval_pass_threshold,
            max_iterations_budget=s.eval_max_iterations_budget,
            cost_budget_usd=s.eval_cost_budget_usd,
            efficiency_iter_weight=s.eval_efficiency_iter_weight,
            efficiency_cost_weight=s.eval_efficiency_cost_weight,
            accuracy_partial_credit=s.eval_accuracy_partial_credit,
            safety_violation_penalty=s.eval_safety_violation_penalty,
            coherence_output_weight=s.eval_coherence_output_weight,
            coherence_diversity_weight=s.eval_coherence_diversity_weight,
            sla_budget_seconds=s.eval_sla_budget_seconds,
            sla_iteration_seconds=s.eval_sla_iteration_seconds,
            tool_calls_per_step_target=s.eval_tool_calls_per_step_target,
            tool_efficiency_tolerance=s.eval_tool_efficiency_tolerance,
            tool_relevance_success_weight=s.eval_tool_relevance_success_weight,
            tool_relevance_efficiency_weight=s.eval_tool_relevance_efficiency_weight,
            neutral_no_data_score=s.eval_neutral_no_data_score,
            neutral_no_tool_calls_score=s.eval_neutral_no_tool_calls_score,
        )


@dataclass(frozen=True)
class ImprovementThresholds:
    """Per-dimension floors that decide which self-improvement actions fire."""

    rag_quality_floor: float
    retrieval_confidence_floor: float
    goal_success_floor: float
    tool_success_floor: float
    tool_success_critical: float
    cost_efficiency_floor: float
    latency_floor: float
    regression_case_floor: float

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> ImprovementThresholds:
        s = settings if settings is not None else _get_settings()
        return cls(
            rag_quality_floor=s.eval_improve_rag_quality_floor,
            retrieval_confidence_floor=s.eval_improve_retrieval_confidence_floor,
            goal_success_floor=s.eval_improve_goal_success_floor,
            tool_success_floor=s.eval_improve_tool_success_floor,
            tool_success_critical=s.eval_improve_tool_success_critical,
            cost_efficiency_floor=s.eval_improve_cost_efficiency_floor,
            latency_floor=s.eval_improve_latency_floor,
            regression_case_floor=s.eval_improve_regression_case_floor,
        )


def _get_settings() -> Settings:
    # Imported lazily so this module has no import-time dependency on config
    # (avoids a cycle: config → nothing here, scorer → here → config at call time).
    from app.core.config import get_settings

    return get_settings()
