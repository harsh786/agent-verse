"""Tests for P2 governance fixes."""
import pytest
from app.governance.hitl import HITLGateway, ApprovalStatus
from app.tenancy.context import TenantContext, PlanTier

CTX = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k1")


def test_multi_person_approval_requires_two():
    """Approval with required_approvers=2 only completes after 2 approvals."""
    gw = HITLGateway()
    req_id = gw.request_approval(goal_id="g1", action="deploy",
                                  risk_level="high", tenant_ctx=CTX)
    # Manually set required_approvers to 2
    req = gw.get_request(req_id, tenant_ctx=CTX)
    assert req is not None
    req.required_approvers = 2

    # First approval — not yet approved
    gw.approve(req_id, approver="alice", note="ok", tenant_ctx=CTX)
    req_after_1 = gw.get_request(req_id, tenant_ctx=CTX)
    assert req_after_1 is not None
    assert req_after_1.approvals_received == 1
    assert req_after_1.status == ApprovalStatus.PENDING

    # Second approval — now approved
    gw.approve(req_id, approver="bob", note="ok", tenant_ctx=CTX)
    req_after_2 = gw.get_request(req_id, tenant_ctx=CTX)
    assert req_after_2 is not None
    assert req_after_2.approvals_received == 2
    assert req_after_2.status == ApprovalStatus.APPROVED


def test_single_person_approval_works_as_before():
    gw = HITLGateway()
    req_id = gw.request_approval(goal_id="g2", action="read",
                                  risk_level="low", tenant_ctx=CTX)
    result = gw.approve(req_id, approver="alice", note="", tenant_ctx=CTX)
    assert result is True
    req = gw.get_request(req_id, tenant_ctx=CTX)
    assert req is not None
    assert req.status == ApprovalStatus.APPROVED


def test_duplicate_approver_not_double_counted():
    """Same approver voting twice should not increment the count."""
    gw = HITLGateway()
    req_id = gw.request_approval(goal_id="g3", action="action",
                                  risk_level="low", tenant_ctx=CTX)
    req = gw.get_request(req_id, tenant_ctx=CTX)
    assert req is not None
    req.required_approvers = 2

    gw.approve(req_id, approver="alice", note="", tenant_ctx=CTX)
    gw.approve(req_id, approver="alice", note="", tenant_ctx=CTX)  # duplicate

    req_check = gw.get_request(req_id, tenant_ctx=CTX)
    assert req_check is not None
    assert req_check.approvals_received == 1
    assert req_check.status == ApprovalStatus.PENDING


def test_time_window_policy_blocks_outside_hours():
    """Policy with allowed_hours_utc should not match when outside window."""
    from app.governance.policies import Policy, PolicyEngine, PolicyResult

    engine = PolicyEngine()
    # Create a policy active only during hour 25 (never active)
    policy = Policy(
        name="restricted",
        description="only during hour 25",
        denied_tools=["dangerous_tool"],
        allowed_hours_utc=(25, 26),  # impossible window
    )
    engine.add_policy(policy)

    result = engine.evaluate("dangerous_tool", tenant_ctx=CTX)
    # Outside time window → policy skipped → ALLOW
    assert result == PolicyResult.ALLOW


def test_time_window_policy_allows_within_hours():
    """Policy without time restrictions should apply normally."""
    from app.governance.policies import Policy, PolicyEngine, PolicyResult

    engine = PolicyEngine()
    policy = Policy(
        name="always_deny",
        description="always active",
        denied_tools=["dangerous_tool"],
        allowed_hours_utc=None,  # no restriction
        allowed_weekdays=None,
    )
    engine.add_policy(policy)

    result = engine.evaluate("dangerous_tool", tenant_ctx=CTX)
    assert result == PolicyResult.DENY


@pytest.mark.asyncio
async def test_delegation_endpoint_requires_auth():
    from httpx import AsyncClient, ASGITransport
    from app.main import create_app
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/collab/sessions/s1/delegate",
                         json={"from_agent_id": "a1", "to_agent_id": "a2",
                               "sub_task": "do it"})
        assert r.status_code == 401
