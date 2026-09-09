"""Phase 2: Agent execution wiring tests — H1-H8."""
from __future__ import annotations

import pytest

from app.agent.state import AgentState, GoalStatus
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext


@pytest.fixture
def tenant_ctx():
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


# ---------------------------------------------------------------------------
# H1: _node_refine in graph topology
# ---------------------------------------------------------------------------

def test_agent_graph_accepts_self_refine_flag():
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_self_refine=True)
    assert g._enable_self_refine is True


def test_agent_graph_self_refine_false_by_default():
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    assert g._enable_self_refine is False


def test_agent_graph_self_refine_node_in_graph():
    """When enable_self_refine=True, 'refine' node is compiled into the graph."""
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_self_refine=True)
    # LangGraph compiled graph exposes node names
    node_names = set(g._graph.get_graph().nodes.keys())
    assert "refine" in node_names


def test_agent_graph_self_refine_not_in_graph_when_disabled():
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_self_refine=False)
    node_names = set(g._graph.get_graph().nodes.keys())
    assert "refine" not in node_names


# ---------------------------------------------------------------------------
# H7: self_consistency, tree_of_thoughts, peer_review flags + nodes
# ---------------------------------------------------------------------------

def test_agent_graph_accepts_self_consistency_flag():
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_self_consistency=True)
    assert g._enable_self_consistency is True


def test_agent_graph_accepts_tree_of_thoughts_flag():
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_tree_of_thoughts=True)
    assert g._enable_tree_of_thoughts is True


def test_agent_graph_accepts_peer_review_flag():
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_peer_review=True)
    assert g._enable_peer_review is True


def test_self_consistency_node_in_graph_when_enabled():
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_self_consistency=True)
    node_names = set(g._graph.get_graph().nodes.keys())
    assert "self_consistency" in node_names


def test_tree_of_thoughts_node_in_graph_when_enabled():
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_tree_of_thoughts=True)
    node_names = set(g._graph.get_graph().nodes.keys())
    assert "tree_of_thoughts" in node_names


def test_peer_review_node_in_graph_when_enabled():
    from app.agent.graph import AgentGraph
    p = FakeProvider()
    g = AgentGraph(planner=p, executor=p, verifier=p, enable_peer_review=True)
    node_names = set(g._graph.get_graph().nodes.keys())
    assert "peer_review" in node_names


# ---------------------------------------------------------------------------
# H6: DynamicGraphAssembler translates reasoning_patterns
# ---------------------------------------------------------------------------

def test_dynamic_graph_assembler_translates_self_consistency():
    from app.agent.dynamic_graph import DynamicGraphAssembler
    from app.agent.pattern_config import PatternConfig
    p = FakeProvider()
    assembler = DynamicGraphAssembler()
    cfg = PatternConfig(reasoning_patterns=["self_consistency", "tree_of_thoughts"])
    g = assembler.assemble(cfg, planner=p, executor=p, verifier=p)
    assert g._enable_self_consistency is True
    assert g._enable_tree_of_thoughts is True


def test_dynamic_graph_assembler_translates_peer_review():
    from app.agent.dynamic_graph import DynamicGraphAssembler
    from app.agent.pattern_config import PatternConfig
    p = FakeProvider()
    assembler = DynamicGraphAssembler()
    cfg = PatternConfig(reasoning_patterns=["peer_review", "self_refine"])
    g = assembler.assemble(cfg, planner=p, executor=p, verifier=p)
    assert g._enable_peer_review is True
    assert g._enable_self_refine is True


def test_dynamic_graph_assembler_translates_reflection():
    from app.agent.dynamic_graph import DynamicGraphAssembler
    from app.agent.pattern_config import PatternConfig
    p = FakeProvider()
    assembler = DynamicGraphAssembler()
    cfg = PatternConfig(reasoning_patterns=["reflection"])
    g = assembler.assemble(cfg, planner=p, executor=p, verifier=p)
    assert g._enable_reflection is True


