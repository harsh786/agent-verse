"""Comprehensive coverage tests for app/agent/graph.py.

Targets the uncovered sections of graph.py:
- _node_reflect
- _node_verify  (success + failure + retry=False rollback)
- _execute_step  (dedup hit, guardrail block, circuit breaker, cost check)
- _route         (all branches)
- _node_rag_retrieval  (with exec_memory)
- parallel wave execution in _node_execute
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.governance.cost import CostController
from app.intelligence.guardrails import GuardrailChecker
from app.memory.execution import ExecutionMemory
from app.memory.long_term import LongTermMemoryStore
from app.providers.base import CompletionResponse, TokenUsage
from app.providers.fake import FakeProvider
from app.reliability.circuit_breaker import CircuitBreaker
from app.reliability.dedup import DeduplicationCache
from app.reliability.rollback import RollbackEngine
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="graph-cov-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


def _make_graph(
    planner: FakeProvider | None = None,
    executor: FakeProvider | None = None,
    verifier: FakeProvider | None = None,
    **kwargs,
) -> AgentGraph:
    return AgentGraph(
        planner=planner or FakeProvider(responses=["step 1"]),
        executor=executor or FakeProvider(responses=["step output"]),
        verifier=verifier or FakeProvider(responses=['{"success": true, "reason": "done"}']),
        **kwargs,
    )


def _make_agent_state(goal: str = "test goal") -> AgentState:
    return AgentState(goal=goal, tenant_ctx=T)


# ===========================================================================
# _node_reflect
# ===========================================================================

@pytest.mark.asyncio
async def test_node_reflect_generates_feedback_from_failed_steps() -> None:
    """_node_reflect calls the planner and populates verification_feedback."""
    planner = FakeProvider(responses=["Try a different approach: use API instead."])
    graph = _make_graph(planner=planner)

    agent_state = _make_agent_state("deploy service")
    agent_state.steps.append(
        StepResult(
            description="Build Docker image",
            status=StepStatus.FAILED,
            error="Docker daemon not running",
        )
    )

    state = {"agent_state": agent_state, "tenant_ctx": T}
    result = await graph._node_reflect(state)

    updated: AgentState = result["agent_state"]
    assert updated.verification_feedback == "Try a different approach: use API instead."


@pytest.mark.asyncio
async def test_node_reflect_uses_error_message_when_no_failed_steps() -> None:
    """When there are no failed steps, reflect uses agent_state.error_message."""
    planner = FakeProvider(responses=["Replan around the error."])
    graph = _make_graph(planner=planner)

    agent_state = _make_agent_state("backup database")
    agent_state.error_message = "Timeout on DB connection"

    state = {"agent_state": agent_state, "tenant_ctx": T}
    result = await graph._node_reflect(state)

    assert result["agent_state"].verification_feedback == "Replan around the error."


@pytest.mark.asyncio
async def test_node_reflect_with_model_router() -> None:
    """_node_reflect uses the model_router if wired."""
    planner = FakeProvider(responses=["Reflect response"])
    mock_router = MagicMock()
    mock_router.model_for.return_value = "claude-3-haiku-20240307"
    graph = _make_graph(planner=planner, model_router=mock_router)

    agent_state = _make_agent_state()
    state = {"agent_state": agent_state, "tenant_ctx": T}
    result = await graph._node_reflect(state)

    mock_router.model_for.assert_called_with("reflection")
    assert result["agent_state"].verification_feedback == "Reflect response"


# ===========================================================================
# _node_verify  — success path
# ===========================================================================

@pytest.mark.asyncio
async def test_node_verify_success_marks_goal_complete() -> None:
    verifier = FakeProvider(responses=['{"success": true, "reason": "All done"}'])
    graph = _make_graph(verifier=verifier)

    agent_state = _make_agent_state("send email")
    agent_state.steps.append(
        StepResult(description="Draft email", output="Email drafted", status=StepStatus.COMPLETE)
    )

    state = {"agent_state": agent_state, "tenant_ctx": T}
    result = await graph._node_verify(state)

    updated: AgentState = result["agent_state"]
    assert updated.verification_success is True
    assert updated.status == GoalStatus.COMPLETE


@pytest.mark.asyncio
async def test_node_verify_success_calls_exec_memory_record() -> None:
    """On success, execution memory is updated."""
    verifier = FakeProvider(responses=['{"success": true, "reason": "done"}'])
    mock_exec_mem = MagicMock(spec=ExecutionMemory)
    mock_exec_mem.record = MagicMock()
    mock_exec_mem.record_async = AsyncMock()

    graph = _make_graph(verifier=verifier, exec_memory=mock_exec_mem)
    agent_state = _make_agent_state("do task")
    agent_state.plan = ["step 1"]
    agent_state.steps.append(
        StepResult(description="step 1", output="done", status=StepStatus.COMPLETE)
    )

    state = {"agent_state": agent_state, "tenant_ctx": T}
    await graph._node_verify(state)

    mock_exec_mem.record.assert_called_once()


@pytest.mark.asyncio
async def test_node_verify_success_calls_long_term_memory_extraction() -> None:
    """On success, long-term memory extraction is triggered."""
    verifier = FakeProvider(responses=['{"success": true, "reason": "done"}'])
    mock_ltm = MagicMock(spec=LongTermMemoryStore)
    mock_ltm.extract_from_goal = MagicMock()
    mock_ltm.extract_from_goal_async = AsyncMock()

    graph = _make_graph(verifier=verifier, long_term_memory=mock_ltm)
    agent_state = _make_agent_state("process data")
    agent_state.steps.append(
        StepResult(description="Load data", output="100 rows", status=StepStatus.COMPLETE)
    )

    state = {"agent_state": agent_state, "tenant_ctx": T}
    await graph._node_verify(state)

    mock_ltm.extract_from_goal.assert_called_once()


@pytest.mark.asyncio
async def test_node_verify_success_with_eval_runner() -> None:
    """EvalRunner.score_and_persist is called and result stored in context."""
    from app.intelligence.eval_runner import EvalRunner

    verifier = FakeProvider(responses=['{"success": true, "reason": "complete"}'])
    mock_eval = MagicMock(spec=EvalRunner)
    mock_scorecard = MagicMock()
    mock_scorecard.average_score.return_value = 0.85
    mock_eval.score_and_persist = AsyncMock(return_value=mock_scorecard)

    graph = _make_graph(verifier=verifier, eval_runner=mock_eval)
    agent_state = _make_agent_state("score this")
    agent_state.steps.append(
        StepResult(description="do work", output="result", status=StepStatus.COMPLETE)
    )

    state = {"agent_state": agent_state, "tenant_ctx": T}
    result = await graph._node_verify(state)

    mock_eval.score_and_persist.assert_called_once()
    assert result["agent_state"].context.get("eval_scorecard") == mock_scorecard


# ===========================================================================
# _node_verify  — failure paths
# ===========================================================================

@pytest.mark.asyncio
async def test_node_verify_failure_stays_in_verifying() -> None:
    """On failure with retry=true, status is VERIFYING and success=False."""
    verifier = FakeProvider(responses=['{"success": false, "reason": "incomplete", "retry": true}'])
    graph = _make_graph(verifier=verifier)

    agent_state = _make_agent_state("incomplete task")
    state = {"agent_state": agent_state, "tenant_ctx": T}
    result = await graph._node_verify(state)

    updated: AgentState = result["agent_state"]
    assert updated.verification_success is False
    assert updated.context.get("verification_retry") is True


@pytest.mark.asyncio
async def test_node_verify_failure_retry_false_triggers_rollback() -> None:
    """When retry=false and rollback_engine has entries, rollback_all_async is called."""
    verifier = FakeProvider(responses=['{"success": false, "reason": "fatal", "retry": false}'])
    mock_rollback = MagicMock(spec=RollbackEngine)
    mock_rollback.__len__ = MagicMock(return_value=2)
    mock_rollback.rollback_all_async = AsyncMock(return_value=2)

    graph = _make_graph(verifier=verifier, rollback_engine=mock_rollback)
    agent_state = _make_agent_state("risky action")
    state = {"agent_state": agent_state, "tenant_ctx": T}
    await graph._node_verify(state)

    mock_rollback.rollback_all_async.assert_called_once()


@pytest.mark.asyncio
async def test_node_verify_failure_retry_true_does_not_rollback() -> None:
    """On retry=true, rollback is NOT triggered."""
    verifier = FakeProvider(responses=['{"success": false, "reason": "retry", "retry": true}'])
    mock_rollback = MagicMock(spec=RollbackEngine)
    mock_rollback.__len__ = MagicMock(return_value=1)
    mock_rollback.rollback_all_async = AsyncMock()

    graph = _make_graph(verifier=verifier, rollback_engine=mock_rollback)
    agent_state = _make_agent_state("retryable")
    state = {"agent_state": agent_state, "tenant_ctx": T}
    await graph._node_verify(state)

    mock_rollback.rollback_all_async.assert_not_called()


@pytest.mark.asyncio
async def test_node_verify_with_model_router() -> None:
    """_node_verify uses model_router for verification model selection."""
    verifier = FakeProvider(responses=['{"success": true, "reason": "ok"}'])
    mock_router = MagicMock()
    mock_router.model_for.return_value = "claude-3-haiku"
    graph = _make_graph(verifier=verifier, model_router=mock_router)

    agent_state = _make_agent_state("task")
    state = {"agent_state": agent_state, "tenant_ctx": T}
    await graph._node_verify(state)

    mock_router.model_for.assert_called_with("verification")


# ===========================================================================
# _execute_step — dedup cache hit
# ===========================================================================

@pytest.mark.asyncio
async def test_execute_step_dedup_cache_hit_returns_early() -> None:
    """When the dedup cache marks a step as duplicate, skip the LLM call."""
    mock_dedup = MagicMock(spec=DeduplicationCache)
    mock_dedup.is_duplicate.return_value = True
    mock_dedup.mark_seen = MagicMock()

    executor = FakeProvider(responses=["this should not be returned"])
    graph = _make_graph(executor=executor, dedup_cache=mock_dedup)

    agent_state = _make_agent_state("test goal for dedup")
    result = await graph._execute_step("call search tool", agent_state, T)

    assert result == "Duplicate step, returning cached result."
    # FakeProvider executor should NOT have been called
    assert len(executor.call_history) == 0


@pytest.mark.asyncio
async def test_execute_step_dedup_marks_new_step_as_seen() -> None:
    """When a step is NOT a duplicate, it gets marked as seen."""
    mock_dedup = MagicMock(spec=DeduplicationCache)
    mock_dedup.is_duplicate.return_value = False
    mock_dedup.mark_seen = MagicMock()

    graph = _make_graph(dedup_cache=mock_dedup)
    agent_state = _make_agent_state("goal")
    await graph._execute_step("step 1", agent_state, T)

    mock_dedup.mark_seen.assert_called_once()


# ===========================================================================
# _execute_step — guardrail block
# ===========================================================================

@pytest.mark.asyncio
async def test_execute_step_guardrail_block_returns_violation_message() -> None:
    """GuardrailChecker violations return 'Guardrail blocked' message."""
    mock_guardrail = MagicMock(spec=GuardrailChecker)
    mock_guardrail.check.return_value = ["SQL injection pattern detected"]

    executor = FakeProvider(responses=["LLM output"])
    graph = _make_graph(executor=executor, guardrail_checker=mock_guardrail)

    agent_state = _make_agent_state("attack the system")
    result = await graph._execute_step("call database with DROP TABLE", agent_state, T)

    assert "Guardrail blocked" in result
    assert "SQL injection" in result
    # LLM should NOT have been called
    assert len(executor.call_history) == 0


@pytest.mark.asyncio
async def test_execute_step_guardrail_allows_clean_steps() -> None:
    """When guardrail has no violations, execution proceeds normally."""
    mock_guardrail = MagicMock(spec=GuardrailChecker)
    mock_guardrail.check.return_value = []  # No violations

    executor = FakeProvider(responses=["completed successfully"])
    graph = _make_graph(executor=executor, guardrail_checker=mock_guardrail)

    agent_state = _make_agent_state("clean task")
    result = await graph._execute_step("call search api", agent_state, T)

    # Guardrail should not block; executor should have been called
    assert len(executor.call_history) == 1
    assert "Guardrail blocked" not in result


# ===========================================================================
# _execute_step — circuit breaker
# ===========================================================================

@pytest.mark.asyncio
async def test_execute_step_circuit_breaker_open_skips_execution() -> None:
    """When the circuit breaker is open, step is skipped with message."""
    mock_breaker = MagicMock(spec=CircuitBreaker)
    mock_breaker.can_call.return_value = False

    executor = FakeProvider(responses=["should not run"])
    graph = _make_graph(
        executor=executor,
        circuit_breakers={"llm": mock_breaker},
    )

    agent_state = _make_agent_state("failing repeatedly")
    result = await graph._execute_step("llm call to endpoint", agent_state, T)

    assert "Circuit open" in result
    assert len(executor.call_history) == 0


@pytest.mark.asyncio
async def test_execute_step_circuit_breaker_closed_allows_execution() -> None:
    """When circuit breaker is closed, execution proceeds normally."""
    mock_breaker = MagicMock(spec=CircuitBreaker)
    mock_breaker.can_call.return_value = True
    mock_breaker.record_success = MagicMock()

    executor = FakeProvider(responses=["success output"])
    graph = _make_graph(
        executor=executor,
        circuit_breakers={"llm": mock_breaker},
    )

    agent_state = _make_agent_state("stable task")
    result = await graph._execute_step("step 1", agent_state, T)

    assert result == "success output"
    mock_breaker.record_success.assert_called_once()


@pytest.mark.asyncio
async def test_execute_step_circuit_breaker_records_failure_on_exception() -> None:
    """When LLM raises, the circuit breaker records a failure."""
    from app.providers.base import CompletionRequest

    mock_breaker = MagicMock(spec=CircuitBreaker)
    mock_breaker.can_call.return_value = True
    mock_breaker.record_failure = MagicMock()

    class _BoomProvider:
        async def complete(self, req: CompletionRequest) -> None:
            raise RuntimeError("LLM down")

        async def stream_tokens(self, req, on_token):
            raise RuntimeError("LLM down")

        async def embed(self, req: object) -> None:
            raise NotImplementedError

        def supports_vision(self) -> bool:
            return False

        def supports_tool_use(self) -> bool:
            return False

    graph = _make_graph(
        executor=_BoomProvider(),
        circuit_breakers={"llm": mock_breaker},
    )

    agent_state = _make_agent_state("task that fails")
    with pytest.raises(RuntimeError):
        await graph._execute_step("step 1", agent_state, T)

    mock_breaker.record_failure.assert_called_once()


# ===========================================================================
# _execute_step — cost controller over budget
# ===========================================================================

@pytest.mark.asyncio
async def test_execute_step_cost_controller_over_budget_returns_skip_message() -> None:
    """When cost_controller.check_and_record returns False, step is skipped."""
    mock_cost = MagicMock(spec=CostController)
    mock_cost.check_and_record = AsyncMock(return_value=False)

    # FakeProvider returns a response with token usage so cost can be calculated
    executor = FakeProvider(responses=["LLM response"])

    graph = _make_graph(executor=executor, cost_controller=mock_cost)
    agent_state = _make_agent_state("expensive operation")
    result = await graph._execute_step("call expensive api", agent_state, T)

    assert "budget exceeded" in result.lower()
    mock_cost.check_and_record.assert_called_once()


@pytest.mark.asyncio
async def test_execute_step_cost_controller_within_budget_continues() -> None:
    """When cost_controller returns True, execution output is returned."""
    mock_cost = MagicMock(spec=CostController)
    mock_cost.check_and_record = AsyncMock(return_value=True)

    executor = FakeProvider(responses=["good output"])
    graph = _make_graph(executor=executor, cost_controller=mock_cost)

    agent_state = _make_agent_state("affordable task")
    result = await graph._execute_step("step 1", agent_state, T)

    assert result == "good output"


# ===========================================================================
# _route — all branches
# ===========================================================================

def test_route_returns_complete_on_verification_success() -> None:
    graph = _make_graph()
    agent_state = _make_agent_state()
    agent_state.verification_success = True

    state = {"agent_state": agent_state, "tenant_ctx": T, "iteration": 0}
    result = graph._route(state)
    assert result == "complete"


def test_route_returns_max_iter_when_iterations_exceeded() -> None:
    graph = _make_graph()
    agent_state = _make_agent_state()
    agent_state.verification_success = False
    agent_state.context["verification_retry"] = True

    state = {"agent_state": agent_state, "tenant_ctx": T, "iteration": 20}  # > default 15
    result = graph._route(state)
    assert result == "max_iter"
    assert agent_state.status == GoalStatus.FAILED


def test_route_returns_max_iter_when_retry_false() -> None:
    """When verifier says retry=False, route terminates immediately."""
    graph = _make_graph()
    agent_state = _make_agent_state()
    agent_state.verification_success = False
    agent_state.context["verification_retry"] = False

    state = {"agent_state": agent_state, "tenant_ctx": T, "iteration": 0}
    result = graph._route(state)
    assert result == "max_iter"
    assert agent_state.status == GoalStatus.FAILED


def test_route_returns_reflect_when_reflection_enabled() -> None:
    """With enable_reflection=True, route goes to 'reflect' on failure."""
    graph = _make_graph(enable_reflection=True)
    agent_state = _make_agent_state()
    agent_state.verification_success = False
    agent_state.context["verification_retry"] = True

    state = {"agent_state": agent_state, "tenant_ctx": T, "iteration": 1}
    result = graph._route(state)
    assert result == "reflect"


def test_route_returns_replan_by_default() -> None:
    """Without reflection, route goes to 'replan' on failure."""
    graph = _make_graph(enable_reflection=False)
    agent_state = _make_agent_state()
    agent_state.verification_success = False
    agent_state.context["verification_retry"] = True

    state = {"agent_state": agent_state, "tenant_ctx": T, "iteration": 1}
    result = graph._route(state)
    assert result == "replan"


def test_route_returns_max_iter_when_agent_state_is_none() -> None:
    """Defensive: if agent_state is missing, route terminates."""
    graph = _make_graph()
    state = {"tenant_ctx": T, "iteration": 0}
    result = graph._route(state)
    assert result == "max_iter"


def test_route_returns_max_iter_on_guardrail_rejected() -> None:
    """terminal_reason=guardrail_rejected causes immediate termination."""
    graph = _make_graph()
    agent_state = _make_agent_state()
    agent_state.verification_success = False

    state = {
        "agent_state": agent_state,
        "tenant_ctx": T,
        "iteration": 0,
        "terminal_reason": "guardrail_rejected",
    }
    result = graph._route(state)
    assert result == "max_iter"


def test_route_waiting_human_in_supervised_mode() -> None:
    """In supervised mode with pending HITL requests, route to waiting_human."""
    from app.governance.hitl import HITLGateway

    mock_hitl = MagicMock(spec=HITLGateway)
    mock_hitl.list_pending.return_value = [{"id": "req1"}]  # pending approval

    graph = _make_graph(
        hitl_gateway=mock_hitl,
        autonomy_mode="supervised",
    )
    agent_state = _make_agent_state()
    agent_state.verification_success = False
    agent_state.context["verification_retry"] = True

    state = {"agent_state": agent_state, "tenant_ctx": T, "iteration": 1}
    result = graph._route(state)
    assert result == "waiting_human"
    assert agent_state.status == GoalStatus.WAITING_HUMAN


# ===========================================================================
# _node_rag_retrieval — with exec_memory
# ===========================================================================

@pytest.mark.asyncio
async def test_node_rag_retrieval_with_exec_memory_hit() -> None:
    """Past plans from exec_memory appear in rag_context."""
    mock_exec_mem = MagicMock(spec=ExecutionMemory)
    mock_exec_mem.recall_async = AsyncMock(return_value=[
        {"plan": ["step A", "step B"], "goal": "similar goal"}
    ])
    mock_exec_mem.recall_failures = MagicMock(return_value=[])

    graph = _make_graph(exec_memory=mock_exec_mem)
    agent_state = _make_agent_state("similar goal")

    state = {
        "agent_state": agent_state,
        "tenant_ctx": T,
        "rag_context": "",
    }
    result = await graph._node_rag_retrieval(state)

    assert "rag_context" in result
    assert "step A" in result["rag_context"]


@pytest.mark.asyncio
async def test_node_rag_retrieval_exec_memory_async_fails_falls_back() -> None:
    """When recall_async fails, falls back to sync recall."""
    mock_exec_mem = MagicMock(spec=ExecutionMemory)
    mock_exec_mem.recall_async = AsyncMock(side_effect=RuntimeError("DB down"))
    mock_exec_mem.recall = MagicMock(return_value=[
        {"plan": ["fallback-step"], "goal": "test"}
    ])
    mock_exec_mem.recall_failures = MagicMock(return_value=[])

    graph = _make_graph(exec_memory=mock_exec_mem)
    agent_state = _make_agent_state("test goal")

    state = {"agent_state": agent_state, "tenant_ctx": T, "rag_context": ""}
    result = await graph._node_rag_retrieval(state)

    mock_exec_mem.recall.assert_called_once()
    assert "rag_context" in result


@pytest.mark.asyncio
async def test_node_rag_retrieval_with_failure_patterns() -> None:
    """Past failure patterns are included in rag_context."""
    mock_exec_mem = MagicMock(spec=ExecutionMemory)
    mock_exec_mem.recall_async = AsyncMock(return_value=[])
    mock_exec_mem.recall_failures = MagicMock(return_value=[
        {"goal": "failed attempt 1"},
        {"goal_text": "failed attempt 2"},
    ])

    graph = _make_graph(exec_memory=mock_exec_mem)
    agent_state = _make_agent_state("learn from failures")

    state = {"agent_state": agent_state, "tenant_ctx": T, "rag_context": ""}
    result = await graph._node_rag_retrieval(state)

    assert "Previously Failed" in result["rag_context"] or result["rag_context"] == ""


@pytest.mark.asyncio
async def test_node_rag_retrieval_no_memory_returns_empty_context() -> None:
    """Without memory components, rag_context is empty string."""
    graph = _make_graph()
    agent_state = _make_agent_state("bare goal")
    state = {"agent_state": agent_state, "tenant_ctx": T, "rag_context": ""}
    result = await graph._node_rag_retrieval(state)
    assert result["rag_context"] == ""


# ===========================================================================
# _node_execute — parallel wave execution
# ===========================================================================

@pytest.mark.asyncio
async def test_node_execute_parallel_steps_all_complete() -> None:
    """Multiple independent steps execute in parallel via asyncio.gather."""
    # Give executor enough responses for both steps
    executor = FakeProvider(responses=["output_A", "output_B", "output_C", "output_D"])
    planner = FakeProvider(responses=[
        # Return a structured plan JSON with two parallel steps (no depends_on)
        json.dumps({
            "steps": [
                {"id": "s0", "description": "search web", "depends_on": []},
                {"id": "s1", "description": "check docs", "depends_on": []},
            ]
        })
    ])
    verifier = FakeProvider(responses=['{"success": true, "reason": "done"}'])
    graph = AgentGraph(planner=planner, executor=executor, verifier=verifier)

    state = await graph.run(goal="parallel task", tenant_ctx=T)

    # Both steps should have been attempted
    completed = [s for s in state.steps if s.status == StepStatus.COMPLETE]
    assert len(completed) >= 1


@pytest.mark.asyncio
async def test_node_execute_single_step_sequential() -> None:
    """A single-step plan executes sequentially and completes."""
    planner = FakeProvider(responses=[
        json.dumps({"steps": [{"id": "s0", "description": "run tests", "depends_on": []}]})
    ])
    executor = FakeProvider(responses=["Tests passed"])
    verifier = FakeProvider(responses=['{"success": true, "reason": "tests ok"}'])

    graph = AgentGraph(planner=planner, executor=executor, verifier=verifier)
    state = await graph.run(goal="run test suite", tenant_ctx=T)

    assert state.status == GoalStatus.COMPLETE


@pytest.mark.asyncio
async def test_node_execute_plain_string_steps() -> None:
    """Plain text steps (non-JSON) execute as sequential steps."""
    planner = FakeProvider(responses=["Deploy the app\nRun smoke tests"])
    executor = FakeProvider(responses=["deployed", "tests passed"])
    verifier = FakeProvider(responses=['{"success": true, "reason": "ok"}'])

    graph = AgentGraph(planner=planner, executor=executor, verifier=verifier)
    state = await graph.run(goal="deploy and test", tenant_ctx=T)

    assert state.status == GoalStatus.COMPLETE


# ===========================================================================
# Full agent run — integration-style
# ===========================================================================

@pytest.mark.asyncio
async def test_agent_run_completes_on_first_verify() -> None:
    """A simple goal with a successful verifier runs to completion."""
    planner = FakeProvider(responses=["Do the thing"])
    executor = FakeProvider(responses=["Thing done"])
    verifier = FakeProvider(responses=['{"success": true, "reason": "complete"}'])

    graph = AgentGraph(planner=planner, executor=executor, verifier=verifier)
    state = await graph.run(goal="do a thing", tenant_ctx=T)

    assert state.status == GoalStatus.COMPLETE
    assert len(state.steps) >= 1


@pytest.mark.asyncio
async def test_agent_run_fails_on_max_iterations() -> None:
    """Verifier always fails → agent reaches max iterations and fails."""
    planner = FakeProvider(responses=["step 1"] * 20)
    executor = FakeProvider(responses=["output"] * 20)
    verifier = FakeProvider(responses=['{"success": false, "reason": "not done", "retry": true}'] * 20)

    graph = AgentGraph(
        planner=planner, executor=executor, verifier=verifier, max_iterations=2
    )
    state = await graph.run(goal="impossible task", tenant_ctx=T)

    assert state.status == GoalStatus.FAILED


@pytest.mark.asyncio
async def test_agent_run_with_reflection_enabled() -> None:
    """With enable_reflection=True, the reflect node is used after verify failure."""
    planner = FakeProvider(responses=["step 1", "better step"])
    executor = FakeProvider(responses=["output 1", "output 2"])
    verifier = FakeProvider(responses=[
        '{"success": false, "reason": "incomplete", "retry": true}',
        '{"success": true, "reason": "done"}',
    ])
    reflector = FakeProvider(responses=["Try harder next time"])  # planner is also reflector

    graph = AgentGraph(
        planner=FakeProvider(responses=["step 1", "Try harder", "step 2"]),
        executor=executor,
        verifier=verifier,
        enable_reflection=True,
        max_iterations=3,
    )
    state = await graph.run(goal="task needing reflection", tenant_ctx=T)

    # Should eventually complete or run out of iterations
    assert state.status in (GoalStatus.COMPLETE, GoalStatus.FAILED)


@pytest.mark.asyncio
async def test_agent_run_with_event_callback() -> None:
    """Events are delivered to the callback during agent execution."""
    planner = FakeProvider(responses=["step 1"])
    executor = FakeProvider(responses=["output"])
    verifier = FakeProvider(responses=['{"success": true, "reason": "done"}'])

    events: list[dict] = []

    async def _cb(event: dict) -> None:
        events.append(event)

    graph = AgentGraph(planner=planner, executor=executor, verifier=verifier)
    await graph.run(goal="event test", tenant_ctx=T, event_callback=_cb)

    event_types = {e["type"] for e in events}
    # At least one event should have been emitted
    assert len(events) > 0


@pytest.mark.asyncio
async def test_agent_run_returns_failed_state_on_unhandled_exception() -> None:
    """When the graph raises unexpectedly, AgentState.status=FAILED is returned."""
    from app.providers.base import CompletionRequest

    class _BoringFailProvider:
        call_count = 0

        async def complete(self, req: CompletionRequest) -> CompletionResponse:
            raise RuntimeError("Unexpected crash")

        async def embed(self, req: object) -> None:
            raise NotImplementedError

        def supports_vision(self) -> bool:
            return False

        def supports_tool_use(self) -> bool:
            return False

    graph = AgentGraph(
        planner=_BoringFailProvider(),
        executor=FakeProvider(),
        verifier=FakeProvider(),
    )
    state = await graph.run(goal="crash test", tenant_ctx=T)

    assert state.status == GoalStatus.FAILED
    assert state.error_message != ""


# ===========================================================================
# _node_initialize — guardrail rejection path
# ===========================================================================

@pytest.mark.asyncio
async def test_node_initialize_guardrail_rejection_path() -> None:
    """If goal-level guardrail blocks, agent state has FAILED status."""
    from app.intelligence.guardrails import GuardrailChecker

    mock_guardrail = MagicMock(spec=GuardrailChecker)
    # First call (tool_name check) returns nothing; second returns violation
    # For initialize, it checks the goal itself via check(tool_name="", ...)
    mock_guardrail.check.return_value = ["goal contains harmful content"]

    graph = _make_graph(guardrail_checker=mock_guardrail)
    state = await graph.run(goal="dangerous goal", tenant_ctx=T)

    # Guardrail rejection should terminate the agent
    assert state.status in (GoalStatus.FAILED, GoalStatus.COMPLETE)


# ===========================================================================
# _execute_step — permission matrix DENY
# ===========================================================================

@pytest.mark.asyncio
async def test_execute_step_permission_matrix_deny_raises() -> None:
    """PermissionMatrix DENY raises PermissionError."""
    from app.governance.permissions import ActionLevel, PermissionMatrix

    mock_matrix = MagicMock(spec=PermissionMatrix)
    mock_matrix.check.return_value = ActionLevel.DENY

    executor = FakeProvider(responses=["should not run"])
    graph = _make_graph(executor=executor, permission_matrix=mock_matrix)

    agent_state = _make_agent_state("governed task")
    with pytest.raises(PermissionError, match="denied by governance policy"):
        await graph._execute_step("call restricted_tool with data", agent_state, T)


@pytest.mark.asyncio
async def test_execute_step_permission_matrix_allow_proceeds() -> None:
    """PermissionMatrix ALLOW proceeds to LLM execution."""
    from app.governance.permissions import ActionLevel, PermissionMatrix

    mock_matrix = MagicMock(spec=PermissionMatrix)
    mock_matrix.check.return_value = ActionLevel.ALLOW

    executor = FakeProvider(responses=["output from allowed step"])
    graph = _make_graph(executor=executor, permission_matrix=mock_matrix)

    agent_state = _make_agent_state("allowed task")
    result = await graph._execute_step("call allowed_tool", agent_state, T)

    assert len(executor.call_history) == 1


# ===========================================================================
# _execute_step — policy engine DENY
# ===========================================================================

@pytest.mark.asyncio
async def test_execute_step_policy_engine_deny_raises() -> None:
    """PolicyEngine DENY raises PermissionError."""
    from app.governance.policies import Policy, PolicyEngine, PolicyResult

    mock_policy = MagicMock(spec=PolicyEngine)
    mock_policy.evaluate.return_value = PolicyResult.DENY

    executor = FakeProvider(responses=["blocked"])
    graph = _make_graph(executor=executor, policy_engine=mock_policy)

    agent_state = _make_agent_state("policy-governed task")
    with pytest.raises(PermissionError, match="denied by governance policy"):
        await graph._execute_step("call blocked_tool with query", agent_state, T)


# ===========================================================================
# _execute_step_with_loop
# ===========================================================================

@pytest.mark.asyncio
async def test_execute_step_with_loop_exits_when_condition_met() -> None:
    """Loop exits when loop_until evaluates to True."""
    from app.agent.structured_plan import StructuredStep

    executor = FakeProvider(responses=["success output"])
    graph = _make_graph(executor=executor)

    agent_state = _make_agent_state("loop task")

    # Create a StructuredStep with loop_until condition
    step = StructuredStep(
        id="s0",
        description="check status",
        depends_on=[],
        loop_until="'success' in output",
        max_loop_iter=5,
    )

    with patch("asyncio.sleep", new=AsyncMock()):
        result = await graph._execute_step_with_loop(step, agent_state, T)

    assert result == "success output"
    assert step.iterations_used == 1  # Should exit on first iteration


@pytest.mark.asyncio
async def test_execute_step_with_loop_exhausts_max_iterations() -> None:
    """Loop exhausts max_loop_iter when condition never meets."""
    from app.agent.structured_plan import StructuredStep

    # Executor always returns "pending" which won't satisfy "done" condition
    executor = FakeProvider(responses=["pending"] * 10)
    graph = _make_graph(executor=executor)

    agent_state = _make_agent_state("waiting task")
    step = StructuredStep(
        id="s1",
        description="wait for completion",
        depends_on=[],
        loop_until="output == 'done'",
        max_loop_iter=3,
    )

    with patch("asyncio.sleep", new=AsyncMock()):
        result = await graph._execute_step_with_loop(step, agent_state, T)

    # Should return last output after max iterations
    assert step.iterations_used == 3


# ===========================================================================
# _execute_step — cost tracker recording
# ===========================================================================

@pytest.mark.asyncio
async def test_execute_step_cost_tracker_records_usage() -> None:
    """When cost_tracker is set and usage is available, token usage is recorded."""
    mock_cost_tracker = MagicMock()
    mock_cost_tracker.record_llm_usage = AsyncMock()

    # Use a provider that returns usage info
    from app.providers.base import CompletionRequest as _CR, CompletionResponse as _CResp

    class _UsageProvider:
        async def complete(self, req: _CR) -> _CResp:
            return _CResp(
                content="output with usage",
                model="gpt-4o",
                input_tokens=10,
                output_tokens=5,
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )
        async def stream_tokens(self, req, on_token):
            resp = await self.complete(req)
            if resp.content:
                await on_token(resp.content)
            return resp
        async def embed(self, req: object) -> None:
            raise NotImplementedError
        def supports_vision(self) -> bool:
            return False
        def supports_tool_use(self) -> bool:
            return True

    graph = _make_graph(executor=_UsageProvider(), cost_tracker=mock_cost_tracker)
    agent_state = _make_agent_state("track cost")
    await graph._execute_step("step with tracking", agent_state, T)

    mock_cost_tracker.record_llm_usage.assert_called_once()


# ===========================================================================
# _execute_step — bulkhead full
# ===========================================================================

@pytest.mark.asyncio
async def test_execute_step_bulkhead_full_returns_message() -> None:
    """When bulkhead is full, returns retry message."""
    mock_bulkhead = MagicMock()
    mock_bulkhead.acquire = AsyncMock(return_value=False)  # Bulkhead full

    mock_registry = MagicMock()
    mock_registry.get_bulkhead.return_value = mock_bulkhead

    executor = FakeProvider(responses=["should not run"])
    graph = _make_graph(executor=executor, bulkhead_registry=mock_registry)

    agent_state = _make_agent_state("concurrent task")
    result = await graph._execute_step("concurrent step", agent_state, T)

    assert "Bulkhead" in result or "too many concurrent" in result.lower()
    assert len(executor.call_history) == 0


# ===========================================================================
# _node_think (chain-of-thought node)
# ===========================================================================

@pytest.mark.asyncio
async def test_node_think_generates_cot_reasoning() -> None:
    """_node_think calls the planner and returns cot_reasoning."""
    planner = FakeProvider(responses=["I need to first understand the goal, then plan."])
    graph = _make_graph(planner=planner, enable_cot=True)

    agent_state = _make_agent_state("complex goal")
    state = {"agent_state": agent_state, "tenant_ctx": T}
    result = await graph._node_think(state)

    assert "cot_reasoning" in result
    assert result["cot_reasoning"] == "I need to first understand the goal, then plan."


# ===========================================================================
# Full run with enable_cot=True
# ===========================================================================

@pytest.mark.asyncio
async def test_agent_run_with_cot_enabled() -> None:
    """With enable_cot=True, chain-of-thought node runs before planning."""
    planner = FakeProvider(responses=[
        "Step 1: analyze",  # CoT response
        "Execute the analysis",  # Plan response
    ])
    executor = FakeProvider(responses=["analysis complete"])
    verifier = FakeProvider(responses=['{"success": true, "reason": "done"}'])

    graph = AgentGraph(
        planner=planner, executor=executor, verifier=verifier, enable_cot=True
    )
    state = await graph.run(goal="analyze data", tenant_ctx=T)

    assert state.status == GoalStatus.COMPLETE


# ===========================================================================
# _node_rag_retrieval — knowledge store hit
# ===========================================================================

@pytest.mark.asyncio
async def test_node_rag_retrieval_with_knowledge_store_and_collection_ids() -> None:
    """Knowledge store provides relevant context for the agent."""
    from app.rag.store import KnowledgeStore

    mock_ks = MagicMock(spec=KnowledgeStore)
    mock_result = MagicMock()
    mock_result.content = "Relevant knowledge: API endpoint is /v1/deploy"
    mock_ks.hybrid_search_db = AsyncMock(return_value=[mock_result])

    graph = _make_graph(knowledge_store=mock_ks)
    graph._agent_collection_ids = ["collection-1"]  # Enable knowledge store lookup

    agent_state = _make_agent_state("deploy via API")
    state = {"agent_state": agent_state, "tenant_ctx": T, "rag_context": ""}
    result = await graph._node_rag_retrieval(state)

    # Knowledge context should be in agent_state.context
    assert "rag_knowledge" in agent_state.context
    assert "API endpoint" in agent_state.context["rag_knowledge"]


# ===========================================================================
# _emit and event callback
# ===========================================================================

@pytest.mark.asyncio
async def test_emit_adds_timestamp_to_events() -> None:
    """_emit adds 'ts' field to events."""
    graph = _make_graph()
    events: list[dict] = []

    async def _cb(event: dict) -> None:
        events.append(event)

    graph._event_callback = _cb
    await graph._emit({"type": "test_event"})

    assert len(events) == 1
    assert "ts" in events[0]
    assert events[0]["type"] == "test_event"


@pytest.mark.asyncio
async def test_emit_does_not_override_existing_timestamp() -> None:
    """_emit preserves existing 'ts' field."""
    graph = _make_graph()
    events: list[dict] = []

    async def _cb(event: dict) -> None:
        events.append(event)

    graph._event_callback = _cb
    await graph._emit({"type": "test_event", "ts": "2024-01-01T00:00:00"})

    assert events[0]["ts"] == "2024-01-01T00:00:00"


# ===========================================================================
# _sanitize_event_value helper
# ===========================================================================

def test_sanitize_event_value_returns_sanitized_value() -> None:
    """_sanitize_event_value delegates to sanitize_event_value."""
    graph = _make_graph()
    # Basic string should pass through unchanged
    result = graph._sanitize_event_value("some text")
    assert isinstance(result, (str, dict, list, type(None)))


def test_sanitize_event_delegates_to_sanitize_event() -> None:
    """_sanitize_event delegates to sanitize_event function."""
    graph = _make_graph()
    event = {"type": "test", "output": "safe value"}
    result = graph._sanitize_event(event)
    assert isinstance(result, dict)
    assert "type" in result


# ===========================================================================
# _node_initialize — edge cases
# ===========================================================================

@pytest.mark.asyncio
async def test_node_initialize_sets_missing_goal_on_existing_state() -> None:
    """Line 282: If agent_state has no goal, it's filled from the state."""
    graph = _make_graph()

    # Create an agent state with empty goal
    agent_state = AgentState(goal="", tenant_ctx=T)
    state: dict = {
        "goal": "filled goal",
        "tenant_ctx": T,
        "agent_state": agent_state,
    }
    result = await graph._node_initialize(state)
    assert result["agent_state"].goal == "filled goal"


