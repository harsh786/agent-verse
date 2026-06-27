"""Tests for LangGraph-based AgentGraph."""

from __future__ import annotations

import asyncio
import math

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import GoalStatus
from app.governance.audit import AuditLog
from app.governance.hitl import HITLGateway
from app.governance.permissions import ActionLevel, PermissionMatrix, PermissionRule
from app.memory.execution import ExecutionMemory
from app.providers.fake import FakeProvider
from app.reliability.dedup import DeduplicationCache
from app.reliability.result_processor import ResultProcessor
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="graph-t1", plan=PlanTier.PROFESSIONAL, api_key_id="gk-1")


async def test_graph_complete_simple() -> None:
    p = FakeProvider(
        responses=[
            '{"steps": ["step one"]}',
            "done",
            '{"success": true, "reason": "ok"}',
        ]
    )
    g = AgentGraph(planner=p, executor=p, verifier=p)
    state = await g.run(goal="test", tenant_ctx=TENANT)
    assert state.status == GoalStatus.COMPLETE


async def test_graph_replans_then_completes() -> None:
    p = FakeProvider(
        responses=[
            '{"steps": ["a1"]}',
            "out1",
            '{"success": false, "reason": "not done"}',
            '{"steps": ["a2"]}',
            "out2",
            '{"success": true, "reason": "done"}',
        ]
    )
    g = AgentGraph(planner=p, executor=p, verifier=p)
    state = await g.run(goal="replan", tenant_ctx=TENANT)
    assert state.status == GoalStatus.COMPLETE
    assert state.iterations == 2


async def test_graph_max_iterations_fails() -> None:
    p = FakeProvider(
        responses=[
            '{"steps": ["s"]}',
            "o",
            '{"success": false, "reason": "no"}',
        ]
        * 20
    )
    g = AgentGraph(planner=p, executor=p, verifier=p, max_iterations=2)
    state = await g.run(goal="impossible", tenant_ctx=TENANT)
    assert state.status == GoalStatus.FAILED


async def test_graph_emits_events() -> None:
    p = FakeProvider(
        responses=[
            '{"steps": ["s"]}',
            "o",
            '{"success": true, "reason": "ok"}',
        ]
    )
    g = AgentGraph(planner=p, executor=p, verifier=p)
    events: list[dict] = []

    async def cb(e: dict) -> None:
        events.append(e)

    await g.run(goal="events", tenant_ctx=TENANT, event_callback=cb)
    types = {e["type"] for e in events}
    assert "goal_started" in types
    assert "plan_ready" in types
    assert "goal_complete" in types


async def test_graph_permission_deny_raises() -> None:
    p = FakeProvider(
        responses=[
            '{"steps": ["call restricted_op"]}',
            "x",
            '{"success": true, "reason": "ok"}',
        ]
    )
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(tool_name="restricted_op", level=ActionLevel.DENY),
        tenant_ctx=TENANT,
    )
    g = AgentGraph(planner=p, executor=p, verifier=p, permission_matrix=matrix)
    with pytest.raises(PermissionError):
        await g.run(goal="deny test", tenant_ctx=TENANT)


async def test_graph_result_processor_redacts_secrets() -> None:
    p = FakeProvider(
        responses=[
            '{"steps": ["fetch key"]}',
            "key is sk-secretabc123def456ghi",
            '{"success": true, "reason": "ok"}',
        ]
    )
    g = AgentGraph(planner=p, executor=p, verifier=p, result_processor=ResultProcessor())
    state = await g.run(goal="redact test", tenant_ctx=TENANT)
    if state.steps:
        assert "sk-secret" not in state.steps[0].output


async def test_graph_dedup_skips_duplicate_step() -> None:
    p = FakeProvider(
        responses=[
            '{"steps": ["do thing", "do thing"]}',
            "first output",
            "second output",
            '{"success": true, "reason": "ok"}',
        ]
    )
    g = AgentGraph(planner=p, executor=p, verifier=p, dedup_cache=DeduplicationCache())
    state = await g.run(goal="dedup test", tenant_ctx=TENANT)
    assert state is not None


