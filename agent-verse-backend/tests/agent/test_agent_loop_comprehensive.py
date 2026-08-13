"""Behavior parity tests for the canonical profile-aware AgentGraph kernel."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.graph import AgentGraph, _extract_tool_name
from app.agent.state import GoalStatus, StepStatus
from app.governance.audit import AuditLog
from app.governance.cost import BudgetConfig, CostController
from app.governance.hitl import ApprovalStatus, HITLGateway
from app.governance.permissions import ActionLevel, PermissionMatrix, PermissionRule
from app.memory.execution import ExecutionMemory
from app.providers.fake import FakeProvider
from app.reliability.dedup import DeduplicationCache
from app.reliability.result_processor import ResultProcessor
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(
    tenant_id="tid-graph", plan=PlanTier.ENTERPRISE, api_key_id="key-graph"
)


def _make_graph(**kwargs: object) -> AgentGraph:
    defaults: dict[str, object] = {
        "planner": FakeProvider(responses=['{"steps": ["Step 1: Do the thing"]}']),
        "executor": FakeProvider(responses=["Done"]),
        "verifier": FakeProvider(responses=['{"success": true, "reason": "ok"}']),
    }
    defaults.update(kwargs)
    return AgentGraph(**defaults)  # type: ignore[arg-type]


def test_extract_tool_name_contract() -> None:
    assert _extract_tool_name("call jira to get issues") == "jira"
    assert _extract_tool_name("fetch all open tickets") == "llm_call"
    assert _extract_tool_name("please call") == "llm_call"
    assert _extract_tool_name("call github:") == "github"


@pytest.mark.asyncio
async def test_graph_completes_and_records_steps_and_events() -> None:
    events: list[dict] = []

    async def capture(event: dict) -> None:
        events.append(event)

    graph = _make_graph(
        planner=FakeProvider(responses=['{"steps": ["Step 1", "Step 2"]}']),
        executor=FakeProvider(responses=["R1", "R2"]),
    )
    state = await graph.run(goal="Two-step goal", tenant_ctx=CTX, event_callback=capture)
    assert state.status is GoalStatus.COMPLETE
    assert all(step.status is StepStatus.COMPLETE for step in state.steps)
    assert {"goal_started", "plan_ready", "step_started", "step_complete", "goal_complete"} <= {
        event["type"] for event in events
    }


@pytest.mark.asyncio
async def test_graph_replans_and_honors_max_iterations() -> None:
    graph = _make_graph(
        planner=FakeProvider(
            responses=['{"steps": ["first"]}', '{"steps": ["second"]}']
        ),
        executor=FakeProvider(responses=["attempted", "done"]),
        verifier=FakeProvider(
            responses=[
                '{"success": false, "reason": "retry"}',
                '{"success": true, "reason": "ok"}',
            ]
        ),
        max_iterations=2,
    )
    state = await graph.run(goal="Retry", tenant_ctx=CTX)
    assert state.status is GoalStatus.COMPLETE
    assert state.iterations == 2


@pytest.mark.asyncio
async def test_graph_preserves_initial_context() -> None:
    state = await _make_graph().run(
        goal="Goal", tenant_ctx=CTX, initial_context={"extra": "data"}
    )
    assert state.context["extra"] == "data"


@pytest.mark.asyncio
async def test_graph_enforces_permission_and_budget_controls() -> None:
    permission = PermissionMatrix()
    permission.set_rule(
        PermissionRule(tool_name="llm_call", level=ActionLevel.DENY), tenant_ctx=CTX
    )
    with pytest.raises(PermissionError):
        await _make_graph(permission_matrix=permission).run(goal="Denied", tenant_ctx=CTX)

    budget = CostController(BudgetConfig(per_goal_usd=0.0, per_tenant_daily_usd=0.0))
    state = await _make_graph(cost_controller=budget).run(goal="Budget", tenant_ctx=CTX)
    assert state.iterations > 0


@pytest.mark.asyncio
async def test_graph_deduplication_and_result_sanitization() -> None:
    graph = _make_graph(
        executor=FakeProvider(responses=["API key sk-secretkey123"]),
        dedup_cache=DeduplicationCache(),
        result_processor=ResultProcessor(),
    )
    first = await graph.run(goal="Sanitize", tenant_ctx=CTX)
    second = await graph.run(goal="Sanitize", tenant_ctx=CTX)
    assert "sk-secretkey123" not in (first.steps[0].output or "")
    assert second.status is GoalStatus.COMPLETE


@pytest.mark.asyncio
async def test_graph_awaits_supervised_hitl_rejection() -> None:
    hitl = MagicMock(spec=HITLGateway)
    hitl.request_approval.return_value = MagicMock(request_id="request-1")
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.REJECTED)
    graph = _make_graph(
        planner=FakeProvider(responses=['{"steps": ["deploy production"]}']),
        hitl_gateway=hitl,
        autonomy_mode="supervised",
    )
    with pytest.raises(PermissionError, match="rejected"):
        await graph.run(goal="Deploy", tenant_ctx=CTX)
    hitl.wait_for_approval.assert_awaited_once()


@pytest.mark.asyncio
async def test_graph_records_audit_and_winning_execution_memory() -> None:
    audit = AuditLog()
    memory = ExecutionMemory()
    state = await _make_graph(audit_log=audit, exec_memory=memory).run(
        goal="Memory", tenant_ctx=CTX
    )
    assert state.status is GoalStatus.COMPLETE
    assert audit.query(tenant_ctx=CTX)
    assert memory.recall(goal_hint="Memory", tenant_ctx=CTX)