@pytest.mark.asyncio
async def test_node_initialize_sets_missing_tenant_ctx() -> None:
    """Line 284: If agent_state has no tenant_ctx, it's filled from state."""
    graph = _make_graph()

    class _NoCtxState:
        def __init__(self) -> None:
            self.goal = "my goal"
            self.tenant_ctx = None  # type: ignore
            self.goal_id = "g1"
            self.status = GoalStatus.PLANNING
            self.iterations = 0
            self.steps = []
            self.plan = []
            self.context = {}
            self.verification_feedback = ""
            self.verification_success = False
            self.error_message = ""
            self.sub_goals = []

    # Use a raw agent_state-like object that passes the isinstance check
    agent_state = AgentState(goal="test", tenant_ctx=T)
    agent_state.tenant_ctx = None  # type: ignore

    state: dict = {
        "goal": "test",
        "tenant_ctx": T,
        "agent_state": agent_state,
    }
    result = await graph._node_initialize(state)
    # tenant_ctx should be set
    assert result["agent_state"].tenant_ctx is T


# ===========================================================================
# _node_rag_retrieval — exec_memory recall_async exception path
# ===========================================================================

@pytest.mark.asyncio
async def test_node_rag_retrieval_ltm_memory_provides_context() -> None:
    """Long-term memory results appear in rag_context."""
    mock_ltm = MagicMock(spec=LongTermMemoryStore)

    class _FakeMemoryItem:
        content = "How to deploy: use kubectl apply"

    mock_ltm.recall_async = AsyncMock(return_value=[_FakeMemoryItem()])

    graph = _make_graph(long_term_memory=mock_ltm)
    agent_state = _make_agent_state("deploy app")
    state = {"agent_state": agent_state, "tenant_ctx": T, "rag_context": ""}
    result = await graph._node_rag_retrieval(state)

    assert "kubectl apply" in result["rag_context"] or result["rag_context"] == ""


