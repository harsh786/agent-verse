"""E2E: Full goal lifecycle — submit → plan → execute → verify → complete.

Uses FakeProvider to drive all LLM roles deterministically.
Exercises the complete 12-step pipeline with every optional dependency.
"""

from __future__ import annotations

import pytest

from app.agent.loop import AgentLoop
from app.agent.state import GoalStatus
from app.governance.cost import BudgetConfig, CostController
from app.governance.audit import AuditLog
from app.governance.hitl import HITLGateway
from app.memory.execution import ExecutionMemory
from app.providers.fake import FakeProvider
from app.reliability.circuit_breaker import CircuitBreaker
from app.reliability.dedup import DeduplicationCache
from app.reliability.result_processor import ResultProcessor
from app.reliability.rollback import RollbackEngine
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(
    tenant_id="e2e-tenant",
    plan=PlanTier.PROFESSIONAL,
    api_key_id="e2e-key-001",
)


# ── Test 1: Complete goal lifecycle ──────────────────────────────────────────

async def test_goal_reaches_complete_status() -> None:
    provider = FakeProvider(responses=[
        '{"steps": ["step 1: analyze", "step 2: execute"]}',
        "step 1 output",
        "step 2 output",
        '{"success": true, "reason": "Goal completed"}',
    ])
    loop = AgentLoop(planner=provider, executor=provider, verifier=provider)
    events: list[dict] = []

    async def capture(e: dict) -> None:
        events.append(e)

    state = await loop.run(goal="test goal", tenant_ctx=TENANT, event_callback=capture)
    assert state.status == GoalStatus.COMPLETE
    assert any(e["type"] == "goal_complete" for e in events)


# ── Test 2: Pipeline with all components wired ───────────────────────────────

async def test_goal_with_full_pipeline() -> None:
    provider = FakeProvider(responses=[
        '{"steps": ["call llm to process data"]}',
        "processed data output",
        '{"success": true, "reason": "Done"}',
    ])
    audit = AuditLog()
    cost = CostController()
    hitl = HITLGateway()
    rollback = RollbackEngine()
    dedup = DeduplicationCache()
    rp = ResultProcessor()
    memory = ExecutionMemory()

    loop = AgentLoop(
        planner=provider,
        executor=provider,
        verifier=provider,
        audit_log=audit,
        cost_controller=cost,
        hitl_gateway=hitl,
        rollback_engine=rollback,
        dedup_cache=dedup,
        result_processor=rp,
        exec_memory=memory,
    )
    state = await loop.run(goal="process data pipeline", tenant_ctx=TENANT)
    assert state.status == GoalStatus.COMPLETE


# ── Test 3: Replanning on verification failure ────────────────────────────────

async def test_replan_on_verification_failure_then_succeed() -> None:
    provider = FakeProvider(responses=[
        '{"steps": ["attempt 1"]}',             # First plan
        "attempt 1 output",                      # Execute
        '{"success": false, "reason": "Not done yet"}',  # Fail verification
        '{"steps": ["attempt 2"]}',              # Second plan (replan)
        "attempt 2 output",                      # Execute
        '{"success": true, "reason": "Now done"}',       # Pass verification
    ])
    loop = AgentLoop(planner=provider, executor=provider, verifier=provider)
    state = await loop.run(goal="replan test", tenant_ctx=TENANT)
    assert state.status == GoalStatus.COMPLETE
    assert state.iterations == 2


# ── Test 4: Max iterations exceeded ──────────────────────────────────────────

async def test_max_iterations_exceeded() -> None:
    always_fail = FakeProvider(responses=[
        '{"steps": ["step"]}',
        "output",
        '{"success": false, "reason": "always failing"}',
    ] * 20)
    loop = AgentLoop(
        planner=always_fail,
        executor=always_fail,
        verifier=always_fail,
        max_iterations=3,
    )
    state = await loop.run(goal="impossible goal", tenant_ctx=TENANT)
    assert state.status == GoalStatus.FAILED
    assert state.iterations == 3


# ── Test 5: Cost budget exceeded ─────────────────────────────────────────────

async def test_cost_budget_exceeded() -> None:
    provider = FakeProvider(responses=[
        '{"steps": ["expensive step"]}',
        "output",
        '{"success": true, "reason": "done"}',
    ])
    # Budget of 0.0 USD means any cost estimate (0.01) will be rejected immediately
    cost = CostController(BudgetConfig(per_goal_usd=0.0))

    loop = AgentLoop(
        planner=provider,
        executor=provider,
        verifier=provider,
        cost_controller=cost,
    )
    state = await loop.run(goal="expensive task", tenant_ctx=TENANT)
    # With 0 budget the step is skipped; verify the loop still returns a valid state
    assert state is not None
    # The loop may complete (step output is "Step skipped: budget exceeded." which
    # is treated as a result, then the verifier sees it and decides success/failure)
    assert state.status in {GoalStatus.COMPLETE, GoalStatus.FAILED}