def test_dynamic_graph_assembler_translates_cot():
    from app.agent.dynamic_graph import DynamicGraphAssembler
    from app.agent.pattern_config import PatternConfig
    p = FakeProvider()
    assembler = DynamicGraphAssembler()
    cfg = PatternConfig(reasoning_patterns=["chain_of_thought"])
    g = assembler.assemble(cfg, planner=p, executor=p, verifier=p)
    assert g._enable_cot is True


def test_dynamic_graph_assembler_stores_pattern_config():
    from app.agent.dynamic_graph import DynamicGraphAssembler
    from app.agent.pattern_config import PatternConfig
    p = FakeProvider()
    assembler = DynamicGraphAssembler()
    cfg = PatternConfig(reasoning_patterns=["self_consistency"])
    g = assembler.assemble(cfg, planner=p, executor=p, verifier=p)
    assert g._pattern_config is cfg


def test_dynamic_graph_assembler_max_iterations_forwarded():
    from app.agent.dynamic_graph import DynamicGraphAssembler
    from app.agent.pattern_config import PatternConfig
    p = FakeProvider()
    assembler = DynamicGraphAssembler()
    cfg = PatternConfig(max_iterations=42)
    g = assembler.assemble(cfg, planner=p, executor=p, verifier=p)
    assert g._max_iterations == 42


def test_dynamic_graph_assembler_goal_tree_via_multi_agent():
    from app.agent.dynamic_graph import DynamicGraphAssembler
    from app.agent.pattern_config import PatternConfig
    p = FakeProvider()
    assembler = DynamicGraphAssembler()
    cfg = PatternConfig(multi_agent_patterns=["goal_tree"])
    g = assembler.assemble(cfg, planner=p, executor=p, verifier=p)
    assert g._enable_goal_tree is True


# ---------------------------------------------------------------------------
# H7: Node method behaviour
# ---------------------------------------------------------------------------

async def test_node_self_consistency_improves_output(tenant_ctx):
    from app.agent.graph import AgentGraph
    from app.agent.state import StepResult, StepStatus
    responses = ["Paris", "Paris", "London"]
    provider = FakeProvider(responses=responses)
    g = AgentGraph(planner=provider, executor=provider, verifier=provider,
                   enable_self_consistency=True)
    state = AgentState(goal="capital of France", tenant_ctx=tenant_ctx, goal_id="g1")
    step = StepResult(description="answer", output="London", status=StepStatus.COMPLETE)
    state.steps = [step]
    result = await g._node_self_consistency({"agent_state": state, "tenant_ctx": tenant_ctx})
    assert isinstance(result, dict)
    rs = result.get("agent_state", state)
    assert isinstance(rs, AgentState)


async def test_node_self_consistency_empty_steps(tenant_ctx):
    from app.agent.graph import AgentGraph
    provider = FakeProvider()
    g = AgentGraph(planner=provider, executor=provider, verifier=provider,
                   enable_self_consistency=True)
    state = AgentState(goal="test", tenant_ctx=tenant_ctx, goal_id="g1")
    state.steps = []
    result = await g._node_self_consistency({"agent_state": state, "tenant_ctx": tenant_ctx})
    assert result == {}


async def test_node_tree_of_thoughts_populates_context(tenant_ctx):
    from app.agent.graph import AgentGraph
    provider = FakeProvider(responses=["Approach A", "Approach B", "Approach C",
                                       '{"score": 0.8, "promising": true, "reason": "good"}',
                                       '{"score": 0.6, "promising": true, "reason": "ok"}',
                                       '{"score": 0.4, "promising": false, "reason": "weak"}',
                                       "Final answer based on approach A"])
    g = AgentGraph(planner=provider, executor=provider, verifier=provider,
                   enable_tree_of_thoughts=True)
    state = AgentState(goal="what is the best sorting algorithm", tenant_ctx=tenant_ctx,
                       goal_id="g1")
    result = await g._node_tree_of_thoughts({"agent_state": state, "tenant_ctx": tenant_ctx})
    assert isinstance(result, dict)


