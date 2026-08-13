from __future__ import annotations

from app.orchestration.compatibility import CompatibilityEvaluator
from app.orchestration.pattern_selector import PatternSelector
from app.orchestration.runtime_profile import Complexity, GoalProperties
from app.orchestration.strategy_adapters import ExecutionTier
from app.orchestration.strategy_registry import build_default_registry


def test_accepts_one_primary_and_compatible_cross_cutting_auxiliary() -> None:
    registry = build_default_registry()
    decision = CompatibilityEvaluator(registry).compose(
        primary_id="react",
        candidate_auxiliary_ids=("guardrails",),
        ready_ids=frozenset({"react", "guardrails"}),
    )

    assert decision.primary.strategy_id == "react"
    assert decision.execution_tier is ExecutionTier.LOCAL
    assert [item.strategy_id for item in decision.accepted] == ["guardrails"]


def test_rejects_disabled_unready_and_incompatible_candidates_deterministically() -> None:
    registry = build_default_registry()
    decision = CompatibilityEvaluator(registry).compose(
        primary_id="react",
        candidate_auxiliary_ids=("workflow_dag", "codeact", "missing"),
        ready_ids=frozenset({"react", "workflow_dag"}),
    )

    assert [(item.strategy_id, item.reason_code) for item in decision.rejected] == [
        ("workflow_dag", "incompatible_primary_tier"),
        ("codeact", "not_ready"),
        ("missing", "unknown_strategy"),
    ]


def test_generated_code_and_distributed_strategies_require_runtime_dependencies() -> None:
    registry = build_default_registry()
    evaluator = CompatibilityEvaluator(registry)

    code = evaluator.compose(
        primary_id="codeact",
        candidate_auxiliary_ids=(),
        ready_ids=frozenset({"codeact"}),
    )
    distributed = evaluator.compose(
        primary_id="debate",
        candidate_auxiliary_ids=(),
        ready_ids=frozenset({"debate"}),
    )

    assert code.primary_rejection == "sandbox_not_ready"
    assert distributed.primary_rejection == "coordination_not_ready"


def test_pattern_selector_scores_candidates_without_claiming_topology_authority() -> None:
    registry = build_default_registry()
    candidates = PatternSelector(registry).score_reasoning_candidates(
        GoalProperties(raw_goal="complex research", complexity=Complexity.COMPLEX)
    )

    assert candidates[0].strategy_id == "react"
    assert candidates[0].score > candidates[-1].score
