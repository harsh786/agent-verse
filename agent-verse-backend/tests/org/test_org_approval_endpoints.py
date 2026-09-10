"""Regression: org-scoped approval-center endpoints (G-24) resolve real HITL
requests via the one HITLGateway.

These endpoints previously called a non-existent ``hitl_gateway.resolve(...)``;
the AttributeError was swallowed by a broad ``except`` and turned into a 404, so
*every* org approve/reject silently failed. There was no test. This locks in the
fix: they call the real ``approve``/``reject`` API and 404 only when the request
is genuinely absent.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.governance.hitl import ApprovalStatus, HITLGateway
from app.org.router import _OrgApprovalDecision, approve_org_request, reject_org_request
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tenant-1", plan=PlanTier.FREE, api_key_id="k")


def _request_with(gateway: HITLGateway) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(hitl_gateway=gateway)))


def _service() -> SimpleNamespace:
    return SimpleNamespace(_tenant_id="tenant-1")


async def _seed_pending(gateway: HITLGateway) -> str:
    req = await gateway.request_approval(
        goal_id="goal-1", action="deploy prod", risk_level="high", tenant_ctx=_CTX
    )
    return req.request_id


@pytest.mark.asyncio
async def test_approve_org_request_resolves_the_gateway_request() -> None:
    gateway = HITLGateway()
    request_id = await _seed_pending(gateway)

    result = await approve_org_request(
        org_id="org-1",
        approval_id=request_id,
        body=_OrgApprovalDecision(approver="alice", note="ok"),
        request=_request_with(gateway),
        x_request_id="req-1",
        service=_service(),
    )

    assert result["status"] == "approved"
    # The paired gateway request is actually resolved (not a phantom 404).
    resolved = gateway.get_request(request_id, tenant_ctx=_CTX)
    assert resolved is not None
    assert resolved.status == ApprovalStatus.APPROVED


@pytest.mark.asyncio
async def test_reject_org_request_resolves_the_gateway_request() -> None:
    gateway = HITLGateway()
    request_id = await _seed_pending(gateway)

    result = await reject_org_request(
        org_id="org-1",
        approval_id=request_id,
        body=_OrgApprovalDecision(approver="bob", note=""),
        request=_request_with(gateway),
        x_request_id="req-2",
        service=_service(),
    )

    assert result["status"] == "rejected"
    resolved = gateway.get_request(request_id, tenant_ctx=_CTX)
    assert resolved is not None
    assert resolved.status == ApprovalStatus.REJECTED


@pytest.mark.asyncio
async def test_approve_unknown_request_is_404() -> None:
    from fastapi import HTTPException

    gateway = HITLGateway()
    with pytest.raises(HTTPException) as exc:
        await approve_org_request(
            org_id="org-1",
            approval_id="does-not-exist",
            body=_OrgApprovalDecision(approver="alice"),
            request=_request_with(gateway),
            x_request_id="req-3",
            service=_service(),
        )
    assert exc.value.status_code == 404
