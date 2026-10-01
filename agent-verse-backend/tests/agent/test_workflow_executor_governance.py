"""WorkflowExecutor tool calls go through the governed gate (audit item 2)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.tool_context import ToolContext, ToolRef
from app.agent.tool_gate import GovernedToolGate
from app.agent.workflow_executor import WorkflowExecutor
from app.agent.workflow_planner import WorkflowStep, _StaticWorkflowPlan, _StaticWorkflowStep
from app.governance.grants.store import InMemoryGrantStore
from app.governance.hitl import ApprovalStatus
from app.governance.permissions import ActionLevel
from app.governance.policies import PolicyResult
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio

T = TenantContext(tenant_id="wf-gov", plan=PlanTier.ENTERPRISE, api_key_id="k")


def _mcp() -> MagicMock:
    mcp = MagicMock()
    mcp.call_tool = AsyncMock(return_value=SimpleNamespace(success=True, output="rows", error=None))
    return mcp


async def _run_tool_step(gate: GovernedToolGate, tool: str = "search_records") -> tuple:
    mcp = _mcp()
    ex = WorkflowExecutor(mcp_client=mcp, tool_gate=gate, goal_id="g1")
    result = await ex._execute_step(WorkflowStep(id="s1", description="d", tool=tool), T, {})
    return result, mcp


async def test_policy_deny_blocks_tool_call() -> None:
    policy = MagicMock()
    policy.evaluate.return_value = PolicyResult.DENY
    result, mcp = await _run_tool_step(GovernedToolGate(policy_engine=policy, guardrails=None))
    assert result["status"] == "denied"
    mcp.call_tool.assert_not_called()


async def test_permission_matrix_deny_blocks_tool_call() -> None:
    matrix = MagicMock()
    matrix.check.return_value = ActionLevel.DENY
    result, mcp = await _run_tool_step(GovernedToolGate(permission_matrix=matrix, guardrails=None))
    assert result["status"] == "denied"
    mcp.call_tool.assert_not_called()


async def test_ungranted_tool_blocked_when_grants_enforced() -> None:
    gate = GovernedToolGate(
        grant_store=InMemoryGrantStore(), enforce_grants=True, agent_id="a1", guardrails=None
    )
    result, mcp = await _run_tool_step(gate)
    assert result["status"] == "denied"
    mcp.call_tool.assert_not_called()


async def test_high_risk_tool_requires_approval_and_timeout_denies() -> None:
    hitl = MagicMock()
    hitl.request_approval.return_value = "r1"
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.TIMED_OUT)
    gate = GovernedToolGate(hitl_gateway=hitl, autonomy_mode="supervised", guardrails=None)
    result, mcp = await _run_tool_step(gate, tool="create_invoice")
    assert result["status"] == "denied"
    hitl.request_approval.assert_called_once()
    mcp.call_tool.assert_not_called()


async def test_high_risk_tool_runs_after_approval() -> None:
    hitl = MagicMock()
    hitl.request_approval.return_value = "r1"
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.APPROVED)
    gate = GovernedToolGate(hitl_gateway=hitl, autonomy_mode="supervised", guardrails=None)
    result, mcp = await _run_tool_step(gate, tool="create_invoice")
    assert result["status"] == "complete"
    assert result["output"] == "rows"
    mcp.call_tool.assert_awaited_once()


@pytest.mark.parametrize("mode", ["bounded-autonomous", "fully-autonomous"])
async def test_high_risk_tool_outside_supervised_is_denied_without_orphan_request(
    mode: str,
) -> None:
    """CORE-01: nothing waits for a decision outside supervised mode, so the tool
    is denied and no approval request is filed (one used to be left pending)."""
    hitl = MagicMock()
    hitl.request_approval.return_value = "r1"
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.APPROVED)
    gate = GovernedToolGate(hitl_gateway=hitl, autonomy_mode=mode, guardrails=None)
    result, mcp = await _run_tool_step(gate, tool="create_invoice")
    assert result["status"] == "denied"
    assert "supervised mode" in result["error"]
    hitl.request_approval.assert_not_called()
    hitl.wait_for_approval.assert_not_awaited()
    mcp.call_tool.assert_not_called()


async def test_exhausted_budget_blocks_tool_call() -> None:
    cc = SimpleNamespace(check_and_record=AsyncMock(return_value=False))
    result, mcp = await _run_tool_step(GovernedToolGate(cost_controller=cc, guardrails=None))
    assert result["status"] == "denied"
    assert "budget" in result["error"]
    mcp.call_tool.assert_not_called()


async def test_default_gate_refuses_unknown_tool_without_approval_gateway() -> None:
    mcp = _mcp()
    ex = WorkflowExecutor(mcp_client=mcp)
    result = await ex._execute_step(WorkflowStep(id="s1", description="d", tool="frobnicate"), T, {})
    assert result["status"] == "denied"
    mcp.call_tool.assert_not_called()


async def test_static_workflow_with_unexecuted_step_is_not_complete() -> None:
    policy = MagicMock()
    policy.evaluate.return_value = PolicyResult.DENY
    tool = ToolRef(
        server_id="jira", server_name="Jira", name="search_issue", description="jira search",
        input_schema={},
    )
    plan = _StaticWorkflowPlan(
        steps=[
            _StaticWorkflowStep(
                step_id="s1",
                connector_name="jira",
                agent_id=None,
                intent="fetch_open_issues",
                input_from=[],
                requires_approval=False,
            )
        ]
    )
    mcp = _mcp()
    ex = WorkflowExecutor(
        mcp_client=mcp, tool_gate=GovernedToolGate(policy_engine=policy, guardrails=None)
    )
    result = await ex.execute(plan, T, tool_context=ToolContext(connectors=[], tools=[tool]))
    assert result["status"] == "incomplete"
    mcp.call_tool.assert_not_called()


async def test_budget_controller_error_fails_closed_for_tool_call() -> None:
    """A cost-controller outage must deny the tool call (was: ok=True)."""
    cc = SimpleNamespace(check_and_record=AsyncMock(side_effect=ConnectionError("redis down")))
    result, mcp = await _run_tool_step(GovernedToolGate(cost_controller=cc, guardrails=None))
    assert result["status"] == "denied"
    assert "budget_check_failed" in result["error"]
    mcp.call_tool.assert_not_called()


async def test_charge_llm_controller_error_denies_spend() -> None:
    """charge_llm returns False (deny) on a controller error (was: True)."""
    cc = SimpleNamespace(check_and_record=AsyncMock(side_effect=ConnectionError("redis down")))
    gate = GovernedToolGate(cost_controller=cc, guardrails=None)
    resp = SimpleNamespace(model="gpt-4o", usage=None, input_tokens=10, output_tokens=5)
    assert await gate.charge_llm(goal_id="g1", tenant_ctx=T, resp=resp) is False


class _BrokenSession:
    async def __aenter__(self) -> None:
        raise ConnectionError("approval_requests unreachable")

    async def __aexit__(self, *exc: object) -> None:
        return None


async def test_unpersistable_approval_denies_workflow_tool_immediately() -> None:
    """CORE-27: the gate files its approval durably. When the row cannot be
    written no other replica can see it, so the tool is denied now — it used to
    be filed fire-and-forget and the gate waited out its whole timeout."""
    from app.governance.hitl import HITLGateway

    hitl = HITLGateway(db_session_factory=_BrokenSession)
    hitl.wait_for_approval = AsyncMock(return_value=ApprovalStatus.APPROVED)  # type: ignore[method-assign]
    gate = GovernedToolGate(
        hitl_gateway=hitl, autonomy_mode="supervised", guardrails=None, hitl_timeout=0.1
    )
    result, mcp = await _run_tool_step(gate, tool="create_invoice")
    assert result["status"] == "denied"
    assert "could not be persisted" in result["error"]
    hitl.wait_for_approval.assert_not_awaited()
    assert hitl._requests == {}  # no invisible, process-local request left behind
    mcp.call_tool.assert_not_called()
