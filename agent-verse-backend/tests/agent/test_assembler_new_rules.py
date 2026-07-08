# tests/agent/test_assembler_new_rules.py
"""PatternAssembler must activate new patterns based on goal properties."""
from __future__ import annotations
import pytest
from app.agent.pattern_assembler import PatternAssembler
from app.agent.pattern_config import GoalProperties, RiskLevel, Complexity, Domain


def _assemble(props: GoalProperties) -> object:
    assembler = PatternAssembler()
    return assembler.assemble(props, {})


def test_self_consistency_activated_for_expert_analytical():
    props = GoalProperties(complexity=Complexity.EXPERT, domain=Domain.ANALYTICAL)
    config = _assemble(props)
    assert "self_consistency" in config.reasoning_patterns


def test_tree_of_thoughts_activated_for_complex_multistep():
    props = GoalProperties(complexity=Complexity.COMPLEX, multi_step=True, domain=Domain.TECHNICAL)
    config = _assemble(props)
    assert "tree_of_thoughts" in config.reasoning_patterns


def test_peer_review_activated_for_critical_expert():
    props = GoalProperties(risk=RiskLevel.CRITICAL, complexity=Complexity.EXPERT)
    config = _assemble(props)
    assert "peer_review" in config.reasoning_patterns


def test_fusion_rag_activated_for_analytical():
    props = GoalProperties(domain=Domain.ANALYTICAL, complexity=Complexity.COMPLEX)
    config = _assemble(props)
    assert "fusion_rag" in config.rag_patterns


def test_flare_activated_for_web_goals():
    props = GoalProperties(requires_web=True)
    config = _assemble(props)
    assert "flare" in config.rag_patterns


def test_corrective_rag_activated_for_high_risk_knowledge():
    props = GoalProperties(domain=Domain.ANALYTICAL, risk=RiskLevel.HIGH)
    config = _assemble(props)
    assert "corrective_rag" in config.rag_patterns


def test_simple_goal_no_advanced_patterns():
    props = GoalProperties(complexity=Complexity.SIMPLE)
    config = _assemble(props)
    assert "self_consistency" not in config.reasoning_patterns
    assert "tree_of_thoughts" not in config.reasoning_patterns
    assert "raptor" not in config.rag_patterns
