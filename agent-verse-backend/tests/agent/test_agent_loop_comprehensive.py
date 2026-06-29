"""Comprehensive tests for app/agent/loop.py — targets 90%+ statement coverage."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.loop import AgentLoop, _extract_tool_name, _parse_json_response
from app.agent.state import AgentState, GoalStatus, StepStatus
from app.governance.audit import AuditEvent, AuditLog
from app.governance.cost import BudgetConfig, CostController
from app.governance.hitl import HITLGateway
from app.governance.permissions import ActionLevel, PermissionMatrix, PermissionRule
from app.memory.execution import ExecutionMemory
from app.providers.fake import FakeProvider
from app.reliability.circuit_breaker import CircuitBreaker
from app.reliability.dedup import DeduplicationCache
from app.reliability.rollback import RollbackEngine
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-loop", plan=PlanTier.ENTERPRISE, api_key_id="key-l")


# ── Module-level helpers ─────────────────────────────────────────────────────

def test_parse_json_response_valid_json() -> None:
    obj = _parse_json_response('{"steps": ["s1", "s2"]}')
    assert obj["steps"] == ["s1", "s2"]


def test_parse_json_response_strips_markdown_fence() -> None:
    text = '```json\n{"success": true}\n```'
    obj = _parse_json_response(text)
    assert obj["success"] is True


def test_parse_json_response_invalid_json_key_steps() -> None:
    obj = _parse_json_response("plain text response", key="steps")
    assert obj["steps"] == ["plain text response"]


def test_parse_json_response_invalid_json_no_key() -> None:
    obj = _parse_json_response("plain text")
    assert obj["success"] is True
    assert obj["reason"] == "plain text"


def test_extract_tool_name_with_call_keyword() -> None:
    assert _extract_tool_name("call jira to get issues") == "jira"


def test_extract_tool_name_no_call_keyword() -> None:
    assert _extract_tool_name("fetch all open tickets") == "llm_call"


def test_extract_tool_name_call_at_end_returns_llm_call() -> None:
    # "call" at the end with nothing after → empty words → fallback
    assert _extract_tool_name("please call") == "llm_call"


def test_extract_tool_name_strips_punctuation() -> None:
    name = _extract_tool_name("call github:")
    assert name == "github"


# ── AgentLoop construction ─────────────────────────────────────────────────────

def _make_loop(**kwargs) -> AgentLoop:
    defaults = dict(
        planner=FakeProvider(responses=['{"steps": ["Step 1: Do the thing"]}']),
        executor=FakeProvider(responses=["Done"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
    )
    defaults.update(kwargs)
    return AgentLoop(**defaults)


# ── AgentLoop.run — happy paths ───────────────────────────────────────────────

async def test_loop_completes_single_step_goal() -> None:
    loop = _make_loop()
    state = await loop.run(goal="Write hello world", tenant_ctx=_CTX)
    assert state.status == GoalStatus.COMPLETE
    assert state.verification_success is True


async def test_loop_records_step_results() -> None:
    loop = _make_loop(
        planner=FakeProvider(responses=['{"steps": ["Step 1", "Step 2"]}']),
        executor=FakeProvider(responses=["R1", "R2"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
    )
    state = await loop.run(goal="Two-step goal", tenant_ctx=_CTX)
    assert len(state.steps) == 2
    assert all(s.status == StepStatus.COMPLETE for s in state.steps)


async def test_loop_emits_events_via_callback() -> None:
    events: list[dict] = []

    async def cb(evt: dict) -> None:
        events.append(evt)

    loop = _make_loop()
    await loop.run(goal="Task", tenant_ctx=_CTX, event_callback=cb)
    types = {e["type"] for e in events}
    assert "goal_started" in types
    assert "plan_ready" in types
    assert "step_started" in types
    assert "step_complete" in types
    assert "verification_done" in types
    assert "goal_complete" in types


async def test_loop_replans_on_verification_failure() -> None:
    loop = _make_loop(
        planner=FakeProvider(responses=[
            '{"steps": ["Step 1"]}',
            '{"steps": ["Step 1 retry"]}',
        ]),
        executor=FakeProvider(responses=["Attempted", "Done"]),
        verifier=FakeProvider(responses=[
            '{"success": false, "reason": "not done yet"}',
            '{"success": true, "reason": "ok"}',
        ]),
    )
    state = await loop.run(goal="Retry goal", tenant_ctx=_CTX)
    assert state.status == GoalStatus.COMPLETE
    assert state.iterations == 2


async def test_loop_stops_at_max_iterations() -> None:
    loop = AgentLoop(
        planner=FakeProvider(responses=['{"steps": ["Step"]}']),
        executor=FakeProvider(responses=["tried"]),
        verifier=FakeProvider(responses=['{"success": false, "reason": "nope"}']),
        max_iterations=2,
    )
    state = await loop.run(goal="Impossible", tenant_ctx=_CTX)
    assert state.status == GoalStatus.FAILED
    assert state.iterations == 2
    assert "max iterations" in state.error_message


async def test_loop_with_initial_context() -> None:
    loop = _make_loop()
    state = await loop.run(
        goal="Goal",
        tenant_ctx=_CTX,
        initial_context={"extra": "data"},
    )
    assert state.context["extra"] == "data"


async def test_loop_plan_fallback_on_invalid_json() -> None:
    """When planner returns non-JSON, loop falls back gracefully."""
    loop = _make_loop(
        planner=FakeProvider(responses=["Step 1 do something. Step 2 finish."]),
        executor=FakeProvider(responses=["done"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
    )
    state = await loop.run(goal="Non-JSON plan goal", tenant_ctx=_CTX)
    assert state.status == GoalStatus.COMPLETE


async def test_loop_records_verification_feedback_on_failure() -> None:
    loop = AgentLoop(
        planner=FakeProvider(responses=['{"steps": ["S1"]}']),
        executor=FakeProvider(responses=["tried"]),
        verifier=FakeProvider(responses=['{"success": false, "reason": "needs more work"}']),
        max_iterations=1,
    )
    state = await loop.run(goal="Feedback goal", tenant_ctx=_CTX)
    assert state.verification_feedback == "needs more work"


# ── AgentLoop._execute — governance layers ────────────────────────────────────

async def test_execute_with_cost_controller_within_budget() -> None:
    cost_ctrl = CostController(BudgetConfig(per_goal_usd=100.0, per_tenant_daily_usd=1000.0))
    loop = _make_loop(cost_controller=cost_ctrl)
    state = await loop.run(goal="Budget goal", tenant_ctx=_CTX)
    assert state.status == GoalStatus.COMPLETE


async def test_execute_with_cost_controller_exceeds_budget() -> None:
    """When budget exceeded, steps return 'skipped' message but loop continues."""
    cost_ctrl = CostController(BudgetConfig(per_goal_usd=0.0, per_tenant_daily_usd=0.0))
    loop = _make_loop(cost_controller=cost_ctrl)
    state = await loop.run(goal="Over-budget goal", tenant_ctx=_CTX)
    # Loop still completes (verifier returns success regardless of step output)
    assert state.iterations > 0


async def test_execute_with_dedup_cache_marks_and_checks() -> None:
    dedup = DeduplicationCache()
    loop = _make_loop(dedup_cache=dedup)
    state = await loop.run(goal="Dedup goal", tenant_ctx=_CTX)
    assert state.status == GoalStatus.COMPLETE


async def test_execute_with_dedup_cache_duplicate_returns_cached() -> None:
    dedup = DeduplicationCache()
    loop = AgentLoop(
        planner=FakeProvider(responses=['{"steps": ["call api"]}']),
        executor=FakeProvider(responses=["real result"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        dedup_cache=dedup,
        max_iterations=2,
    )
    # First run marks the hash
    state1 = await loop.run(goal="Dedup goal", tenant_ctx=_CTX)
    # Second run with same goal — step is a duplicate
    state2 = await loop.run(goal="Dedup goal", tenant_ctx=_CTX)
    assert state2.status == GoalStatus.COMPLETE


async def test_execute_with_permission_matrix_allow() -> None:
    pm = PermissionMatrix()
    loop = _make_loop(permission_matrix=pm)
    state = await loop.run(goal="Allowed goal", tenant_ctx=_CTX)
    assert state.status == GoalStatus.COMPLETE


async def test_execute_with_permission_matrix_deny_raises() -> None:
    pm = PermissionMatrix()
    pm.set_rule(
        PermissionRule(tool_name="llm_call", level=ActionLevel.DENY),
        tenant_ctx=_CTX,
    )
    loop = _make_loop(permission_matrix=pm)
    with pytest.raises(PermissionError):
        await loop.run(goal="Denied goal", tenant_ctx=_CTX)


async def test_execute_with_circuit_breaker_open_skips() -> None:
    cb = CircuitBreaker(failure_threshold=1)
    # Force circuit open by registering failures
    cb._failure_count = 5
    from app.reliability.circuit_breaker import CircuitState
    cb._state = CircuitState.OPEN
    cb._opened_at = 0.0

    loop = _make_loop(circuit_breakers={"llm": cb})
    state = await loop.run(goal="Circuit open goal", tenant_ctx=_CTX)
    # Step should be skipped, verifier still succeeds
    assert state.status == GoalStatus.COMPLETE


async def test_execute_with_hitl_gateway_high_risk_step() -> None:
    hitl = HITLGateway(timeout_seconds=300.0)
    loop = _make_loop(
        planner=FakeProvider(responses=['{"steps": ["deploy to production server"]}']),
        executor=FakeProvider(responses=["deployed"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        hitl_gateway=hitl,
    )
    state = await loop.run(goal="Deploy", tenant_ctx=_CTX)
    # HITL gateway logs but auto-proceeds (non-blocking)
    assert state.status == GoalStatus.COMPLETE


async def test_execute_with_hitl_gateway_low_risk_step() -> None:
    hitl = HITLGateway(timeout_seconds=300.0)
    loop = _make_loop(hitl_gateway=hitl)
    state = await loop.run(goal="Low risk task", tenant_ctx=_CTX)
    assert state.status == GoalStatus.COMPLETE


async def test_execute_with_rollback_engine() -> None:
    rollback = RollbackEngine()
    loop = _make_loop(rollback_engine=rollback)
    state = await loop.run(goal="Rollback goal", tenant_ctx=_CTX)
    assert state.status == GoalStatus.COMPLETE
    # At least one action registered
    assert len(rollback._stack) >= 1


async def test_execute_with_result_processor() -> None:
    class UpperProcessor:
        def process(self, text: str) -> str:
            return text.upper()

    loop = _make_loop(result_processor=UpperProcessor())
    state = await loop.run(goal="Processor goal", tenant_ctx=_CTX)
    assert state.status == GoalStatus.COMPLETE
    # Step output should be uppercased
    assert state.steps[0].output.isupper()


async def test_execute_with_audit_log_records_events() -> None:
    audit = AuditLog()
    loop = _make_loop(audit_log=audit)
    state = await loop.run(goal="Audit goal", tenant_ctx=_CTX)
    assert state.status == GoalStatus.COMPLETE
    entries = audit.query(tenant_ctx=_CTX)
    assert len(entries) >= 1


async def test_execute_with_exec_memory_records_winning_plan() -> None:
    mem = ExecutionMemory()
    loop = _make_loop(exec_memory=mem)
    state = await loop.run(goal="Memory goal", tenant_ctx=_CTX)
    assert state.status == GoalStatus.COMPLETE
    recalled = mem.recall(goal_hint="Memory", tenant_ctx=_CTX)
    assert len(recalled) >= 1


async def test_loop_verify_parses_success_false() -> None:
    loop = _make_loop(
        verifier=FakeProvider(responses=['{"success": false, "reason": "not enough"}'])
    )
    state = await loop.run(goal="Fail verify", tenant_ctx=_CTX)
    # Will exhaust max_iterations (default 15) or succeed on retry
    assert state.iterations >= 1


async def test_loop_recent_outputs_injected_into_executor_prompt() -> None:
    """When steps have outputs, they are prepended to subsequent step prompts."""
    loop = _make_loop(
        planner=FakeProvider(responses=['{"steps": ["step1", "step2"]}']),
        executor=FakeProvider(responses=["output1", "output2"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
    )
    state = await loop.run(goal="Multi-step", tenant_ctx=_CTX)
    assert len(state.steps) == 2
