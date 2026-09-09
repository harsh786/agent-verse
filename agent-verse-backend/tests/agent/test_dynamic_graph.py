"""Tests for DynamicGraphAssembler."""
from __future__ import annotations

import pytest

from app.agent.dynamic_graph import DynamicGraphAssembler
from app.agent.pattern_config import Complexity, GoalProperties, PatternConfig, RiskLevel
from app.providers.fake import FakeProvider


@pytest.fixture
def assembler() -> DynamicGraphAssembler:
    return DynamicGraphAssembler()


def _make_providers():
    planner = FakeProvider(responses=['{"steps": ["Step 1: test"]}'])
    executor = FakeProvider(responses=["Done"])
    verifier = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    return planner, executor, verifier


def test_assembles_graph(assembler: DynamicGraphAssembler) -> None:
    cfg = PatternConfig(max_iterations=10)
    planner, executor, verifier = _make_providers()
    graph = assembler.assemble(cfg, planner=planner, executor=executor, verifier=verifier)
    assert graph is not None
    assert graph._pattern_config is cfg


def test_simple_no_debate_nodes(assembler: DynamicGraphAssembler) -> None:
    cfg = PatternConfig(
        reasoning_patterns=["react"],
        rag_patterns=["hybrid_rag"],
        multi_agent_patterns=["single_agent"],
        safety_patterns=["guardrails"],
    )
    nodes = assembler.get_active_nodes(cfg)
    assert "debate" not in nodes
    assert "hitl_check" not in nodes
    assert "initialize" in nodes
    assert "plan" in nodes
    assert "execute" in nodes
    assert "verify" in nodes


def test_critical_has_hitl_check(assembler: DynamicGraphAssembler) -> None:
    cfg = PatternConfig(
        reasoning_patterns=["react"],
        safety_patterns=["hitl", "guardrails", "rollback"],
    )
    nodes = assembler.get_active_nodes(cfg)
    assert "hitl_check" in nodes


def test_expert_gets_rag_prime(assembler: DynamicGraphAssembler) -> None:
    cfg = PatternConfig(
        reasoning_patterns=["react"],
        rag_patterns=["hybrid_rag", "agentic_rag"],
    )
    nodes = assembler.get_active_nodes(cfg)
    assert "rag_prime" in nodes
    assert "rag_remediate" in nodes


def test_get_active_nodes_simple(assembler: DynamicGraphAssembler) -> None:
    cfg = PatternConfig(
        reasoning_patterns=["react"],
        rag_patterns=["hybrid_rag"],
        multi_agent_patterns=["single_agent"],
        safety_patterns=["guardrails"],
    )
    nodes = assembler.get_active_nodes(cfg)
    assert set(nodes) >= {"initialize", "rag_prime", "plan", "execute", "verify"}


def test_sse_event_shape(assembler: DynamicGraphAssembler) -> None:
    props = GoalProperties(complexity=Complexity.EXPERT, risk=RiskLevel.CRITICAL)
    cfg = PatternConfig(
        goal_properties=props,
        reasoning_patterns=["react", "chain_of_thought"],
        safety_patterns=["hitl", "guardrails"],
        assembly_latency_ms=2.5,
    )
    event = cfg.to_sse_event("goal-test-123")
    assert event["type"] == "pattern_assembled"
    assert event["goal_id"] == "goal-test-123"
    assert event["complexity"] == "expert"
    assert event["risk"] == "critical"
    assert "hitl" in event["patterns_active"]["safety"]
    assert event["assembly_latency_ms"] == pytest.approx(2.5)
