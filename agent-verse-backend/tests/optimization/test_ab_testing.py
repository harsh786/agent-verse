"""A/B testing: arm selection, eval regression gating."""
from __future__ import annotations
import pytest
from app.optimization.ab_testing import ABTestingEngine, ExperimentType, ExperimentArm, ab_testing_engine


def test_arm_selection_deterministic():
    engine = ABTestingEngine()
    arm1 = engine.get_experiment_arm("goal_abc", ExperimentType.PLANNER_PROMPT)
    arm2 = engine.get_experiment_arm("goal_abc", ExperimentType.PLANNER_PROMPT)
    assert arm1.arm_id == arm2.arm_id


def test_arm_selection_different_goals_may_differ():
    engine = ABTestingEngine()
    arms = {engine.get_experiment_arm(f"goal_{i}", ExperimentType.PLANNER_PROMPT).arm_id for i in range(20)}
    assert len(arms) >= 1


def test_experiment_types_defined():
    assert ExperimentType.PLANNER_PROMPT is not None
    assert ExperimentType.MODEL_ROUTING is not None
    assert ExperimentType.RAG_STRATEGY is not None


def test_ab_test_record_and_stats():
    engine = ABTestingEngine()
    arm = engine.get_experiment_arm("g1", ExperimentType.PLANNER_PROMPT)
    engine.record_result("g1", ExperimentType.PLANNER_PROMPT, arm.arm_id, score=0.85)
    stats = engine.get_arm_stats(ExperimentType.PLANNER_PROMPT, arm.arm_id)
    assert stats["call_count"] >= 1


def test_promotion_blocked_below_threshold():
    engine = ABTestingEngine()
    can_promote = engine.can_promote_variant(ExperimentType.PLANNER_PROMPT, "variant_b",
                                              min_score_threshold=0.72, current_score=0.65)
    assert can_promote is False


def test_promotion_allowed_above_threshold():
    engine = ABTestingEngine()
    can_promote = engine.can_promote_variant(ExperimentType.PLANNER_PROMPT, "variant_b",
                                              min_score_threshold=0.72, current_score=0.88)
    assert can_promote is True


def test_module_singleton_exists():
    assert ab_testing_engine is not None
    arm = ab_testing_engine.get_experiment_arm("g1", ExperimentType.MODEL_ROUTING)
    assert arm.arm_id in ("control", "variant_a", "variant_b")