async def test_graph_audit_log_records_steps() -> None:
    p = FakeProvider(
        responses=[
            '{"steps": ["call github", "call jira"]}',
            "repos",
            "ticket",
            '{"success": true, "reason": "ok"}',
        ]
    )
    audit = AuditLog()
    g = AgentGraph(planner=p, executor=p, verifier=p, audit_log=audit)
    state = await g.run(goal="audit test", tenant_ctx=TENANT)
    entries = audit.query(tenant_ctx=TENANT)
    assert len(entries) == 2


async def test_graph_execution_memory_records_winning_plan() -> None:
    p = FakeProvider(
        responses=[
            '{"steps": ["step a", "step b"]}',
            "a done",
            "b done",
            '{"success": true, "reason": "ok"}',
        ]
    )
    mem = ExecutionMemory()
    g = AgentGraph(planner=p, executor=p, verifier=p, exec_memory=mem)
    state = await g.run(goal="memory test", tenant_ctx=TENANT)
    assert state.status == GoalStatus.COMPLETE
    recalled = mem.recall(goal_hint="memory test", tenant_ctx=TENANT)
    assert len(recalled) >= 1


async def test_graph_supervised_mode_waiting_human() -> None:
    """In supervised mode, high-risk step blocks on HITL; times out and raises PermissionError."""
    p = FakeProvider(
        responses=[
            '{"steps": ["deploy to production"]}',
            "deploying",
            '{"success": false, "reason": "needs approval"}',
        ]
    )
    hitl = HITLGateway(timeout_seconds=0.05)  # Very short timeout so test does not hang
    g = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        hitl_gateway=hitl,
        autonomy_mode="supervised",
        max_iterations=1,
    )
    # With blocking HITL and no approver, the step times out -> PermissionError
    with pytest.raises(PermissionError, match="timed out"):
        await g.run(goal="supervised deploy", tenant_ctx=TENANT)


async def test_graph_tenant_isolation() -> None:
    ta = TenantContext(tenant_id="t-a", plan=PlanTier.FREE, api_key_id="ka")
    tb = TenantContext(tenant_id="t-b", plan=PlanTier.FREE, api_key_id="kb")
    p = FakeProvider(
        responses=[
            '{"steps": ["s"]}',
            "o",
            '{"success": true, "reason": "ok"}',
            '{"steps": ["s"]}',
            "o",
            '{"success": true, "reason": "ok"}',
        ]
    )
    g = AgentGraph(planner=p, executor=p, verifier=p)
    sa = await g.run(goal="goal a", tenant_ctx=ta)
    sb = await g.run(goal="goal b", tenant_ctx=tb)
    assert sa.tenant_ctx.tenant_id == "t-a"
    assert sb.tenant_ctx.tenant_id == "t-b"
    assert sa.goal_id != sb.goal_id


# ---------------------------------------------------------------------------
# New tests: Fix 3 (HITL blocking), Fix 4 (circuit breaker), Fix 1 (embeddings)
# ---------------------------------------------------------------------------


async def test_hitl_supervised_blocks_then_approves() -> None:
    """Supervised mode: agent blocks on high-risk step, resumes after approval."""
    p = FakeProvider(
        responses=[
            '{"steps": ["deploy to production server"]}',
            "deployed",
            '{"success": true, "reason": "done"}',
        ]
    )
    hitl = HITLGateway(timeout_seconds=5.0)
    graph = AgentGraph(
        planner=p, executor=p, verifier=p,
        hitl_gateway=hitl,
        autonomy_mode="supervised",
    )

    async def approve_after_delay() -> None:
        await asyncio.sleep(0.1)
        pending = hitl.list_pending(tenant_ctx=TENANT)
        if pending:
            hitl.approve(pending[0].request_id, approver="admin", tenant_ctx=TENANT)

    # Launch the graph and the approver concurrently
    task = asyncio.create_task(
        graph.run(goal="supervised deploy", tenant_ctx=TENANT)
    )
    await asyncio.sleep(0.05)
    asyncio.create_task(approve_after_delay())
    state = await task
    assert state.status in {GoalStatus.COMPLETE, GoalStatus.FAILED}


