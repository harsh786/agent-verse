"""Tests for P0 governance fixes."""
import pytest
from app.governance.pricing import estimate_cost
from app.governance.hitl import HITLGateway, ApprovalStatus
from app.tenancy.context import TenantContext, PlanTier


CTX = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


def test_estimate_cost_returns_nonzero():
    cost = estimate_cost("claude-opus-4-8", 1000, 500)
    assert cost > 0.0
    assert cost < 10.0  # sanity check


def test_estimate_cost_zero_tokens():
    assert estimate_cost("gpt-4o", 0, 0) == 0.0


def test_estimate_cost_unknown_model():
    cost = estimate_cost("unknown-model-xyz", 1000, 1000)
    assert cost > 0.0  # Conservative fallback


def test_hitl_expires_timed_out():
    import time
    gw = HITLGateway(timeout_seconds=0.01)  # Very short timeout
    req_id = gw.request_approval(goal_id="g1", action="deploy",
                                  risk_level="high", tenant_ctx=CTX)
    time.sleep(0.05)  # Wait for expiry
    expired = gw.expire_timed_out_requests()
    assert req_id in expired
    req = gw.get_request(req_id, tenant_ctx=CTX)
    assert req.status == ApprovalStatus.TIMED_OUT


def test_hitl_notification_service_wired():
    """HITLGateway accepts notification_service injection."""
    gw = HITLGateway()
    assert hasattr(gw, '_notification_service')
    gw._notification_service = None  # Can be set to None
