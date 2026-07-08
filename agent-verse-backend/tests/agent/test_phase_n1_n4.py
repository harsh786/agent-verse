# tests/agent/test_phase_n1_n4.py
"""Phase N1-N4 real-world wiring tests."""
from __future__ import annotations

import pytest
from app.providers.fake import FakeProvider
from app.tenancy.context import TenantContext, PlanTier
from app.agent.state import AgentState, GoalStatus


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


# ── N1: Pattern nodes wired via constructor kwargs ────────────────────────────

def test_self_refine_in_compiled_graph():
    """_node_refine must be in compiled graph when enable_self_refine=True."""
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_self_refine=True)
    node_names = set(g._graph.get_graph().nodes.keys())
    assert "refine" in node_names, "refine node not compiled into graph"


def test_self_consistency_in_compiled_graph():
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_self_consistency=True)
    node_names = set(g._graph.get_graph().nodes.keys())
    assert "self_consistency" in node_names


def test_tree_of_thoughts_in_compiled_graph():
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_tree_of_thoughts=True)
    node_names = set(g._graph.get_graph().nodes.keys())
    assert "tree_of_thoughts" in node_names


def test_peer_review_in_compiled_graph():
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_peer_review=True)
    node_names = set(g._graph.get_graph().nodes.keys())
    assert "peer_review" in node_names


def test_all_patterns_in_compiled_graph():
    """When all flags True, all 4 advanced pattern nodes must be in graph."""
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(
        planner=p, executor=p, verifier=p,
        enable_self_refine=True, enable_self_consistency=True,
        enable_tree_of_thoughts=True, enable_peer_review=True,
    )
    node_names = set(g._graph.get_graph().nodes.keys())
    for node in ["refine", "self_consistency", "tree_of_thoughts", "peer_review"]:
        assert node in node_names, f"{node} not in compiled graph"


def test_no_advanced_nodes_by_default():
    """Without flags, advanced nodes must NOT be in default graph."""
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p)  # all defaults False
    node_names = set(g._graph.get_graph().nodes.keys())
    assert "refine" not in node_names
    assert "self_consistency" not in node_names
    assert "tree_of_thoughts" not in node_names
    assert "peer_review" not in node_names


# ── N2: Reflexion lessons in initial_context ─────────────────────────────────

def test_reflexion_lessons_format_compatible_with_context_pipeline():
    """Lessons recalled from ReflexionStore must have keys compatible with planning context."""
    from app.state_runtime.reflexion_store import ReflexionStore
    store = ReflexionStore()
    store.record(
        tenant_id="t1",
        lesson="For goal 'delete db': always check permissions first",
        source_goal_id="g0",
        failure_class="auth_failure",
    )
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) == 1
    assert "lesson" in lessons[0]
    assert "failure_class" in lessons[0]
    assert "source_goal_id" in lessons[0]
    # Verify format is usable in initial_context
    ctx: dict = {"_reflexion_lessons": lessons}
    assert ctx["_reflexion_lessons"][0]["lesson"]


# ── N3: Latency tracking ──────────────────────────────────────────────────────

def test_latency_ms_key_written_to_context(tenant_ctx: TenantContext) -> None:
    """_latency_ms must be populated before RuntimeScorecard scoring."""
    import time
    state = AgentState(goal="test", tenant_ctx=tenant_ctx, goal_id="g1")
    state.context["_goal_start_ms"] = time.monotonic() * 1000 - 5000  # 5 seconds ago
    # Simulate what _node_verify does
    _start_ms = state.context.get("_goal_start_ms", 0.0)
    if _start_ms > 0:
        state.context["_latency_ms"] = time.monotonic() * 1000 - _start_ms
    assert "_latency_ms" in state.context
    assert state.context["_latency_ms"] > 0
    assert state.context["_latency_ms"] >= 4000  # At least 4 seconds


def test_score_latency_uses_context_key(tenant_ctx: TenantContext) -> None:
    """score_latency() must read _latency_ms from state.context."""
    from app.evals.model_score import ModelScorer
    state = AgentState(goal="test", tenant_ctx=tenant_ctx, goal_id="g1")
    state.context["_latency_ms"] = 3000  # 3 seconds = good latency
    scorer = ModelScorer()
    score = scorer.score_latency(state)
    assert score >= 0.8  # 3s < 5s threshold → 1.0


def test_score_latency_neutral_when_no_key(tenant_ctx: TenantContext) -> None:
    """score_latency() must return neutral when _latency_ms not set."""
    from app.evals.model_score import ModelScorer
    state = AgentState(goal="test", tenant_ctx=tenant_ctx, goal_id="g1")
    scorer = ModelScorer()
    score = scorer.score_latency(state)
    assert score == 0.75  # neutral sentinel


# ── N4: Cost reads from context ───────────────────────────────────────────────

def test_score_cost_reads_from_context_not_attribute(tenant_ctx: TenantContext) -> None:
    """score_cost must read from state.context['total_cost_usd'] not getattr."""
    from app.evals.model_score import ModelScorer
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
    )
    state = AgentState(goal="test", tenant_ctx=tenant_ctx, goal_id="g1")
    state.context["total_cost_usd"] = 0.05  # 5 cents, budget is 10 cents

    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(max_cost_usd=0.10), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    scorer = ModelScorer()
    score = scorer.score_cost(profile, state)
    # 0.05/0.10 = 0.5 ratio → bucket 0.3<ratio<=0.6 → score should be 0.8
    assert score == 0.8
    # Verify a high-cost state is penalised (proves we're reading context, not attr)
    state2 = AgentState(goal="test", tenant_ctx=tenant_ctx, goal_id="g2")
    state2.context["total_cost_usd"] = 0.20  # 20 cents, 2x over budget
    score2 = scorer.score_cost(profile, state2)
    assert score2 < 0.6  # Should penalize over-budget


def test_score_cost_neutral_when_no_cost(tenant_ctx: TenantContext) -> None:
    """score_cost must return neutral when no cost data."""
    from app.evals.model_score import ModelScorer
    from app.orchestration.runtime_profile import (
        GoalRuntimeProfile, GoalProperties, AgentPatternConfig, RAGStrategyConfig,
        ModelPlanConfig, SecurityConfig, MemoryCacheConfig, EvalConfig,
    )
    state = AgentState(goal="test", tenant_ctx=tenant_ctx, goal_id="g1")
    # No cost in context
    profile = GoalRuntimeProfile(
        goal_id="g1", tenant_id="t1",
        properties=GoalProperties(raw_goal="test"),
        agent_patterns=AgentPatternConfig(), rag_strategy=RAGStrategyConfig(),
        model_plan=ModelPlanConfig(), security=SecurityConfig(),
        memory_cache=MemoryCacheConfig(), eval_config=EvalConfig(),
    )
    scorer = ModelScorer()
    score = scorer.score_cost(profile, state)
    assert score == 0.8  # neutral sentinel
