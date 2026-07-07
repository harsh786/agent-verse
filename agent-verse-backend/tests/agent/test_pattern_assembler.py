"""Tests for PatternAssembler — CRITICAL safety rules inviolable per doc-4."""
from __future__ import annotations

import pytest

from app.agent.pattern_assembler import PatternAssembler, pattern_assembler
from app.agent.pattern_config import Complexity, Domain, GoalProperties, PatternConfig, RiskLevel


@pytest.fixture
def assembler() -> PatternAssembler:
    return PatternAssembler()


def _simple_props() -> GoalProperties:
    return GoalProperties(
        complexity=Complexity.SIMPLE,
        domain=Domain.OPERATIONAL,
        risk=RiskLevel.LOW,
        multi_step=False,
        estimated_steps=1,
    )


def test_simple_minimal(assembler: PatternAssembler) -> None:
    props = _simple_props()
    cfg = assembler.assemble(props, {})
    assert "react" in cfg.reasoning_patterns
    assert cfg.reasoning_patterns[0] == "react"
    assert "hitl" not in cfg.safety_patterns
    assert "guardrails" in cfg.safety_patterns


def test_expert_gets_cot_and_reflection(assembler: PatternAssembler) -> None:
    props = GoalProperties(
        complexity=Complexity.EXPERT,
        domain=Domain.TECHNICAL,
        risk=RiskLevel.LOW,
        multi_step=True,
        estimated_steps=6,
    )
    cfg = assembler.assemble(props, {})
    assert "chain_of_thought" in cfg.reasoning_patterns
    assert "reflection" in cfg.reasoning_patterns
    assert cfg.max_iterations == 50
    assert cfg.persistence_mode is True


def test_critical_risk_adds_hitl(assembler: PatternAssembler) -> None:
    props = GoalProperties(
        complexity=Complexity.MEDIUM,
        domain=Domain.OPERATIONAL,
        risk=RiskLevel.CRITICAL,
    )
    cfg = assembler.assemble(props, {})
    assert "hitl" in cfg.safety_patterns
    assert "rollback" in cfg.safety_patterns
    assert "consensus_verification" in cfg.safety_patterns
    assert cfg.autonomy_mode == "supervised"


def test_high_risk_adds_hitl(assembler: PatternAssembler) -> None:
    props = GoalProperties(
        complexity=Complexity.MEDIUM,
        domain=Domain.OPERATIONAL,
        risk=RiskLevel.HIGH,
    )
    cfg = assembler.assemble(props, {})
    assert "hitl" in cfg.safety_patterns
    assert "rollback" in cfg.safety_patterns
    assert cfg.autonomy_mode == "supervised"


def test_safety_cannot_be_removed_force_no_hitl_ignored(assembler: PatternAssembler) -> None:
    """CRITICAL rule: force_no_hitl in agent_config must be ignored."""
    props = GoalProperties(
        complexity=Complexity.MEDIUM,
        domain=Domain.OPERATIONAL,
        risk=RiskLevel.CRITICAL,
    )
    # Pass force_no_hitl — should be silently ignored
    cfg = assembler.assemble(props, {"force_no_hitl": True})
    assert "hitl" in cfg.safety_patterns
    assert "rollback" in cfg.safety_patterns


def test_agent_config_add_cot(assembler: PatternAssembler) -> None:
    props = GoalProperties(
        complexity=Complexity.SIMPLE,
        domain=Domain.OPERATIONAL,
        risk=RiskLevel.LOW,
    )
    cfg = assembler.assemble(props, {"enable_cot": True})
    assert "chain_of_thought" in cfg.reasoning_patterns


def test_web_sets_web_auto_activate(assembler: PatternAssembler) -> None:
    props = GoalProperties(
        complexity=Complexity.MEDIUM,
        domain=Domain.TECHNICAL,
        risk=RiskLevel.LOW,
        requires_web=True,
        time_sensitivity="realtime",
    )
    cfg = assembler.assemble(props, {})
    assert cfg.web_auto_activate is True
    assert "web_augmented_rag" in cfg.rag_patterns


def test_creative_gets_self_refine(assembler: PatternAssembler) -> None:
    props = GoalProperties(
        complexity=Complexity.MEDIUM,
        domain=Domain.CREATIVE,
        risk=RiskLevel.LOW,
        is_generative=True,
    )
    cfg = assembler.assemble(props, {})
    assert "self_refine" in cfg.reasoning_patterns


def test_expert_multistep_gets_goal_tree(assembler: PatternAssembler) -> None:
    props = GoalProperties(
        complexity=Complexity.EXPERT,
        domain=Domain.TECHNICAL,
        risk=RiskLevel.LOW,
        multi_step=True,
        estimated_steps=7,
    )
    cfg = assembler.assemble(props, {})
    assert "goal_tree" in cfg.multi_agent_patterns
    assert "single_agent" not in cfg.multi_agent_patterns


def test_latency_recorded(assembler: PatternAssembler) -> None:
    props = _simple_props()
    cfg = assembler.assemble(props, {})
    assert cfg.assembly_latency_ms >= 0.0


def test_singleton_exists() -> None:
    assert pattern_assembler is not None
    assert isinstance(pattern_assembler, PatternAssembler)
