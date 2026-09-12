"""Tests for PatternConfig and GoalProperties — exact doc-4 contracts."""
from __future__ import annotations

import pytest

from app.agent.pattern_config import (
    Complexity,
    Domain,
    GoalProperties,
    PatternConfig,
    RiskLevel,
)


def test_goal_properties_defaults() -> None:
    props = GoalProperties()
    assert props.complexity == Complexity.MEDIUM
    assert props.domain == Domain.TECHNICAL
    assert props.risk == RiskLevel.LOW
    assert props.time_sensitivity == "normal"
    assert props.knowledge_requirement == "kb_only"
    assert props.reversibility == "reversible"
    assert props.multi_step is True
    assert props.is_generative is False
    assert props.requires_web is False
    assert props.estimated_steps == 3
    assert props.confidence == 0.8


def test_pattern_config_defaults() -> None:
    cfg = PatternConfig()
    assert cfg.reasoning_patterns == ["reflection"]
    assert cfg.rag_patterns == ["hybrid_rag"]
    assert cfg.multi_agent_patterns == ["single_agent"]
    assert cfg.safety_patterns == ["guardrails"]
    assert cfg.model_planner == "gpt-5.2"
    assert cfg.model_executor == "gpt-5.2"
    assert cfg.model_verifier == "gpt-5.2"
    assert cfg.model_classifier == "gpt-4o-mini"
    assert cfg.max_iterations == 6
    assert cfg.max_refine_iterations == 2
    assert cfg.persistence_mode is False
    assert cfg.max_persistence_attempts == 3
    assert cfg.autonomy_mode == "bounded-autonomous"
    assert cfg.web_auto_activate is False
    assert cfg.goal_properties is None
    assert cfg.selection_reason == {}
    assert cfg.assembly_latency_ms == 0.0


def test_complexity_enum_values() -> None:
    assert Complexity.SIMPLE.value == "simple"
    assert Complexity.MEDIUM.value == "medium"
    assert Complexity.COMPLEX.value == "complex"
    assert Complexity.EXPERT.value == "expert"


def test_risk_level_enum_values() -> None:
    assert RiskLevel.LOW.value == "low"
    assert RiskLevel.MEDIUM.value == "medium"
    assert RiskLevel.HIGH.value == "high"
    assert RiskLevel.CRITICAL.value == "critical"


def test_domain_enum_values() -> None:
    assert Domain.TECHNICAL.value == "technical"
    assert Domain.CREATIVE.value == "creative"
    assert Domain.ANALYTICAL.value == "analytical"
    assert Domain.OPERATIONAL.value == "operational"
    assert Domain.CONVERSATIONAL.value == "conversational"


def test_pattern_config_stores_goal_properties() -> None:
    props = GoalProperties(
        complexity=Complexity.EXPERT,
        domain=Domain.ANALYTICAL,
        risk=RiskLevel.HIGH,
    )
    cfg = PatternConfig(goal_properties=props)
    assert cfg.goal_properties is props
    assert cfg.goal_properties.complexity == Complexity.EXPERT
    assert cfg.goal_properties.domain == Domain.ANALYTICAL
    assert cfg.goal_properties.risk == RiskLevel.HIGH


def test_to_sse_event_shape() -> None:
    props = GoalProperties(complexity=Complexity.COMPLEX, risk=RiskLevel.HIGH)
    cfg = PatternConfig(
        reasoning_patterns=["react", "chain_of_thought"],
        rag_patterns=["hybrid_rag", "agentic_rag"],
        multi_agent_patterns=["goal_tree"],
        safety_patterns=["guardrails", "hitl"],
        selection_reason={"hitl": "risk=high"},
        assembly_latency_ms=1.23,
        goal_properties=props,
    )
    event = cfg.to_sse_event("goal-abc")
    assert event["type"] == "pattern_assembled"
    assert event["goal_id"] == "goal-abc"
    assert event["complexity"] == "complex"
    assert event["risk"] == "high"
    assert event["patterns_active"]["reasoning"] == ["react", "chain_of_thought"]
    assert event["patterns_active"]["rag"] == ["hybrid_rag", "agentic_rag"]
    assert event["patterns_active"]["multi_agent"] == ["goal_tree"]
    assert event["patterns_active"]["safety"] == ["guardrails", "hitl"]
    assert event["models"]["planner"] == "gpt-5.2"
    assert event["models"]["executor"] == "gpt-5.2"
    assert event["models"]["verifier"] == "gpt-5.2"
    assert event["selection_reasons"] == {"hitl": "risk=high"}
    assert event["assembly_latency_ms"] == pytest.approx(1.23)


def test_to_sse_event_no_goal_properties() -> None:
    cfg = PatternConfig()
    event = cfg.to_sse_event("goal-xyz")
    assert event["complexity"] == "unknown"
    assert event["risk"] == "unknown"
