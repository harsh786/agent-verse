"""Tests for PatternSelector — 10 tests covering goal archetypes."""
from __future__ import annotations

import pytest
from app.orchestration.pattern_selector import PatternSelector
from app.orchestration.runtime_profile import (
    Complexity,
    Domain,
    GoalProperties,
    KnowledgeState,
    RiskLevel,
    TimeSensitivity,
)
from app.orchestration.strategy_registry import build_default_registry


def _selector() -> PatternSelector:
    return PatternSelector(registry=build_default_registry())


def _props(**kwargs) -> GoalProperties:  # type: ignore[no-untyped-def]
    defaults = dict(raw_goal="test goal")
    defaults.update(kwargs)
    return GoalProperties(**defaults)


# ---------------------------------------------------------------------------
# 1. Simple low-risk: cheap model, guardrails only, semantic cache enabled
# ---------------------------------------------------------------------------
def test_simple_low_risk_profile():
    sel = _selector()
    props = _props(complexity=Complexity.SIMPLE, risk=RiskLevel.LOW)
    agent = sel.select_agent_patterns(props)
    mem = sel.select_memory_cache_policy(props)
    model = sel.select_model_plan(props)
    assert "hitl" not in agent.safety
    assert "rollback" not in agent.safety
    assert mem.use_semantic_cache is True
    assert model.cost_class == "low"


# ---------------------------------------------------------------------------
# 2. Complex goal: adds CoT + reflection, rrf reranker, LTM enabled
# ---------------------------------------------------------------------------
def test_complex_goal_patterns():
    sel = _selector()
    props = _props(complexity=Complexity.COMPLEX, risk=RiskLevel.LOW)
    agent = sel.select_agent_patterns(props)
    rag = sel.select_rag_strategy(props)
    mem = sel.select_memory_cache_policy(props)
    assert "chain_of_thought" in agent.reasoning
    assert "reflection" in agent.reasoning
    assert rag.reranker == "rrf"
    assert mem.use_long_term_memory is True


# ---------------------------------------------------------------------------
# 3. Critical risk: HITL + rollback + consensus in safety list
# ---------------------------------------------------------------------------
def test_critical_risk_safety_profile():
    sel = _selector()
    props = _props(risk=RiskLevel.CRITICAL, reversibility="irreversible")
    agent = sel.select_agent_patterns(props)
    sec = sel.select_security_profile(props)
    assert "hitl" in agent.safety
    assert "rollback" in agent.safety
    assert "consensus_verification" in agent.safety
    assert sec.hitl_required is True
    assert sec.consensus_required is True
    assert sec.rollback_required is True
    assert sec.audit_level == "forensic"
    assert agent.autonomy_mode == "supervised"


# ---------------------------------------------------------------------------
# 4. Coding goal: sandbox required, ast chunking, coding eval suite
# ---------------------------------------------------------------------------
def test_coding_goal_sandbox_and_ast():
    sel = _selector()
    props = _props(requires_code=True, complexity=Complexity.SIMPLE, risk=RiskLevel.LOW)
    sec = sel.select_security_profile(props)
    rag = sel.select_rag_strategy(props)
    ev = sel.select_eval_config(props)
    assert sec.sandbox_required is True
    assert rag.chunking_strategy == "ast"
    assert rag.embedding_model == "code"
    assert ev.eval_suite == "coding"


# ---------------------------------------------------------------------------
# 5. Web-requiring goal: web_augmented_rag, web_search in sources
# ---------------------------------------------------------------------------
def test_web_goal_rag_strategy():
    sel = _selector()
    props = _props(requires_web=True, time_sensitivity=TimeSensitivity.REALTIME)
    rag = sel.select_rag_strategy(props)
    assert rag.web_fallback_enabled is True
    assert "web_search" in rag.sources
    assert rag.strategy == "web_augmented_rag"


# ---------------------------------------------------------------------------
# 6. Empty KB state: web fallback forced on
# ---------------------------------------------------------------------------
def test_empty_kb_forces_web_fallback():
    sel = _selector()
    props = _props(kb_state=KnowledgeState.EMPTY)
    rag = sel.select_rag_strategy(props)
    assert rag.web_fallback_enabled is True
    assert "web_search" in rag.sources


# ---------------------------------------------------------------------------
# 7. Realtime time sensitivity: low token budget, low cost model
# ---------------------------------------------------------------------------
def test_realtime_token_budget():
    sel = _selector()
    props = _props(time_sensitivity=TimeSensitivity.REALTIME, complexity=Complexity.SIMPLE)
    rag = sel.select_rag_strategy(props)
    model = sel.select_model_plan(props)
    assert rag.max_context_tokens <= 2000
    assert model.cost_class == "low"
    assert model.latency_class == "realtime"


# ---------------------------------------------------------------------------
# 8. Expert complexity: agentic_rag, goal_tree, LTM+KG memory, rag eval suite
# ---------------------------------------------------------------------------
def test_expert_complexity_full_profile():
    sel = _selector()
    props = _props(complexity=Complexity.EXPERT, risk=RiskLevel.LOW)
    agent = sel.select_agent_patterns(props)
    rag = sel.select_rag_strategy(props)
    mem = sel.select_memory_cache_policy(props)
    ev = sel.select_eval_config(props)
    assert "goal_tree" in agent.multi_agent
    assert agent.persistence_mode is True
    assert agent.max_iterations == 50
    assert rag.strategy == "agentic_rag"
    assert "knowledge_graph" in rag.sources
    assert mem.use_knowledge_graph is True
    assert ev.eval_suite == "rag"


# ---------------------------------------------------------------------------
# 9. Generative/creative goal: self_refine added to reasoning
# ---------------------------------------------------------------------------
def test_generative_goal_adds_self_refine():
    sel = _selector()
    props = _props(is_generative=True, complexity=Complexity.SIMPLE, risk=RiskLevel.LOW)
    agent = sel.select_agent_patterns(props)
    assert "self_refine" in agent.reasoning


# ---------------------------------------------------------------------------
# 10. High risk: HITL + rollback but NOT consensus (only critical gets that)
# ---------------------------------------------------------------------------
def test_high_risk_no_consensus():
    sel = _selector()
    props = _props(risk=RiskLevel.HIGH, complexity=Complexity.MEDIUM)
    agent = sel.select_agent_patterns(props)
    sec = sel.select_security_profile(props)
    assert "hitl" in agent.safety
    assert "rollback" in agent.safety
    assert "consensus_verification" not in agent.safety
    assert sec.hitl_required is True
    assert sec.consensus_required is False
    assert sec.audit_level == "full"
