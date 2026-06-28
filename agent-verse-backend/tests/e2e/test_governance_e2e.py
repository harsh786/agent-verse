"""E2E: Governance pipeline — permissions, audit, HITL, cost, policy engine."""

from __future__ import annotations

import pytest

from app.agent.loop import AgentLoop
from app.governance.audit import AuditEvent, AuditLog
from app.governance.cost import BudgetConfig, CostController
from app.governance.hitl import ApprovalStatus, HITLGateway
from app.governance.permissions import ActionLevel, PermissionMatrix, PermissionRule
from app.governance.policies import Policy, PolicyEngine, PolicyResult
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(
    tenant_id="gov-e2e",
    plan=PlanTier.PROFESSIONAL,
    api_key_id="gov-key-001",
)


# ── Audit log records tool calls ─────────────────────────────────────────────

async def test_audit_trail_records_all_tool_calls() -> None:
    """Audit log should record every tool call passed through the pipeline."""
    provider = FakeProvider(responses=[
        '{"steps": ["call github to list repos", "call jira to create ticket"]}',
        "repos listed",
        "ticket created",
        '{"success": true, "reason": "done"}',
    ])
    audit = AuditLog()
    loop = AgentLoop(
        planner=provider,
        executor=provider,
        verifier=provider,
        audit_log=audit,
    )
    state = await loop.run(
        goal="list repos and create ticket", tenant_ctx=TENANT
    )
    # Two steps → two audit entries recorded by the loop
    entries = audit.query(tenant_ctx=TENANT)
    assert len(entries) == 2
    assert all(e.goal_id == state.goal_id for e in entries)


# ── Cost controller tracks usage ──────────────────────────────────────────────

async def test_cost_controller_tracks_usage() -> None:
    """Cost controller should track accumulated spend after each step."""
    provider = FakeProvider(responses=[
        '{"steps": ["call llm"]}',
        "output",
        '{"success": true, "reason": "done"}',
    ])
    cost = CostController()
    loop = AgentLoop(
        planner=provider,
        executor=provider,
        verifier=provider,
        cost_controller=cost,
    )
    state = await loop.run(goal="test cost tracking", tenant_ctx=TENANT)
    assert state.status.value in {"complete", "failed"}
    # The loop charges 0.01 USD per step; one step → 0.01 tracked
    assert cost.goal_total(state.goal_id, tenant_ctx=TENANT) == pytest.approx(0.01)


# ── HITL full lifecycle ───────────────────────────────────────────────────────

async def test_hitl_gateway_full_lifecycle() -> None:
    """HITL: create request → approve → verify the approval is persisted."""
    hitl = HITLGateway()

    req_id = hitl.request_approval(
        goal_id="goal-123",
        action="deploy to production",
        risk_level="high",
        tenant_ctx=TENANT,
    )
    pending = hitl.list_pending(tenant_ctx=TENANT)
    assert len(pending) == 1
    assert pending[0].request_id == req_id

    approved = hitl.approve(
        req_id,
        approver="admin@test.com",
        note="Looks good",
        tenant_ctx=TENANT,
    )
    assert approved == True  # noqa: E712 — _AwaitableBool defines __eq__ with bool

    req = hitl.get_request(req_id, tenant_ctx=TENANT)
    assert req is not None
    assert req.status == ApprovalStatus.APPROVED
    assert req.approver == "admin@test.com"


# ── HITL tenant isolation ─────────────────────────────────────────────────────

async def test_hitl_tenant_isolation() -> None:
    """HITL requests from tenant A must not be visible to tenant B."""
    hitl = HITLGateway()
    tenant_a = TenantContext(
        tenant_id="hitl-a", plan=PlanTier.FREE, api_key_id="key-a"
    )
    tenant_b = TenantContext(
        tenant_id="hitl-b", plan=PlanTier.FREE, api_key_id="key-b"
    )

    hitl.request_approval(
        goal_id="g1", action="act", risk_level="high", tenant_ctx=tenant_a
    )

    pending_a = hitl.list_pending(tenant_ctx=tenant_a)
    pending_b = hitl.list_pending(tenant_ctx=tenant_b)

    assert len(pending_a) == 1
    assert len(pending_b) == 0


# ── Policy engine — most restrictive wins ─────────────────────────────────────

async def test_policy_engine_most_restrictive_wins() -> None:
    """A deny policy on a sub-pattern overrides a general allow-all policy."""
    engine = PolicyEngine()

    # General policy: no denied tools (allows everything by default ALLOW)
    engine.add_policy(Policy(
        name="base-policy",
        description="Allow all github operations",
        denied_tools=[],
        approval_tools=[],
    ))
    # Restrictive policy: deny all delete operations
    engine.add_policy(Policy(
        name="no-deletes",
        description="Deny any delete tool",
        denied_tools=["github.delete*", "*.delete*"],
    ))

    tenant = TenantContext(
        tenant_id="policy-e2e", plan=PlanTier.FREE, api_key_id="pol-key"
    )

    # github.list_repos: not in any denied pattern → ALLOW
    result_list = engine.evaluate(tool_name="github.list_repos", tenant_ctx=tenant)
    assert result_list == PolicyResult.ALLOW

    # github.delete_repo: matches "github.delete*" → DENY
    result_delete = engine.evaluate(
        tool_name="github.delete_repo", tenant_ctx=tenant
    )
    assert result_delete == PolicyResult.DENY


# ── Permission matrix per-tool ────────────────────────────────────────────────

async def test_permission_matrix_deny_blocks_execution() -> None:
    """Governance check: a DENY rule causes the loop to raise PermissionError."""
    provider = FakeProvider(responses=[
        '{"steps": ["call restricted_tool to do something"]}',
        "should not reach here",
        '{"success": true, "reason": "done"}',
    ])
    matrix = PermissionMatrix()
    matrix.set_rule(
        PermissionRule(tool_name="restricted_tool", level=ActionLevel.DENY),
        tenant_ctx=TENANT,
    )
    loop = AgentLoop(
        planner=provider,
        executor=provider,
        verifier=provider,
        permission_matrix=matrix,
    )
    with pytest.raises(PermissionError, match="restricted_tool"):
        await loop.run(goal="trigger deny rule", tenant_ctx=TENANT)


# ── PolicyEngine wired into AgentGraph blocks execution ───────────────────────

async def test_policy_engine_deny_blocks_agent_execution() -> None:
    """PolicyEngine deny policy blocks tool execution in the agent."""
    from app.governance.policies import PolicyEngine, Policy
    from app.agent.graph import AgentGraph
    from app.agent.state import GoalStatus
    from app.providers.fake import FakeProvider
    from app.tenancy.context import TenantContext, PlanTier

    T2 = TenantContext(tenant_id="policy-agent-t1", plan=PlanTier.ENTERPRISE, api_key_id="pat1")

    p = FakeProvider(responses=[
        '{"steps": ["call github to delete repo"]}',
        "deleted",
        '{"success": true, "reason": "done"}',
    ])

    engine = PolicyEngine()
    engine.add_policy(Policy(
        name="no-deletes",
        description="deny delete tools",
        denied_tools=["github"],  # matches tool name "github" extracted from step
    ))

    graph = AgentGraph(planner=p, executor=p, verifier=p, policy_engine=engine)

    with pytest.raises(PermissionError, match="denied by governance policy"):
        await graph.run(goal="delete repos", tenant_ctx=T2)