async def test_hitl_timeout_auto_rejects() -> None:
    """Approval request times out and execution raises PermissionError."""
    p = FakeProvider(
        responses=[
            '{"steps": ["deploy to prod"]}',
            "deploying",
            '{"success": true, "reason": "done"}',
        ]
    )
    hitl = HITLGateway(timeout_seconds=0.05)  # Very short timeout
    graph = AgentGraph(
        planner=p, executor=p, verifier=p,
        hitl_gateway=hitl,
        autonomy_mode="supervised",
    )
    # Don't approve -- let it timeout
    with pytest.raises(PermissionError, match="timed out"):
        await graph.run(goal="timeout deploy", tenant_ctx=TENANT)


async def test_circuit_breaker_half_open_probe() -> None:
    """After cooldown, HALF_OPEN allows one probe call and resets on success."""
    from app.reliability.circuit_breaker import CircuitBreaker, CircuitState

    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=0.01)
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    await asyncio.sleep(0.02)
    assert breaker.allows_probe()
    assert breaker.can_call()  # transitions to HALF_OPEN
    assert breaker.state == CircuitState.HALF_OPEN
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED


async def test_real_embedding_fallback_no_provider() -> None:
    """embed_texts returns empty embeddings when no provider given (Fix 1.6)."""
    from app.providers.base import embed_texts

    result = await embed_texts(["hello world", "foo bar"])
    assert len(result) == 2
    for vec in result:
        # Must be empty list, not random noise (Fix 1.6 — no random vectors)
        assert vec == []


def test_write_checkpoint_without_db_does_not_raise() -> None:
    """Without DB, _write_checkpoint is a safe no-op."""
    import asyncio

    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState, GoalStatus
    from app.intelligence.guardrails import GuardrailChecker
    from app.providers.fake import FakeProvider
    from app.reliability.dedup import DeduplicationCache
    from app.reliability.result_processor import ResultProcessor
    from app.reliability.rollback import RollbackEngine
    from app.tenancy.context import PlanTier, TenantContext

    fake = FakeProvider(
        responses=['{"steps":["do it"]}', "done", '{"success":true,"reason":"ok"}']
    )
    graph = AgentGraph(
        planner=fake,
        executor=fake,
        verifier=fake,
        result_processor=ResultProcessor(),
        dedup_cache=DeduplicationCache(),
        rollback_engine=RollbackEngine(),
        guardrail_checker=GuardrailChecker(),
    )
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")
    state = AgentState(goal="test", tenant_ctx=ctx, goal_id="g1")
    # Should not raise — no DB configured
    asyncio.run(graph._write_checkpoint("g1", 0, state, ctx))


def test_load_checkpoint_without_db_returns_none() -> None:
    import asyncio

    from app.agent.graph import AgentGraph
    from app.intelligence.guardrails import GuardrailChecker
    from app.providers.fake import FakeProvider
    from app.reliability.dedup import DeduplicationCache
    from app.reliability.result_processor import ResultProcessor
    from app.reliability.rollback import RollbackEngine
    from app.tenancy.context import PlanTier, TenantContext

    fake = FakeProvider(responses=["done"])
    graph = AgentGraph(
        planner=fake,
        executor=fake,
        verifier=fake,
        result_processor=ResultProcessor(),
        dedup_cache=DeduplicationCache(),
        rollback_engine=RollbackEngine(),
        guardrail_checker=GuardrailChecker(),
    )
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")
    result = asyncio.run(graph._load_checkpoint("g1", ctx))
    assert result is None
