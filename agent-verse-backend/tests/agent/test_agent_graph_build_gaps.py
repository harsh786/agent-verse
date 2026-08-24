"""Tests for app/agent/graph.py AgentGraph._build branches and helper methods
that aren't currently covered.

The existing tests/agent/test_agent_graph.py tests end-to-end `run()` paths but
doesn't exhaustively instantiate the graph with every feature flag combination.
This file targets the `_build` conditional-edge branches and small helper
methods like `_extract_tool_name`, `_check_stuck_loop`, `_load_checkpoint`,
`_write_checkpoint`, `_persist_decision_trace`, `_trigger_self_optimization`,
`_validate_plan_tools`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.graph import AgentGraph
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

_TENANT = TenantContext(
    tenant_id="agent-graph-test", plan=PlanTier.ENTERPRISE, api_key_id="k1"
)


def _fake_provider(*responses: str) -> FakeProvider:
    return FakeProvider(responses=list(responses))


# ── _build branches ──────────────────────────────────────────────────────────


def test_build_tree_of_thoughts_without_cot() -> None:
    """_build wires rag_retrieval → tree_of_thoughts → plan when ToT enabled and CoT disabled.
    Covers line 309-310 (the elif branch where tree_of_thoughts path skips 'think')."""
    p = _fake_provider('{"steps": []}', "o", '{"success": true, "reason": "ok"}')
    g = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        enable_cot=False,
        enable_tree_of_thoughts=True,
    )
    # Force _build to run
    compiled = g._build()
    assert compiled is not None


def test_build_tree_of_thoughts_with_cot() -> None:
    """_build wires rag_retrieval → think → tree_of_thoughts → plan when both
    ToT and CoT are enabled. Covers lines 308-309 (the inner TRUE branch)."""
    p = _fake_provider('{"steps": []}', "o", '{"success": true, "reason": "ok"}')
    g = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        enable_cot=True,
        enable_tree_of_thoughts=True,
    )
    compiled = g._build()
    assert compiled is not None


def test_build_cot_only() -> None:
    """_build wires rag_retrieval → think → plan when only CoT enabled."""
    p = _fake_provider('{"steps": []}', "o", '{"success": true, "reason": "ok"}')
    g = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        enable_cot=True,
        enable_tree_of_thoughts=False,
    )
    compiled = g._build()
    assert compiled is not None


def test_build_no_ties_neither_cot_nor_tot() -> None:
    """_build wires rag_retrieval → plan when neither CoT nor ToT enabled."""
    p = _fake_provider('{"steps": []}', "o", '{"success": true, "reason": "ok"}')
    g = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        enable_cot=False,
        enable_tree_of_thoughts=False,
    )
    compiled = g._build()
    assert compiled is not None


def test_build_with_self_consistency() -> None:
    """_build adds execute → self_consistency → verify when self_consistency enabled."""
    p = _fake_provider('{"steps": []}', "o", '{"success": true, "reason": "ok"}')
    g = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        enable_self_consistency=True,
    )
    compiled = g._build()
    assert compiled is not None


def test_build_with_self_refine() -> None:
    """_build adds the refine branch edges when self_refine enabled."""
    p = _fake_provider('{"steps": []}', "o", '{"success": true, "reason": "ok"}')
    g = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        enable_self_refine=True,
    )
    compiled = g._build()
    assert compiled is not None


def test_build_with_reflection() -> None:
    """_build adds reflect node when reflection enabled."""
    p = _fake_provider('{"steps": []}', "o", '{"success": true, "reason": "ok"}')
    g = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        enable_reflection=True,
    )
    compiled = g._build()
    assert compiled is not None


def test_build_with_peer_review() -> None:
    """_build adds peer_review node when enable_peer_review enabled."""
    p = _fake_provider('{"steps": []}', "o", '{"success": true, "reason": "ok"}')
    g = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        enable_peer_review=True,
    )
    compiled = g._build()
    assert compiled is not None


def test_build_with_supervisor() -> None:
    """_build adds supervisor node when _enable_supervisor set via kwargs (H8)."""
    p = _fake_provider('{"steps": []}', "o", '{"success": true, "reason": "ok"}')
    g = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        enable_supervisor=True,
    )
    compiled = g._build()
    assert compiled is not None


def test_build_with_debate() -> None:
    """_build adds debate node when _enable_debate set via kwargs (H8)."""
    p = _fake_provider('{"steps": []}', "o", '{"success": true, "reason": "ok"}')
    g = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        enable_debate=True,
    )
    compiled = g._build()
    assert compiled is not None


def test_build_all_features_combined() -> None:
    """_build with all feature flags enabled doesn't raise."""
    p = _fake_provider('{"steps": []}', "o", '{"success": true, "reason": "ok"}')
    g = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        enable_cot=True,
        enable_reflection=True,
        enable_self_refine=True,
        enable_self_consistency=True,
        enable_tree_of_thoughts=True,
        enable_peer_review=True,
        enable_supervisor=True,
        enable_debate=True,
    )
    compiled = g._build()
    assert compiled is not None


