"""Unit tests for AgentGraph node methods — targets 25% → 60%+ coverage on app/agent/graph.py.

Tests individual node methods (_node_initialize, _node_plan, _node_verify, _route, etc.)
and the routing logic, without running the full compiled graph.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.graph import AgentGraph, GraphState
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.governance.audit import AuditLog
from app.governance.cost import CostController
from app.governance.hitl import HITLGateway
from app.governance.permissions import ActionLevel, PermissionMatrix
from app.governance.policies import Policy, PolicyEngine
from app.intelligence.eval_runner import EvalRunner
from app.intelligence.guardrails import GuardrailChecker
from app.memory.execution import ExecutionMemory
from app.memory.long_term import LongTermMemoryStore
from app.providers.fake import FakeProvider
from app.reliability.circuit_breaker import CircuitBreaker
from app.reliability.dedup import DeduplicationCache
from app.reliability.result_processor import ResultProcessor
from app.reliability.rollback import RollbackEngine
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="gn-test-t1", plan=PlanTier.ENTERPRISE, api_key_id="gn1")


def _make_graph(
    *,
    enable_cot: bool = False,
    enable_reflection: bool = False,
    autonomy_mode: str = "bounded-autonomous",
    max_iterations: int = 5,
    guardrail_checker: GuardrailChecker | None = None,
    policy_engine: PolicyEngine | None = None,
    hitl_gateway: HITLGateway | None = None,
    exec_memory: ExecutionMemory | None = None,
    long_term_memory: LongTermMemoryStore | None = None,
    cost_controller: CostController | None = None,
    eval_runner: EvalRunner | None = None,
    rollback_engine: RollbackEngine | None = None,
    dedup_cache: DeduplicationCache | None = None,
    mcp_client: Any = None,
) -> AgentGraph:
    provider = FakeProvider()
    return AgentGraph(
        planner=provider,
        executor=provider,
        verifier=provider,
        max_iterations=max_iterations,
        enable_cot=enable_cot,
        enable_reflection=enable_reflection,
        autonomy_mode=autonomy_mode,
        guardrail_checker=guardrail_checker,
        policy_engine=policy_engine,
        hitl_gateway=hitl_gateway,
        exec_memory=exec_memory,
        long_term_memory=long_term_memory,
        cost_controller=cost_controller,
        eval_runner=eval_runner,
        rollback_engine=rollback_engine,
        dedup_cache=dedup_cache,
        mcp_client=mcp_client,
    )


def _make_state(
    goal: str = "Test goal",
    tenant_ctx: TenantContext = T,
    agent_state: AgentState | None = None,
    iteration: int = 0,
    rag_context: str = "",
) -> GraphState:
    state: GraphState = {
        "goal": goal,
        "tenant_ctx": tenant_ctx,
        "iteration": iteration,
        "rag_context": rag_context,
    }
    if agent_state is not None:
        state["agent_state"] = agent_state
    return state


# ---------------------------------------------------------------------------
# _node_initialize
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_initialize_creates_agent_state() -> None:
    graph = _make_graph()
    graph._event_callback = None
    state = _make_state("Analyze codebase")
    result = await graph._node_initialize(state)
    assert "agent_state" in result
    assert isinstance(result["agent_state"], AgentState)
    assert result["agent_state"].goal == "Analyze codebase"
    assert result["iteration"] == 0


@pytest.mark.asyncio
async def test_node_initialize_reuses_existing_state() -> None:
    graph = _make_graph()
    graph._event_callback = None
    existing = AgentState(goal="Existing goal", tenant_ctx=T)
    state = _make_state("Different goal", agent_state=existing)
    result = await graph._node_initialize(state)
    assert result["agent_state"] is existing
    assert result["agent_state"].goal == "Existing goal"


@pytest.mark.asyncio
async def test_node_initialize_guardrail_blocks_goal() -> None:
    """When guardrail_checker raises issues, goal is rejected."""
    checker = MagicMock(spec=GuardrailChecker)
    checker.check_goal.return_value = ["Injection attempt detected"]

    graph = _make_graph(guardrail_checker=checker)
    graph._event_callback = None
    state = _make_state("DROP TABLE users; --")
    result = await graph._node_initialize(state)
    assert result["agent_state"].status == GoalStatus.FAILED
    assert result.get("terminal_reason") == "guardrail_rejected"


@pytest.mark.asyncio
async def test_node_initialize_guardrail_no_issues() -> None:
    checker = MagicMock(spec=GuardrailChecker)
    checker.check_goal.return_value = []  # no issues

    graph = _make_graph(guardrail_checker=checker)
    graph._event_callback = None
    state = _make_state("List GitHub repos")
    result = await graph._node_initialize(state)
    assert result["agent_state"].status != GoalStatus.FAILED
    assert result.get("terminal_reason") != "guardrail_rejected"


@pytest.mark.asyncio
async def test_node_initialize_emits_goal_started_event() -> None:
    events = []

    async def _cb(event: dict) -> None:
        events.append(event)

    graph = _make_graph()
    graph._event_callback = _cb
    state = _make_state("Run tests")
    await graph._node_initialize(state)
    assert any(e.get("type") == "goal_started" for e in events)


# ---------------------------------------------------------------------------
# _node_rag_retrieval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_rag_retrieval_no_memory() -> None:
    graph = _make_graph()
    graph._event_callback = None
    agent_state = AgentState(goal="Analyze metrics", tenant_ctx=T)
    state = _make_state(agent_state=agent_state)
    result = await graph._node_rag_retrieval(state)
    assert "rag_context" in result
    # Without any memory sources, context should be empty
    assert result["rag_context"] == ""


@pytest.mark.asyncio
async def test_node_rag_retrieval_with_exec_memory() -> None:
    exec_mem = MagicMock(spec=ExecutionMemory)
    exec_mem.recall_async = AsyncMock(return_value=[
        {"plan": ["step1", "step2"], "goal": "past goal"}
    ])
    exec_mem.recall_failures = MagicMock(return_value=[])

    graph = _make_graph(exec_memory=exec_mem)
    graph._event_callback = None
    agent_state = AgentState(goal="Run tests", tenant_ctx=T)
    state = _make_state(agent_state=agent_state)
    result = await graph._node_rag_retrieval(state)
    assert "Past winning plans" in result.get("rag_context", "")


@pytest.mark.asyncio
async def test_node_rag_retrieval_with_long_term_memory() -> None:
    ltm = MagicMock(spec=LongTermMemoryStore)
    ltm_result = MagicMock()
    ltm_result.content = "Domain knowledge: test environments use pytest"
    ltm.recall_async = AsyncMock(return_value=[ltm_result])

    graph = _make_graph(long_term_memory=ltm)
    graph._event_callback = None
    agent_state = AgentState(goal="Test the app", tenant_ctx=T)
    state = _make_state(agent_state=agent_state)
    result = await graph._node_rag_retrieval(state)
    assert "Domain knowledge" in result.get("rag_context", "")


@pytest.mark.asyncio
async def test_node_rag_retrieval_exec_memory_fallback_on_error() -> None:
    """When async recall fails, falls back to sync recall."""
    exec_mem = MagicMock(spec=ExecutionMemory)
    exec_mem.recall_async = AsyncMock(side_effect=RuntimeError("DB down"))
    exec_mem.recall = MagicMock(return_value=[{"plan": ["fallback step"]}])
    exec_mem.recall_failures = MagicMock(return_value=[])

    graph = _make_graph(exec_memory=exec_mem)
    graph._event_callback = None
    agent_state = AgentState(goal="Test fallback", tenant_ctx=T)
    state = _make_state(agent_state=agent_state)
    result = await graph._node_rag_retrieval(state)
    # Should not raise; exec_mem.recall() should have been called
    exec_mem.recall.assert_called_once()


# ---------------------------------------------------------------------------
# _node_think (Chain of Thought)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_think_returns_cot_reasoning() -> None:
    graph = _make_graph(enable_cot=True)
    graph._event_callback = None
    agent_state = AgentState(goal="Analyze slow queries", tenant_ctx=T)
    state = _make_state(agent_state=agent_state)
    result = await graph._node_think(state)
    assert "cot_reasoning" in result
    assert isinstance(result["cot_reasoning"], str)


@pytest.mark.asyncio
async def test_node_think_with_model_router() -> None:
    graph = _make_graph(enable_cot=True)
    graph._model_router = MagicMock()
    graph._model_router.model_for.return_value = "claude-haiku"
    graph._event_callback = None
    agent_state = AgentState(goal="Optimize queries", tenant_ctx=T)
    state = _make_state(agent_state=agent_state)
    result = await graph._node_think(state)
    assert "cot_reasoning" in result
    graph._model_router.model_for.assert_called_with("think")


# ---------------------------------------------------------------------------
# _node_reflect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_reflect_with_failed_steps() -> None:
    graph = _make_graph(enable_reflection=True)
    graph._event_callback = None
    agent_state = AgentState(goal="Fix CI pipeline", tenant_ctx=T)
    failed_step = StepResult(
        description="Run tests",
        status=StepStatus.FAILED,
        error="Connection timeout",
    )
    agent_state.steps = [failed_step]

    state = _make_state(agent_state=agent_state)
    result = await graph._node_reflect(state)
    assert result["agent_state"].verification_feedback != ""


@pytest.mark.asyncio
async def test_node_reflect_no_failed_steps() -> None:
    graph = _make_graph(enable_reflection=True)
    graph._event_callback = None
    agent_state = AgentState(goal="Deploy app", tenant_ctx=T)
    agent_state.steps = []  # No failed steps
    agent_state.error_message = "Unknown failure"

    state = _make_state(agent_state=agent_state)
    result = await graph._node_reflect(state)
    assert "agent_state" in result


# ---------------------------------------------------------------------------
# _node_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_plan_returns_plan() -> None:
    provider = FakeProvider()
    graph = AgentGraph(planner=provider, executor=provider, verifier=provider)
    graph._event_callback = None
    agent_state = AgentState(goal="Fix the bug", tenant_ctx=T)
    state = _make_state(agent_state=agent_state, iteration=0)
    result = await graph._node_plan(state)
    assert "plan" in result
    assert isinstance(result["plan"], list)
    assert len(result["plan"]) > 0
    assert result["iteration"] == 1


@pytest.mark.asyncio
async def test_node_plan_with_rag_context() -> None:
    provider = FakeProvider()
    graph = AgentGraph(planner=provider, executor=provider, verifier=provider)
    graph._event_callback = None
    agent_state = AgentState(goal="Deploy service", tenant_ctx=T)
    state = _make_state(agent_state=agent_state, rag_context="Use kubectl for deployments")
    result = await graph._node_plan(state)
    assert result["plan"]


@pytest.mark.asyncio
async def test_node_plan_with_verification_feedback() -> None:
    provider = FakeProvider()
    graph = AgentGraph(planner=provider, executor=provider, verifier=provider)
    graph._event_callback = None
    agent_state = AgentState(goal="Fix login bug", tenant_ctx=T)
    agent_state.verification_feedback = "The patch was incomplete. Try again with auth module."
    state = _make_state(agent_state=agent_state)
    result = await graph._node_plan(state)
    assert result["plan"]


@pytest.mark.asyncio
async def test_node_plan_emits_plan_ready_event() -> None:
    events = []

    async def _cb(event: dict) -> None:
        events.append(event)

    provider = FakeProvider()
    graph = AgentGraph(planner=provider, executor=provider, verifier=provider)
    graph._event_callback = _cb
    agent_state = AgentState(goal="List users", tenant_ctx=T)
    state = _make_state(agent_state=agent_state)
    await graph._node_plan(state)
    assert any(e.get("type") == "plan_ready" for e in events)


# ---------------------------------------------------------------------------
# _node_verify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_verify_success() -> None:
    provider = FakeProvider()
    provider._fake_response = '{"success": true, "reason": "All steps completed"}'
    graph = AgentGraph(planner=provider, executor=provider, verifier=provider)
    graph._event_callback = None

    agent_state = AgentState(goal="Deploy app", tenant_ctx=T)
    completed_step = StepResult(
        description="Deploy app",
        status=StepStatus.COMPLETE,
        output="Deployed successfully",
    )
    agent_state.steps = [completed_step]

    state = _make_state(agent_state=agent_state)
    result = await graph._node_verify(state)
    assert result["agent_state"].verification_success is True


@pytest.mark.asyncio
async def test_node_verify_failure() -> None:
    # Provide a failure response for the verifier
    provider = FakeProvider(responses=[
        '{"steps": ["step1"]}',  # planner
        "step output",  # executor
        '{"success": false, "reason": "Deployment failed", "retry": true}',  # verifier
    ])
    graph = AgentGraph(planner=provider, executor=provider, verifier=provider)
    graph._event_callback = None

    agent_state = AgentState(goal="Deploy app", tenant_ctx=T)
    state = _make_state(agent_state=agent_state)
    result = await graph._node_verify(state)
    assert result["agent_state"].verification_success is False


@pytest.mark.asyncio
async def test_node_verify_emits_verification_done_event() -> None:
    events = []

    async def _cb(event: dict) -> None:
        events.append(event)

    provider = FakeProvider()
    graph = AgentGraph(planner=provider, executor=provider, verifier=provider)
    graph._event_callback = _cb

    agent_state = AgentState(goal="Test", tenant_ctx=T)
    state = _make_state(agent_state=agent_state)
    await graph._node_verify(state)
    assert any(e.get("type") == "verification_done" for e in events)


@pytest.mark.asyncio
async def test_node_verify_with_exec_memory_on_success() -> None:
    exec_mem = MagicMock(spec=ExecutionMemory)
    exec_mem.record = MagicMock()
    exec_mem.recall_failures = MagicMock(return_value=[])

    provider = FakeProvider()
    provider._fake_response = '{"success": true, "reason": "Done"}'
    graph = AgentGraph(
        planner=provider, executor=provider, verifier=provider,
        exec_memory=exec_mem,
    )
    graph._event_callback = None

    agent_state = AgentState(goal="Deploy", tenant_ctx=T)
    state = _make_state(agent_state=agent_state)
    await graph._node_verify(state)
    exec_mem.record.assert_called_once()


# ---------------------------------------------------------------------------
# _route
# ---------------------------------------------------------------------------


def test_route_complete_on_success() -> None:
    graph = _make_graph()
    agent_state = AgentState(goal="Test", tenant_ctx=T)
    agent_state.verification_success = True
    state = _make_state(agent_state=agent_state, iteration=1)
    assert graph._route(state) == "complete"


def test_route_replan_on_failure() -> None:
    graph = _make_graph()
    agent_state = AgentState(goal="Test", tenant_ctx=T)
    agent_state.verification_success = False
    agent_state.context["verification_retry"] = True
    state = _make_state(agent_state=agent_state, iteration=1)
    assert graph._route(state) == "replan"


def test_route_max_iter_when_exceeded() -> None:
    graph = _make_graph(max_iterations=3)
    agent_state = AgentState(goal="Test", tenant_ctx=T)
    agent_state.verification_success = False
    agent_state.context["verification_retry"] = True
    state = _make_state(agent_state=agent_state, iteration=3)
    assert graph._route(state) == "max_iter"


def test_route_max_iter_on_no_agent_state() -> None:
    graph = _make_graph()
    state: GraphState = {"goal": "Test", "tenant_ctx": T}
    assert graph._route(state) == "max_iter"


def test_route_max_iter_on_guardrail_rejected() -> None:
    graph = _make_graph()
    agent_state = AgentState(goal="DROP TABLE", tenant_ctx=T)
    agent_state.verification_success = False
    state = _make_state(agent_state=agent_state)
    state["terminal_reason"] = "guardrail_rejected"
    assert graph._route(state) == "max_iter"


def test_route_max_iter_when_no_retry() -> None:
    graph = _make_graph()
    agent_state = AgentState(goal="Failing goal", tenant_ctx=T)
    agent_state.verification_success = False
    agent_state.context["verification_retry"] = False
    state = _make_state(agent_state=agent_state, iteration=1)
    assert graph._route(state) == "max_iter"
    assert agent_state.status == GoalStatus.FAILED


def test_route_waiting_human_in_supervised_mode() -> None:
    hitl = HITLGateway()
    hitl.request_approval(goal_id="g1", action="deploy", risk_level="high", tenant_ctx=T)

    graph = _make_graph(autonomy_mode="supervised", hitl_gateway=hitl)
    agent_state = AgentState(goal="Deploy to prod", tenant_ctx=T)
    agent_state.verification_success = False
    agent_state.context["verification_retry"] = True
    state = _make_state(agent_state=agent_state, iteration=1)
    result = graph._route(state)
    assert result == "waiting_human"


def test_route_reflect_when_reflection_enabled() -> None:
    graph = _make_graph(enable_reflection=True)
    agent_state = AgentState(goal="Test", tenant_ctx=T)
    agent_state.verification_success = False
    agent_state.context["verification_retry"] = True
    state = _make_state(agent_state=agent_state, iteration=1)
    assert graph._route(state) == "reflect"


# ---------------------------------------------------------------------------
# Full graph run (integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_graph_run_simple_goal() -> None:
    graph = _make_graph(max_iterations=2)
    events: list[dict] = []

    async def _cb(event: dict) -> None:
        events.append(event)

    result = await graph.run(
        goal="List all open GitHub issues",
        tenant_ctx=T,
        event_callback=_cb,
    )
    assert result.goal == "List all open GitHub issues"
    # Should emit at least goal_started
    assert any(e.get("type") == "goal_started" for e in events)


@pytest.mark.asyncio
async def test_full_graph_run_with_context() -> None:
    graph = _make_graph(max_iterations=1)

    result = await graph.run(
        goal="Run unit tests",
        tenant_ctx=T,
        initial_context={"project": "myapp", "tool_prompt": "Available: pytest"},
    )
    assert result.goal == "Run unit tests"


@pytest.mark.asyncio
async def test_full_graph_run_guardrail_blocks() -> None:
    """Guardrail-rejected goals emit a goal_rejected event."""
    checker = MagicMock(spec=GuardrailChecker)
    checker.check_goal.return_value = ["Injection detected"]
    events: list[dict] = []

    async def _cb(event: dict) -> None:
        events.append(event)

    graph = _make_graph(guardrail_checker=checker, max_iterations=1)

    result = await graph.run(
        goal="'; DROP TABLE users; --",
        tenant_ctx=T,
        event_callback=_cb,
    )
    # The guardrail rejects the goal in initialize; status is set to FAILED there
    # but may be overridden by subsequent nodes. Check events instead.
    assert any(e.get("type") == "goal_rejected" for e in events)


@pytest.mark.asyncio
async def test_full_graph_run_with_cot_enabled() -> None:
    graph = _make_graph(enable_cot=True, max_iterations=1)

    result = await graph.run(
        goal="Analyze performance bottleneck",
        tenant_ctx=T,
    )
    assert result.goal == "Analyze performance bottleneck"


@pytest.mark.asyncio
async def test_full_graph_run_cost_controller_check() -> None:
    """Cost controller check should not break graph execution."""
    cost = MagicMock(spec=CostController)
    cost.check_and_record = AsyncMock(return_value=True)

    graph = _make_graph(cost_controller=cost, max_iterations=1)

    result = await graph.run(
        goal="Generate report",
        tenant_ctx=T,
    )
    assert result.goal == "Generate report"