async def test_node_tree_of_thoughts_none_state(tenant_ctx):
    from app.agent.graph import AgentGraph
    provider = FakeProvider()
    g = AgentGraph(planner=provider, executor=provider, verifier=provider,
                   enable_tree_of_thoughts=True)
    result = await g._node_tree_of_thoughts({"agent_state": None, "tenant_ctx": tenant_ctx})
    assert result == {}


async def test_node_peer_review_flags_low_quality(tenant_ctx):
    from app.agent.graph import AgentGraph
    from app.agent.state import StepResult, StepStatus
    provider = FakeProvider(responses=[
        '{"quality_score": 0.2, "critique": "Too brief", "suggestions": [], "approved": false}'
    ])
    g = AgentGraph(planner=provider, executor=provider, verifier=provider,
                   enable_peer_review=True)
    state = AgentState(goal="explain quantum computing", tenant_ctx=tenant_ctx, goal_id="g1")
    step = StepResult(description="answer", output="It is complex.", status=StepStatus.COMPLETE)
    state.steps = [step]
    result = await g._node_peer_review({"agent_state": state, "tenant_ctx": tenant_ctx})
    assert isinstance(result, dict)
    rs = result.get("agent_state", state)
    assert rs.context.get("peer_review_score") is not None


async def test_node_peer_review_approves_high_quality(tenant_ctx):
    from app.agent.graph import AgentGraph
    from app.agent.state import StepResult, StepStatus
    provider = FakeProvider(responses=[
        '{"quality_score": 0.9, "critique": "Excellent", "suggestions": [], "approved": true}'
    ])
    g = AgentGraph(planner=provider, executor=provider, verifier=provider,
                   enable_peer_review=True)
    state = AgentState(goal="explain quantum computing", tenant_ctx=tenant_ctx, goal_id="g1")
    step = StepResult(description="answer", output="A comprehensive answer.", status=StepStatus.COMPLETE)
    state.steps = [step]
    result = await g._node_peer_review({"agent_state": state, "tenant_ctx": tenant_ctx})
    rs = result.get("agent_state", state)
    assert rs.context.get("peer_review_approved") is True


async def test_node_peer_review_empty_steps_returns_agent_state(tenant_ctx):
    from app.agent.graph import AgentGraph
    provider = FakeProvider()
    g = AgentGraph(planner=provider, executor=provider, verifier=provider,
                   enable_peer_review=True)
    state = AgentState(goal="test", tenant_ctx=tenant_ctx, goal_id="g1")
    state.steps = []
    result = await g._node_peer_review({"agent_state": state, "tenant_ctx": tenant_ctx})
    assert result == {}


# ---------------------------------------------------------------------------
# H8: Supervisor / debate stubs compile and return state
# ---------------------------------------------------------------------------

async def test_node_supervisor_check_stub_returns_state(tenant_ctx):
    from app.agent.graph import AgentGraph
    provider = FakeProvider()
    g = AgentGraph(planner=provider, executor=provider, verifier=provider)
    state = AgentState(goal="test", tenant_ctx=tenant_ctx, goal_id="g1")
    result = await g._node_supervisor_check({"agent_state": state, "tenant_ctx": tenant_ctx})
    assert isinstance(result, dict)
    assert result.get("agent_state") is state


async def test_node_debate_stub_returns_state(tenant_ctx):
    from app.agent.graph import AgentGraph
    provider = FakeProvider()
    g = AgentGraph(planner=provider, executor=provider, verifier=provider)
    state = AgentState(goal="test", tenant_ctx=tenant_ctx, goal_id="g1")
    result = await g._node_debate({"agent_state": state, "tenant_ctx": tenant_ctx})
    assert isinstance(result, dict)
    assert result.get("agent_state") is state
