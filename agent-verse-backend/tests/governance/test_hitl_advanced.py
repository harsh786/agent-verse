"""P1.3 advanced HITL tests: multi-person approval, rejection feedback, email."""
import pytest

from app.governance.hitl import ApprovalStatus, HITLGateway
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="hitl-t1", plan=PlanTier.ENTERPRISE, api_key_id="k")


@pytest.mark.asyncio
async def test_request_approval_accepts_required_approvers():
    gw = HITLGateway()
    req = await gw.request_approval(
        goal_id="g1",
        step_description="Deploy to prod",
        tenant_ctx=T,
        required_approvers=2,
    )
    assert req.required_approvers == 2, "required_approvers must be stored on ApprovalRequest"


@pytest.mark.asyncio
async def test_request_approval_returns_approval_request():
    """await gw.request_approval(...) must return an ApprovalRequest, not a string."""
    gw = HITLGateway()
    req = await gw.request_approval(
        goal_id="g0",
        step_description="Some action",
        tenant_ctx=T,
    )
    from app.governance.hitl import ApprovalRequest
    assert isinstance(req, ApprovalRequest)
    assert req.request_id  # non-empty
    assert req.status == ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_single_approval_insufficient_when_two_required():
    gw = HITLGateway()
    req = await gw.request_approval(
        goal_id="g2",
        step_description="Critical action",
        tenant_ctx=T,
        required_approvers=2,
    )
    # First approval
    result1 = await gw.approve(request_id=req.request_id, tenant_ctx=T, approver="alice")
    # Should NOT be approved yet (need 2)
    fetched = gw.get_request(request_id=req.request_id, tenant_ctx=T)
    assert fetched is not None
    assert fetched.status != ApprovalStatus.APPROVED, \
        "Single approval should not approve when 2 required"


@pytest.mark.asyncio
async def test_two_approvals_completes_when_two_required():
    gw = HITLGateway()
    req = await gw.request_approval(
        goal_id="g3",
        step_description="Deploy",
        tenant_ctx=T,
        required_approvers=2,
    )
    await gw.approve(request_id=req.request_id, tenant_ctx=T, approver="alice")
    await gw.approve(request_id=req.request_id, tenant_ctx=T, approver="bob")

    fetched = gw.get_request(request_id=req.request_id, tenant_ctx=T)
    assert fetched.status == ApprovalStatus.APPROVED, \
        "Two approvals should complete when 2 required"


def test_approval_email_sender_importable():
    from app.integrations.email.approval_sender import send_approval_email
    import asyncio
    assert asyncio.iscoroutinefunction(send_approval_email)


def test_approval_email_signs_links():
    from app.integrations.email.approval_sender import _sign
    sig1 = _sign("req-1", "approve")
    sig2 = _sign("req-1", "reject")
    sig3 = _sign("req-2", "approve")
    assert sig1 != sig2  # different actions
    assert sig1 != sig3  # different request IDs
    assert len(sig1) == 32  # correct length


def test_rejection_note_injector_importable():
    import inspect
    from app.services import goal_service
    src = inspect.getsource(goal_service)
    assert "_subscribe_hitl_rejections" in src or "hitl_rejection" in src, \
        "goal_service must have HITL rejection note subscription"


def test_approval_request_is_string_compatible():
    """ApprovalRequest can be used as a string-like key (backward compat)."""
    gw = HITLGateway()
    req = gw.request_approval(
        goal_id="gcompat",
        action="some-action",
        risk_level="high",
        tenant_ctx=T,
    )
    # Should be truthy
    assert req, "ApprovalRequest must be truthy"
    # str() should return the request_id
    assert str(req) == req.request_id
    # Should be able to look up via the returned object
    fetched = gw.get_request(req, tenant_ctx=T)
    assert fetched is not None
    assert fetched.request_id == req.request_id


def test_approval_request_await_returns_self():
    """await gateway.request_approval(...) must return the ApprovalRequest."""
    import asyncio
    from app.governance.hitl import ApprovalRequest

    gw = HITLGateway()

    async def _test():
        req = await gw.request_approval(
            goal_id="gawaitable",
            step_description="test await",
            tenant_ctx=T,
        )
        assert isinstance(req, ApprovalRequest)
        return req

    result = asyncio.run(_test())
    assert isinstance(result, ApprovalRequest)