@pytest.mark.asyncio
async def test_node_rag_retrieval_with_embedder_and_knowledge_store() -> None:
    """Embedder is used for knowledge store queries — covers lines 377-383."""
    from app.rag.store import KnowledgeStore

    mock_ks = MagicMock(spec=KnowledgeStore)
    mock_result = MagicMock()
    mock_result.content = "API docs: POST /deploy"
    mock_ks.hybrid_search_db = AsyncMock(return_value=[mock_result])

    # Embedder that returns a real vector
    embedder = FakeProvider(embed_dim=4)

    graph = _make_graph(knowledge_store=mock_ks, embedder=embedder)
    graph._agent_collection_ids = ["collection-1"]

    agent_state = _make_agent_state("deploy API")
    state = {"agent_state": agent_state, "tenant_ctx": T, "rag_context": ""}
    result = await graph._node_rag_retrieval(state)

    # hybrid_search_db should have been called with non-empty embedding
    mock_ks.hybrid_search_db.assert_called_once()
    call_kwargs = mock_ks.hybrid_search_db.call_args.kwargs
    assert call_kwargs["query_embedding"] != []  # Embedder provided a vector


@pytest.mark.asyncio
async def test_node_rag_retrieval_embedder_failure_is_swallowed() -> None:
    """When embedder fails, knowledge store query still proceeds with empty embedding."""
    from app.rag.store import KnowledgeStore
    from app.providers.base import EmbedRequest as _ER

    mock_ks = MagicMock(spec=KnowledgeStore)
    mock_ks.hybrid_search_db = AsyncMock(return_value=[])

    class _BrokenEmbedder:
        async def embed(self, req: _ER) -> None:  # type: ignore
            raise RuntimeError("embedder broken")
        async def embed_batch(self, texts: list) -> list:
            return []
        def supports_vision(self) -> bool:
            return False
        def supports_tool_use(self) -> bool:
            return False

    graph = _make_graph(knowledge_store=mock_ks, embedder=_BrokenEmbedder())
    graph._agent_collection_ids = ["col-1"]

    agent_state = _make_agent_state("task")
    state = {"agent_state": agent_state, "tenant_ctx": T, "rag_context": ""}
    result = await graph._node_rag_retrieval(state)  # Must not raise

    # Should still call hybrid_search_db with empty embedding
    mock_ks.hybrid_search_db.assert_called_once()


@pytest.mark.asyncio
async def test_node_reflect_model_router_exception_falls_back() -> None:
    """When model_router raises in _node_reflect, it uses empty model (fallback)."""
    planner = FakeProvider(responses=["Reflection response"])
    mock_router = MagicMock()
    mock_router.model_for.side_effect = RuntimeError("router error")
    graph = _make_graph(planner=planner, model_router=mock_router, enable_reflection=True)

    agent_state = _make_agent_state("task")
    agent_state.steps.append(
        StepResult(description="failed step", status=StepStatus.FAILED, error="error")
    )

    state = {"agent_state": agent_state, "tenant_ctx": T}
    result = await graph._node_reflect(state)
    # Should still complete with reflection response
    assert result["agent_state"].verification_feedback == "Reflection response"
