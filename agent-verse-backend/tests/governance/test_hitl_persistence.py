"""Tests for HITL persistence additions — 5 tests."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.governance.hitl import ApprovalRequest, ApprovalStatus, HITLGateway
from app.tenancy.context import TenantContext


def _tenant(tid: str = "tenant-1") -> TenantContext:
    return TenantContext(tenant_id=tid, plan="pro", api_key_id="key-test")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_expire_timed_out_requests_auto_rejects_old_requests() -> None:
    """expire_timed_out_requests must mark timed-out requests as TIMED_OUT."""
    gateway = HITLGateway(timeout_seconds=300)
    tenant = _tenant()

    req_id = gateway.request_approval(
        goal_id="g1", action="delete", risk_level="high", tenant_ctx=tenant
    )
    req = gateway.get_request(req_id, tenant_ctx=tenant)
    assert req is not None

    # Backdate _expires_at_dt so it appears already expired
    req._expires_at_dt = datetime.now(UTC) - timedelta(seconds=1)

    expired = gateway.expire_timed_out_requests()

    assert req_id in expired
    assert req.status == ApprovalStatus.TIMED_OUT
    assert req._event.is_set()


def test_request_approval_sets_expires_at_dt() -> None:
    """request_approval must set _expires_at_dt on the created request."""
    timeout = 120.0
    gateway = HITLGateway(timeout_seconds=timeout)
    tenant = _tenant()

    before = datetime.now(UTC)
    req_id = gateway.request_approval(
        goal_id="g2", action="write", risk_level="medium", tenant_ctx=tenant
    )
    after = datetime.now(UTC)

    req = gateway.get_request(req_id, tenant_ctx=tenant)
    assert req is not None
    assert req._expires_at_dt is not None

    expected_min = before + timedelta(seconds=timeout)
    expected_max = after + timedelta(seconds=timeout)
    assert expected_min <= req._expires_at_dt <= expected_max


@pytest.mark.anyio
async def test_load_pending_from_db_returns_zero_when_db_is_none() -> None:
    """load_pending_from_db must return 0 immediately when db=None."""
    gateway = HITLGateway()
    result = await gateway.load_pending_from_db(None, "tenant-x")
    assert result == 0


def test_approve_resolves_event() -> None:
    """approve() must set the asyncio.Event so waiters unblock."""
    gateway = HITLGateway()
    tenant = _tenant()

    req_id = gateway.request_approval(
        goal_id="g3", action="read", risk_level="low", tenant_ctx=tenant
    )
    req = gateway.get_request(req_id, tenant_ctx=tenant)
    assert req is not None
    assert not req._event.is_set()

    ok = gateway.approve(req_id, approver="alice", tenant_ctx=tenant)

    assert ok is True
    assert req.status == ApprovalStatus.APPROVED
    assert req._event.is_set()


async def test_reject_resolves_event() -> None:
    """reject() must set the asyncio.Event so waiters unblock."""
    gateway = HITLGateway()
    tenant = _tenant()

    req_id = gateway.request_approval(
        goal_id="g4", action="execute", risk_level="critical", tenant_ctx=tenant
    )
    req = gateway.get_request(req_id, tenant_ctx=tenant)
    assert req is not None
    assert not req._event.is_set()

    ok = await gateway.reject(req_id, approver="bob", note="too risky", tenant_ctx=tenant)

    assert ok is True
    assert req.status == ApprovalStatus.REJECTED
    assert req._event.is_set()
