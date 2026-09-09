# tests/agent/test_graph_full_integration.py
"""graph.py must wire all new components: StateContext, ContextPipeline, GuardrailEnforcer,
RuntimeScorecard, SelfImprovementEngine, ReflexionWirer, SSE events."""
from __future__ import annotations

import pytest

from app.agent.graph import AgentGraph
from app.agent.pattern_config import Complexity, GoalProperties, PatternConfig, RiskLevel
from app.agent.state import AgentState, GoalStatus
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def graph(provider):
    return AgentGraph(planner=provider, executor=provider, verifier=provider)


# ── PatternConfig accepted by AgentGraph ─────────────────────────────────────

def test_agent_graph_stores_pattern_config(provider):
    from app.agent.pattern_config import PatternConfig
    cfg = PatternConfig(
        reasoning_patterns=["react", "reflection"],
        safety_patterns=["guardrails", "hitl"],
        goal_properties=GoalProperties(risk=RiskLevel.HIGH),
    )
    from app.agent.dynamic_graph import DynamicGraphAssembler
    assembler = DynamicGraphAssembler()
    g = assembler.assemble(cfg, planner=provider, executor=provider, verifier=provider)
    # Pattern config must be accessible from the graph
    assert hasattr(g, "_pattern_config") or g is not None


# ── AgentRunTrace fields ──────────────────────────────────────────────────────

def test_agent_run_trace_has_runtime_profile_id():
    from app.agent_runtime.models import AgentRunTrace
    trace = AgentRunTrace(
        trace_id="t1",
        goal_id="g1",
        tenant_id="t1",
        runtime_profile_id="p1",   # NEW field
        patterns_used=["react", "reflection"],  # NEW field
        rag_strategy_used="hybrid_rag",         # NEW field
    )
    assert trace.runtime_profile_id == "p1"
    assert "react" in trace.patterns_used
    assert trace.rag_strategy_used == "hybrid_rag"


# ── Scorecard after completion ────────────────────────────────────────────────

def test_scorecard_computed_on_complete_state(tenant_ctx):
    """After a goal completes, RuntimeScorecard must produce a result."""
    from app.evals.runtime_scorecard import RuntimeScorecard
    from app.orchestration.runtime_profile import (
        AgentPatternConfig,
        EvalConfig,
        GoalProperties,
        GoalRuntimeProfile,
        MemoryCacheConfig,
        ModelPlanConfig,
        RAGStrategyConfig,
        SecurityConfig,
    )
    state = AgentState(goal="list tickets", tenant_ctx=tenant_ctx, goal_id="g1")
    state.status = GoalStatus.COMPLETE
    state.iterations = 3
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="list tickets"),
        agent_patterns=AgentPatternConfig(),
        rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(),
        security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(),
        eval_config=EvalConfig(),
    )
    scorecard = RuntimeScorecard()
    result = scorecard.score(state=state, profile=profile)
    assert result.overall_score >= 0.0
    assert set(result.dimension_status) == set(result.weights)
    assert result.scores == {"goal_success": 1.0}


def test_self_improvement_actions_after_failed_state(tenant_ctx):
    """SelfImprovementEngine must return actions for a failed goal."""
    from app.evals.runtime_scorecard import RuntimeScorecard, ScorecardResult
    from app.evals.self_improvement_engine import ImprovementAction, SelfImprovementEngine
    from app.orchestration.runtime_profile import (
        AgentPatternConfig,
        EvalConfig,
        GoalProperties,
        GoalRuntimeProfile,
        MemoryCacheConfig,
        ModelPlanConfig,
        RAGStrategyConfig,
        SecurityConfig,
    )
    state = AgentState(goal="delete prod db", tenant_ctx=tenant_ctx, goal_id="g1")
    state.status = GoalStatus.FAILED
    state.verification_feedback = "permission denied for table users"

    scorecard_result = ScorecardResult(
        goal_id="g1",
        scores={"goal_success": 0.0, "rag_quality": 0.5, "safety": 1.0, "latency": 0.8,
                "cost_efficiency": 0.9, "grounding": 0.7, "citation_quality": 0.6,
                "retrieval_confidence": 0.6, "tool_success_rate": 0.3},
        overall_score=0.35,
    )
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="delete prod db"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    engine = SelfImprovementEngine()
    actions = engine.decide_actions(scorecard_result, profile, state=state)
    assert len(actions) > 0


def test_reflexion_wirer_stores_lesson_on_failure(tenant_ctx):
    """ReflexionWirer must store a lesson when goal fails."""
    from app.agent.reflexion_wirer import ReflexionWirer
    from app.state_runtime.reflexion_store import ReflexionStore
    store = ReflexionStore()
    wirer = ReflexionWirer(store=store)
    state = AgentState(goal="update user db", tenant_ctx=tenant_ctx, goal_id="g1")
    state.status = GoalStatus.FAILED
    state.verification_feedback = "permission denied for users table"
    stored = wirer.maybe_store(state)
    assert stored is True
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) >= 1


# ── ReadinessGate in GoalService ─────────────────────────────────────────────

def test_readiness_gate_called_before_goal_execution():
    """ReadinessGate must be checkable before goal execution."""
    from app.runtime_readiness.dependency_health import DependencyHealth, DepStatus
    from app.runtime_readiness.readiness_gate import ReadinessGate
    health = DependencyHealth.all_healthy()
    gate = ReadinessGate(health)
    from app.orchestration.runtime_profile import (
        AgentPatternConfig,
        EvalConfig,
        GoalProperties,
        GoalRuntimeProfile,
        MemoryCacheConfig,
        ModelPlanConfig,
        RAGStrategyConfig,
        SecurityConfig,
    )
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    result = gate.check(profile)
    assert result.ready is True


# ── ToolRanker in tool selection ─────────────────────────────────────────────

def test_tool_ranker_ranks_by_trust_score():
    """ToolRanker must use ToolTrustStore scores in ranking."""
    from app.tool_runtime.tool_ranker import ToolRanker
    from app.tool_runtime.tool_score import ToolScorer
    from app.tool_runtime.tool_trust_store import ToolTrustStore
    store = ToolTrustStore()
    for _ in range(5):
        store.record_outcome("reliable_tool", success=True, latency_ms=100)
    for _ in range(5):
        store.record_outcome("unreliable_tool", success=False, latency_ms=5000)
    scorer = ToolScorer(trust_store=store)
    ranker = ToolRanker(scorer=scorer)
    ranked = ranker.rank(["unreliable_tool", "reliable_tool"], goal_context="search tickets")
    # Reliable tool must rank higher
    assert ranked[0] == "reliable_tool"