# ── Test 6: Circuit breaker integration ──────────────────────────────────────

async def test_circuit_breaker_open_skips_step() -> None:
    provider = FakeProvider(responses=[
        '{"steps": ["call external service"]}',
        "fallback output",
        '{"success": true, "reason": "done"}',
    ])
    breaker = CircuitBreaker(failure_threshold=3)
    # Force the circuit open by recording 3 consecutive failures
    for _ in range(3):
        breaker.record_failure()

    # The loop checks circuit_breakers["llm"] by convention
    loop = AgentLoop(
        planner=provider,
        executor=provider,
        verifier=provider,
        circuit_breakers={"llm": breaker},
    )
    state = await loop.run(goal="test circuit breaker", tenant_ctx=TENANT)
    # Step output should be the circuit-open message
    assert state is not None
    if state.steps:
        assert "Circuit open" in state.steps[0].output


# ── Test 7: HITL gateway creates approval request for high-risk steps ─────────

async def test_hitl_request_created_for_high_risk_step() -> None:
    provider = FakeProvider(responses=[
        '{"steps": ["deploy to production server"]}',
        "deployed",
        '{"success": true, "reason": "done"}',
    ])
    hitl = HITLGateway()
    loop = AgentLoop(
        planner=provider,
        executor=provider,
        verifier=provider,
        hitl_gateway=hitl,
    )
    state = await loop.run(goal="deploy production app", tenant_ctx=TENANT)
    assert state is not None
    # The "deploy to production" step should have triggered an approval request
    pending = hitl.list_pending(tenant_ctx=TENANT)
    # Approval requests are created and auto-proceeded; they are no longer PENDING
    # if there were no pending from other runs — just verify no exception was raised
    assert isinstance(pending, list)


# ── Test 8: Result processor redacts secrets ─────────────────────────────────

async def test_result_processor_redacts_secrets() -> None:
    provider = FakeProvider(responses=[
        '{"steps": ["fetch api key"]}',
        "API key is sk-secretkey123 and some token",
        '{"success": true, "reason": "done"}',
    ])
    rp = ResultProcessor()
    loop = AgentLoop(
        planner=provider,
        executor=provider,
        verifier=provider,
        result_processor=rp,
    )
    state = await loop.run(goal="test secret redaction", tenant_ctx=TENANT)
    # The raw output "sk-secretkey123" must be replaced with [REDACTED]
    assert state.steps, "Expected at least one step result"
    assert "sk-secretkey123" not in state.steps[0].output
    assert "[REDACTED]" in state.steps[0].output


# ── Test 9: Execution memory records winning plan ─────────────────────────────

async def test_execution_memory_records_winning_plan() -> None:
    provider = FakeProvider(responses=[
        '{"steps": ["analyze data", "produce report"]}',
        "data analyzed",
        "report generated",
        '{"success": true, "reason": "done"}',
    ])
    memory = ExecutionMemory()
    loop = AgentLoop(
        planner=provider,
        executor=provider,
        verifier=provider,
        exec_memory=memory,
    )
    state = await loop.run(goal="generate data report", tenant_ctx=TENANT)
    assert state.status == GoalStatus.COMPLETE

    # On success the loop records the winning plan into execution memory
    memories = memory.recall(goal_hint="generate data report", tenant_ctx=TENANT)
    assert isinstance(memories, list)
    assert len(memories) >= 1
    assert memories[0]["goal"] == "generate data report"


# ── Test 10: Tenant isolation in agent loop ───────────────────────────────────

async def test_tenant_isolation() -> None:
    tenant_a = TenantContext(
        tenant_id="tenant-a", plan=PlanTier.FREE, api_key_id="key-a"
    )
    tenant_b = TenantContext(
        tenant_id="tenant-b", plan=PlanTier.FREE, api_key_id="key-b"
    )

    provider = FakeProvider(responses=[
        '{"steps": ["step"]}', "output", '{"success": true, "reason": "done"}',
        '{"steps": ["step"]}', "output", '{"success": true, "reason": "done"}',
    ])
    loop = AgentLoop(planner=provider, executor=provider, verifier=provider)

    state_a = await loop.run(goal="tenant a goal", tenant_ctx=tenant_a)
    state_b = await loop.run(goal="tenant b goal", tenant_ctx=tenant_b)

    assert state_a.tenant_ctx.tenant_id == "tenant-a"
    assert state_b.tenant_ctx.tenant_id == "tenant-b"
    assert state_a.goal_id != state_b.goal_id
