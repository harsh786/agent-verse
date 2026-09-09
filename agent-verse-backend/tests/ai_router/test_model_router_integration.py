"""ModelOrchestrator end-to-end: classify→assemble→select pipeline."""
from __future__ import annotations

import pytest

from app.agent.goal_classifier import goal_classifier
from app.agent.pattern_assembler import pattern_assembler
from app.agent.pattern_config import Complexity, GoalProperties, RiskLevel
from app.ai_router.model_orchestrator import ModelOrchestrator


@pytest.fixture
def orchestrator():
    return ModelOrchestrator()


def test_simple_goal_gets_low_cost_models(orchestrator):
    goal = "list all open Jira tickets"
    props = goal_classifier.classify_fast(goal)
    cfg = pattern_assembler.assemble(props, agent_config={})
    assignment = orchestrator.select_models(cfg)
    assert assignment.quality_tier == "low"
    assert assignment.planner is not None
    assert assignment.executor is not None
    assert assignment.verifier is not None
    assert assignment.judge is not None
    assert assignment.embedder is not None
    assert assignment.reranker is not None
    assert assignment.classifier is not None


def test_critical_goal_gets_high_quality_models(orchestrator):
    goal = "delete all records from the production database"
    props = goal_classifier.classify_fast(goal)
    cfg = pattern_assembler.assemble(props, agent_config={})
    assignment = orchestrator.select_models(cfg)
    assert assignment.quality_tier in ("medium", "high")
    assert "mini" not in assignment.verifier.lower()


def test_pattern_config_hints_respected(orchestrator):
    from app.agent.pattern_config import GoalProperties, PatternConfig
    cfg = PatternConfig(
        model_planner="gpt-4o-mini", model_executor="gpt-4o-mini", model_verifier="gpt-4o",
        goal_properties=GoalProperties(complexity=Complexity.EXPERT),
    )
    assignment = orchestrator.select_models(cfg)
    assert assignment.planner == "gpt-4o-mini"
    assert assignment.verifier == "gpt-4o"


def test_budget_downgrade_reduces_tier(orchestrator):
    from app.agent.pattern_config import Complexity, GoalProperties, PatternConfig
    cfg = PatternConfig(goal_properties=GoalProperties(complexity=Complexity.EXPERT))
    full = orchestrator.select_models(cfg, budget_spent_ratio=0.0)
    degraded = orchestrator.select_models(cfg, budget_spent_ratio=0.85)
    tier_order = {"low": 0, "medium": 1, "high": 2}
    assert tier_order[degraded.quality_tier] <= tier_order[full.quality_tier]


def test_all_content_types_get_valid_model_assignment(orchestrator):
    from app.ingestion.content_classifier import ContentType
    for ct in ContentType:
        assignment = orchestrator.select_for_content_type(ct)
        assert assignment.extractor_model, f"{ct.value}: extractor_model is empty"
        assert assignment.reasoner_model, f"{ct.value}: reasoner_model is empty"


def test_image_content_gets_vision_model(orchestrator):
    from app.ingestion.content_classifier import ContentType
    assignment = orchestrator.select_for_content_type(ContentType.IMAGE)
    assert assignment.requires_vision is True
    assert assignment.extractor_model is not None


def test_provider_failover_on_circuit_open():
    from app.ai_router.provider_health_policy import ProviderHealthPolicy
    health_policy = ProviderHealthPolicy()
    for _ in range(6):
        health_policy.record_failure("openai")
    assert health_policy.check("openai").circuit_open is True
    orchestrator = ModelOrchestrator(health_policy=health_policy)
    from app.agent.pattern_config import PatternConfig
    cfg = PatternConfig(model_planner="gpt-5.2")
    assignment = orchestrator.select_models(cfg)
    assert assignment.planner is not None and assignment.planner != ""


def test_latency_class_realtime_for_realtime_goal(orchestrator):
    from app.agent.pattern_config import Complexity, GoalProperties, PatternConfig, RiskLevel
    cfg = PatternConfig(goal_properties=GoalProperties(
        complexity=Complexity.SIMPLE, risk=RiskLevel.LOW, time_sensitivity="realtime"
    ))
    assignment = orchestrator.select_models(cfg)
    assert assignment.latency_class == "realtime"
    assert assignment.quality_tier == "low"
