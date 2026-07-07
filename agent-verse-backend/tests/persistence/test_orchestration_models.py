"""All orchestration state persists to Postgres — ORM model existence tests."""
from __future__ import annotations


def test_eval_scorecard_model_exists() -> None:
    from app.db.models.orchestration import EvalScorecard

    assert hasattr(EvalScorecard, "goal_id")
    assert hasattr(EvalScorecard, "overall_score")
    assert hasattr(EvalScorecard, "scores")
    assert hasattr(EvalScorecard, "tenant_id")


def test_tool_trust_record_model_exists() -> None:
    from app.db.models.orchestration import ToolTrustRecord

    assert hasattr(ToolTrustRecord, "tool_name")
    assert hasattr(ToolTrustRecord, "tenant_id")
    assert hasattr(ToolTrustRecord, "success_rate")
    assert hasattr(ToolTrustRecord, "call_count")


def test_self_improvement_action_model_exists() -> None:
    from app.db.models.orchestration import SelfImprovementAction

    assert hasattr(SelfImprovementAction, "goal_id")
    assert hasattr(SelfImprovementAction, "action_type")
    assert hasattr(SelfImprovementAction, "reason")


def test_ab_test_result_model_exists() -> None:
    from app.db.models.orchestration import ABTestResult

    assert hasattr(ABTestResult, "goal_id")
    assert hasattr(ABTestResult, "experiment_type")
    assert hasattr(ABTestResult, "arm_id")
    assert hasattr(ABTestResult, "score")


def test_reflexion_lesson_model_exists() -> None:
    from app.db.models.orchestration import ReflexionLesson

    assert hasattr(ReflexionLesson, "tenant_id")
    assert hasattr(ReflexionLesson, "lesson")
    assert hasattr(ReflexionLesson, "failure_class")
    assert hasattr(ReflexionLesson, "source_goal_id")