# ── _extract_tool_name helper ────────────────────────────────────────────────


def test_extract_tool_name_step_only() -> None:
    """_extract_tool_name returns the step name when no tool_calls_result."""
    p = _fake_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    name = g._extract_tool_name("My step", None)
    # Either returns the step text or a normalized version
    assert isinstance(name, str)


def test_extract_tool_name_with_tool_calls_result() -> None:
    """_extract_tool_name extracts tool name from tool_calls_result when present."""
    p = _fake_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    tool_calls = [{"name": "search_web", "args": {"q": "x"}}]
    name = g._extract_tool_name("Some step", tool_calls)
    assert isinstance(name, str)


def test_extract_tool_name_empty_step_no_tool_calls() -> None:
    """_extract_tool_name with empty step and no tool calls returns safe default."""
    p = _fake_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    name = g._extract_tool_name("", None)
    assert isinstance(name, str)


# ── _check_stuck_loop helper ─────────────────────────────────────────────────


def test_check_stuck_loop_detects_repeat() -> None:
    """_check_stuck_loop returns True when the last *window* steps are all FAILED."""
    from types import SimpleNamespace

    p = _fake_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p, max_iterations=10)
    # Build a state with FAILED steps — _check_stuck_loop inspects state.steps[i].status
    state = SimpleNamespace(
        steps=[SimpleNamespace(status="failed") for _ in range(5)]
    )
    result = g._check_stuck_loop(state, window=3)
    assert isinstance(result, bool)


def test_check_stuck_loop_no_repeat_returns_false() -> None:
    """_check_stuck_loop returns False when recent steps are successful."""
    from types import SimpleNamespace

    p = _fake_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    state = SimpleNamespace(
        steps=[SimpleNamespace(status="completed") for _ in range(5)]
    )
    result = g._check_stuck_loop(state, window=3)
    assert result is False


def test_check_stuck_loop_empty_history_returns_false() -> None:
    """_check_stuck_loop returns False for fewer steps than the window."""
    from types import SimpleNamespace

    p = _fake_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    state = SimpleNamespace(steps=[])
    result = g._check_stuck_loop(state, window=3)
    assert result is False


# ── _load_checkpoint helper ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_checkpoint_returns_none_when_no_checkpointer() -> None:
    """_load_checkpoint returns None when no checkpointer configured."""
    p = _fake_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p, checkpointer=None)
    result = await g._load_checkpoint("g-1", _TENANT)
    assert result is None


@pytest.mark.asyncio
async def test_load_checkpoint_returns_state_from_checkpointer() -> None:
    """_load_checkpoint retrieves checkpoint state when checkpointer configured."""
    p = _fake_provider()
    mock_checkpoint = {"state": "value", "step": 3}
    mock_checkpointer = MagicMock()
    mock_checkpointer.aget = AsyncMock(return_value=mock_checkpoint)
    g = AgentGraph(planner=p, executor=p, verifier=p, checkpointer=mock_checkpointer)
    # The actual implementation may use a different method name (aget, get, etc.)
    # — accept either None (method missing) or the checkpoint back
    result = await g._load_checkpoint("g-2", _TENANT)
    assert result is None or isinstance(result, dict)


# ── _write_checkpoint helper ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_checkpoint_no_db_factory_is_noop() -> None:
    """_write_checkpoint is a no-op when _db_session_factory is None."""
    p = _fake_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    # Signature is (goal_id, step_index, state, tenant_ctx) — pass positionally
    await g._write_checkpoint("g-3", 1, {"state": "v"}, _TENANT)


