"""DynamicGraphAssembler must activate correct nodes per PatternConfig."""
from __future__ import annotations

import pytest
from app.agent.dynamic_graph import DynamicGraphAssembler
from app.agent.goal_classifier import goal_classifier
from app.agent.pattern_assembler import pattern_assembler
from app.agent.pattern_config import PatternConfig, GoalProperties, Complexity, RiskLevel, Domain
from app.providers.fake import FakeProvider


@pytest.fixture
def assembler():
    return DynamicGraphAssembler()


@pytest.fixture
def provider():
    return FakeProvider()


def test_simple_goal_nodes(assembler, provider):
    cfg = PatternConfig(reasoning_patterns=["react"], multi_agent_patterns=["single_agent"],
                        safety_patterns=["guardrails"])
    nodes = assembler.get_active_nodes(cfg)
    assert "initialize" in nodes
    assert "rag_prime" in nodes
    assert "plan" in nodes
    assert "execute" in nodes
    assert "verify" in nodes
    assert "debate" not in nodes
    assert "think" not in nodes


def test_expert_goal_activates_cot(assembler, provider):
    cfg = PatternConfig(reasoning_patterns=["react", "chain_of_thought", "reflection"],
                        goal_properties=GoalProperties(complexity=Complexity.EXPERT))
    nodes = assembler.get_active_nodes(cfg)
    assert "think" in nodes
    assert "reflect" in nodes


def test_self_refine_activates_refine_node(assembler, provider):
    cfg = PatternConfig(reasoning_patterns=["react", "self_refine"])
    nodes = assembler.get_active_nodes(cfg)
    assert "refine" in nodes


def test_critical_config_has_hitl(assembler, provider):
    cfg = PatternConfig(safety_patterns=["guardrails", "hitl", "rollback"],
                        autonomy_mode="supervised",
                        goal_properties=GoalProperties(risk=RiskLevel.CRITICAL))
    nodes = assembler.get_active_nodes(cfg)
    assert "hitl_check" in nodes


def test_rag_remediate_activates_for_agentic_rag(assembler, provider):
    cfg = PatternConfig(rag_patterns=["hybrid_rag", "agentic_rag"])
    nodes = assembler.get_active_nodes(cfg)
    assert "rag_remediate" in nodes


def test_classify_expert_produces_cot_nodes(assembler, provider):
    goal = "design and architect a distributed rate-limiting system for multi-tenant scalability"
    props = goal_classifier.classify_fast(goal)
    cfg = pattern_assembler.assemble(props, agent_config={})
    nodes = assembler.get_active_nodes(cfg)
    assert any(n in nodes for n in ["think", "reflect"]), f"Expert goal should activate CoT/reflection. Nodes: {nodes}"


def test_classify_critical_produces_hitl_node(assembler, provider):
    goal = "delete all records from the production database permanently"
    props = goal_classifier.classify_fast(goal)
    cfg = pattern_assembler.assemble(props, agent_config={})
    nodes = assembler.get_active_nodes(cfg)
    assert "hitl_check" in nodes, f"Critical goal must activate hitl_check. Nodes: {nodes}"


def test_pattern_assembled_sse_event_shape(assembler, provider):
    cfg = PatternConfig(
        reasoning_patterns=["react", "chain_of_thought"],
        rag_patterns=["hybrid_rag", "agentic_rag"],
        multi_agent_patterns=["goal_tree"],
        safety_patterns=["guardrails", "hitl"],
        goal_properties=GoalProperties(complexity=Complexity.EXPERT, risk=RiskLevel.LOW),
        selection_reason={"chain_of_thought": "complexity=expert"},
        assembly_latency_ms=1.2,
    )
    event = cfg.to_sse_event("goal_1")
    assert event["type"] == "pattern_assembled"
    assert event["goal_id"] == "goal_1"
    assert event["complexity"] == "expert"
    assert event["patterns_active"]["reasoning"] == ["react", "chain_of_thought"]
    assert event["patterns_active"]["safety"] == ["guardrails", "hitl"]
    assert event["assembly_latency_ms"] == 1.2


def test_loop_engineering_pattern_maps_to_existing_node():
    from app.agent.patterns.loop_engineering import LoopEngineeringPattern
    from app.agent.graph import AgentGraph
    p = LoopEngineeringPattern()
    assert p.pattern_id == "loop_engineering"
    assert hasattr(AgentGraph, "_execute_step") or hasattr(AgentGraph, "execute_step")


def test_reflexion_wirer_available():
    from app.agent.reflexion_wirer import ReflexionWirer, get_reflexion_wirer
    wirer = get_reflexion_wirer()
    assert wirer is not None and isinstance(wirer, ReflexionWirer)
