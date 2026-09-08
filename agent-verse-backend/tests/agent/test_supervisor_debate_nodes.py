"""Coverage-Matrix row 1 (D-1/D-2): supervisor & debate patterns on the live graph.

These patterns were log-only stubs gated by ctor flags that were never assigned,
so the in-graph nodes were never added and never did real work. These tests pin:

  (a) the enable flag adds the node to the compiled graph and its body invokes the
      REAL pattern (SupervisorAgent.run / DebateOrchestrator.run), not the stub;
  (b) with the flag unset (default), the node is absent;
  (c) an error inside the pattern does not crash the graph node;
  (d) a decision record is emitted when a pattern node runs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.debate import DebateResult
from app.agent.graph import AgentGraph, GraphState
from app.agent.state import AgentState
from app.agent.supervisor import SupervisionResult
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="sd-t1", plan=PlanTier.ENTERPRISE, api_key_id="sd1")


def _graph(**kwargs: object) -> AgentGraph:
    p = FakeProvider()
    return AgentGraph(planner=p, executor=p, verifier=p, **kwargs)


def _state(graph: AgentGraph) -> GraphState:
    agent_state = AgentState(goal="Solve a complex multi-part goal", tenant_ctx=T)
    graph._tenant_ctx_ref = T
    return {"goal": agent_state.goal, "tenant_ctx": T, "iteration": 0, "agent_state": agent_state}


# ── (b) default OFF ───────────────────────────────────────────────────────────


def test_supervisor_node_absent_by_default() -> None:
    g = _graph()
    assert "supervisor" not in set(g._graph.get_graph().nodes.keys())


def test_debate_node_absent_by_default() -> None:
    g = _graph()
    assert "debate" not in set(g._graph.get_graph().nodes.keys())


# ── (a) enable flag adds node ─────────────────────────────────────────────────


def test_supervisor_node_added_when_enabled() -> None:
    g = _graph(enable_supervisor=True)
    assert "supervisor" in set(g._graph.get_graph().nodes.keys())


def test_debate_node_added_when_enabled() -> None:
    g = _graph(enable_debate=True)
    assert "debate" in set(g._graph.get_graph().nodes.keys())


def test_supervisor_enabled_via_runtime_profile_strategy() -> None:
    profile = MagicMock()
    profile.primary_strategy.strategy_id = "supervisor"
    profile.auxiliary_strategies = []
    g = _graph(runtime_profile=profile)
    assert "supervisor" in set(g._graph.get_graph().nodes.keys())


def test_debate_enabled_via_runtime_profile_strategy() -> None:
    profile = MagicMock()
    profile.primary_strategy.strategy_id = "debate"
    profile.auxiliary_strategies = []
    g = _graph(runtime_profile=profile)
    assert "debate" in set(g._graph.get_graph().nodes.keys())


# ── (a) node body invokes the REAL pattern (not the stub) ─────────────────────


async def test_supervisor_node_invokes_real_supervisor_agent() -> None:
    g = _graph(enable_supervisor=True)
    g._goal_service = MagicMock()
    state = _state(g)
    fake_result = SupervisionResult(
        success=True, tasks=[], synthesized_result="synthesized supervisor answer"
    )
    with patch(
        "app.agent.supervisor.SupervisorAgent.run",
        new=AsyncMock(return_value=fake_result),
    ) as mock_run:
        out = await g._node_supervisor_check(state)
    mock_run.assert_awaited_once()
    agent_state = out["agent_state"]
    # Real output folded into state, and a decision record exists.
    assert "synthesized supervisor answer" in agent_state.context.get("supervisor_result", "")
    decisions = agent_state.context.get("pattern_decisions", [])
    assert any(d.get("pattern") == "supervisor" for d in decisions)


async def test_debate_node_invokes_real_debate_orchestrator() -> None:
    g = _graph(enable_debate=True)
    state = _state(g)
    fake_result = DebateResult(
        winning_proposal="the winning approach",
        winning_agent="agent_2",
        all_proposals=[],
        consensus_level=0.66,
    )
    with patch(
        "app.agent.debate.DebateOrchestrator.run",
        new=AsyncMock(return_value=fake_result),
    ) as mock_run:
        out = await g._node_debate(state)
    mock_run.assert_awaited_once()
    agent_state = out["agent_state"]
    assert "the winning approach" in agent_state.context.get("debate_result", "")
    decisions = agent_state.context.get("pattern_decisions", [])
    assert any(d.get("pattern") == "debate" for d in decisions)


# ── (c) errors don't crash the graph node ─────────────────────────────────────


async def test_supervisor_node_error_does_not_crash() -> None:
    g = _graph(enable_supervisor=True)
    g._goal_service = MagicMock()
    state = _state(g)
    with patch(
        "app.agent.supervisor.SupervisorAgent.run",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        out = await g._node_supervisor_check(state)
    # Node must return state unchanged-ish, never raise.
    assert out["agent_state"] is state["agent_state"]


async def test_debate_node_error_does_not_crash() -> None:
    g = _graph(enable_debate=True)
    state = _state(g)
    with patch(
        "app.agent.debate.DebateOrchestrator.run",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        out = await g._node_debate(state)
    assert out["agent_state"] is state["agent_state"]


async def test_supervisor_node_skips_without_goal_service() -> None:
    """No goal_service wired → supervisor cannot recurse; node no-ops gracefully."""
    g = _graph(enable_supervisor=True)
    g._goal_service = None
    state = _state(g)
    with patch("app.agent.supervisor.SupervisorAgent.run", new=AsyncMock()) as mock_run:
        out = await g._node_supervisor_check(state)
    mock_run.assert_not_awaited()
    assert out["agent_state"] is state["agent_state"]


async def test_supervisor_node_runs_once_per_goal() -> None:
    """Guard against re-running the heavy supervisor pattern on replan loops."""
    g = _graph(enable_supervisor=True)
    g._goal_service = MagicMock()
    state = _state(g)
    fake_result = SupervisionResult(success=True, tasks=[], synthesized_result="answer")
    with patch(
        "app.agent.supervisor.SupervisorAgent.run",
        new=AsyncMock(return_value=fake_result),
    ) as mock_run:
        await g._node_supervisor_check(state)
        await g._node_supervisor_check(state)
    assert mock_run.await_count == 1