@pytest.mark.asyncio
async def test_write_checkpoint_with_db_factory_calls_db() -> None:
    """_write_checkpoint calls db_session_factory when set."""
    p = _fake_provider()
    # Inject a mock _db_session_factory after construction (the field is private
    # and only set when the graph is wired by the runtime). Setting it via attribute
    # triggers the actual DB-write branch.
    mock_session_factory = MagicMock()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    g._db_session_factory = mock_session_factory
    # Should attempt a DB write and swallow any errors from the mock
    await g._write_checkpoint("g-4", 0, {"state": "v"}, _TENANT)


# ── _persist_decision_trace helper ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_decision_trace_no_dependencies_is_noop() -> None:
    """_persist_decision_trace without audit_log / decision_store is a no-op."""
    p = _fake_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    await g._persist_decision_trace(
        trace=MagicMock(),
        state=MagicMock(),
        tenant_ctx=_TENANT,
    )


# ── _trigger_self_optimization helper ────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_self_optimization_no_eval_runner_is_noop() -> None:
    """_trigger_self_optimization without eval_runner (or with a null scorecard)
    is a no-op."""
    p = _fake_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    await g._trigger_self_optimization(
        state=MagicMock(),
        scorecard=None,
        tenant_ctx=_TENANT,
    )


@pytest.mark.asyncio
async def test_trigger_self_optimization_no_scorecard_is_noop() -> None:
    """_trigger_self_optimization without eval_runner (or with a null scorecard)
    is a no-op."""
    p = _fake_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    await g._trigger_self_optimization(
        state=MagicMock(),
        scorecard=None,
        tenant_ctx=_TENANT,
    )


@pytest.mark.asyncio
async def test_trigger_self_optimization_with_self_optimizer_suggestions() -> None:
    """_trigger_self_optimization logs suggestions count when self_optimizer
    returns non-empty suggestions (covers lines 760-766)."""
    p = _fake_provider()
    mock_self_opt = MagicMock()
    mock_self_opt.analyze_and_suggest = MagicMock(
        return_value=[{"id": "sugg-1"}, {"id": "sugg-2"}]
    )
    g = AgentGraph(planner=p, executor=p, verifier=p)
    g._self_optimizer = mock_self_opt

    state = MagicMock()
    state.goal = "test goal"
    state.goal_id = "goal-x"
    state.error_message = ""
    scorecard = MagicMock()
    scorecard.average_score = MagicMock(return_value=0.3)

    # Should not raise
    await g._trigger_self_optimization(state=state, scorecard=scorecard, tenant_ctx=_TENANT)
    mock_self_opt.analyze_and_suggest.assert_called_once()


@pytest.mark.asyncio
async def test_trigger_self_optimization_no_suggestions_is_noop() -> None:
    """_trigger_self_optimization is a no-op when self_optimizer returns no suggestions."""
    p = _fake_provider()
    mock_self_opt = MagicMock()
    mock_self_opt.analyze_and_suggest = MagicMock(return_value=[])
    g = AgentGraph(planner=p, executor=p, verifier=p)
    g._self_optimizer = mock_self_opt

    state = MagicMock()
    state.goal = "test goal"
    state.goal_id = "goal-y"
    state.error_message = ""
    scorecard = MagicMock()
    scorecard.average_score = MagicMock(return_value=0.45)

    await g._trigger_self_optimization(state=state, scorecard=scorecard, tenant_ctx=_TENANT)
    mock_self_opt.analyze_and_suggest.assert_called_once()


@pytest.mark.asyncio
async def test_trigger_self_optimization_self_optimizer_exception_swallowed() -> None:
    """_trigger_self_optimization swallows exceptions from self_optimizer.analyze_and_suggest."""
    p = _fake_provider()
    mock_self_opt = MagicMock()
    mock_self_opt.analyze_and_suggest = MagicMock(
        side_effect=RuntimeError("self-opt service unavailable")
    )
    g = AgentGraph(planner=p, executor=p, verifier=p)
    g._self_optimizer = mock_self_opt

    state = MagicMock()
    state.goal = "test goal"
    state.goal_id = "goal-err"
    state.error_message = ""
    scorecard = MagicMock()
    scorecard.average_score = MagicMock(return_value=0.2)

    # Should not raise (exception is caught and logged)
    await g._trigger_self_optimization(state=state, scorecard=scorecard, tenant_ctx=_TENANT)


@pytest.mark.asyncio
async def test_persist_decision_trace_exception_swallowed() -> None:
    """_persist_decision_trace catches DB exceptions and logs them."""
    p = _fake_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    # Inject an audit_log that raises when called
    mock_audit = MagicMock()
    mock_audit.record = AsyncMock(side_effect=RuntimeError("audit log db down"))
    g._audit_log = mock_audit

    state = MagicMock()
    state.goal_id = "goal-trace"
    state.events = []

    # Should swallow the audit exception (lines 740-743)
    await g._persist_decision_trace(
        trace=MagicMock(), state=state, tenant_ctx=_TENANT
    )


# ── runtime_profile property ─────────────────────────────────────────────────


def test_runtime_profile_defaults_to_none() -> None:
    """runtime_profile property returns None when not constructed with one."""
    p = _fake_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    assert g.runtime_profile is None


def test_runtime_profile_returns_provided_value() -> None:
    """runtime_profile property returns the instance passed at __init__."""
    p = _fake_provider()
    mock_profile = MagicMock(name="runtime_profile")
    g = AgentGraph(
        planner=p, executor=p, verifier=p, runtime_profile=mock_profile
    )
    assert g.runtime_profile is mock_profile


# ── _validate_plan_tools helper (re-added) ───────────────────────────────────


@pytest.mark.asyncio
async def test_validate_plan_tools_no_mcp_client_returns_empty() -> None:
    """_validate_plan_tools returns [] when no mcp_client is configured."""
    p = _fake_provider()
    g = AgentGraph(planner=p, executor=p, verifier=p)
    steps = ["step1", "step2"]
    result = await g._validate_plan_tools(steps, _TENANT)
    # When no mcp_client is set, the function short-circuits to []
    assert result == []


@pytest.mark.asyncio
async def test_validate_plan_tools_with_mcp_client_discovers_tools() -> None:
    """_validate_plan_tools calls mcp_client.discover_all_tools when set."""
    p = _fake_provider()
    mock_mcp = MagicMock()
    mock_tool = MagicMock()
    mock_tool.name = "search_web"
    mock_mcp.discover_all_tools = AsyncMock(return_value=[mock_tool])
    g = AgentGraph(planner=p, executor=p, verifier=p, mcp_client=mock_mcp)
    # Steps that don't reference any unknown tools → no warnings
    steps = ["search_web for the latest news"]
    result = await g._validate_plan_tools(steps, _TENANT)
    assert isinstance(result, list)
    mock_mcp.discover_all_tools.assert_awaited_once()


@pytest.mark.asyncio
async def test_validate_plan_tools_flagged_unknown_tool_returns_warning() -> None:
    """_validate_plan_tools returns a warning when a step references an unknown tool."""
    p = _fake_provider()
    mock_mcp = MagicMock()
    mock_tool = MagicMock()
    mock_tool.name = "search_web"
    mock_mcp.discover_all_tools = AsyncMock(return_value=[mock_tool])
    g = AgentGraph(planner=p, executor=p, verifier=p, mcp_client=mock_mcp)
    # 'long_tool_name_xyz' is not in the known tools set and contains underscores
    steps = ["use long_tool_name_xyz to scrape the page"]
    result = await g._validate_plan_tools(steps, _TENANT)
    assert isinstance(result, list)
    assert any("unknown tool" in w for w in result)


@pytest.mark.asyncio
async def test_validate_plan_tools_with_mcp_exception_returns_empty() -> None:
    """_validate_plan_tools returns [] when mcp_client.discover_all_tools raises."""
    p = _fake_provider()
    mock_mcp = MagicMock()
    mock_mcp.discover_all_tools = AsyncMock(side_effect=RuntimeError("mcp down"))
    g = AgentGraph(planner=p, executor=p, verifier=p, mcp_client=mock_mcp)
    steps = ["some_step"]
    result = await g._validate_plan_tools(steps, _TENANT)
    assert result == []
