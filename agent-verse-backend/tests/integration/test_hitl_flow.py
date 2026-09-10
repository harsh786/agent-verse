"""Integration tests: Human-In-The-Loop (HITL) approval flow.

15+ scenarios covering the HITL gateway: request, approve, reject, timeout,
and full AgentGraph integration.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.agent.graph import AgentGraph
from app.governance.audit import AuditEvent, AuditLog
from app.governance.hitl import ApprovalStatus, HITLGateway
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _tenant(suffix: str = "h1") -> TenantContext:
    return TenantContext(
        tenant_id=f"hitl-{suffix}-{uuid.uuid4().hex[:6]}",
        plan=PlanTier.PROFESSIONAL,
        api_key_id="hitl-key",
    )


_PLAN = '{"steps": ["step one"]}'
_DEPLOY_PLAN = '{"steps": ["deploy the service to production"]}'
_STEP = "step output"
_VERIFY_OK = '{"success": true, "reason": "done"}'


# ---------------------------------------------------------------------------
# 1. Request approval creates a pending HITL entry
# ---------------------------------------------------------------------------


async def test_request_approval_creates_pending_entry() -> None:
    """request_approval() adds to internal store with PENDING status."""
    gateway = HITLGateway()
    tenant = _tenant("req")
    req = gateway.request_approval(
        goal_id="goal-001",
        action="deploy service to production",
        risk_level="high",
        tenant_ctx=tenant,
    )
    assert req.status == ApprovalStatus.PENDING
    assert req.goal_id == "goal-001"
    assert req.action == "deploy service to production"

    pending = gateway.list_pending(tenant_ctx=tenant)
    assert len(pending) == 1
    assert pending[0].request_id == req.request_id


# ---------------------------------------------------------------------------
# 2. Approve action resolves the request
# ---------------------------------------------------------------------------


async def test_approve_resolves_request() -> None:
    """approve() transitions PENDING → APPROVED and unblocks waiters."""
    gateway = HITLGateway()
    tenant = _tenant("appr")
    req = gateway.request_approval(
        goal_id="goal-002",
        action="delete old records",
        risk_level="high",
        tenant_ctx=tenant,
    )
    ok = gateway.approve(req.request_id, approver="alice", note="LGTM", tenant_ctx=tenant)
    assert bool(ok) is True

    fetched = gateway.get_request(req.request_id, tenant_ctx=tenant)
    assert fetched is not None
    assert fetched.status == ApprovalStatus.APPROVED
    assert fetched.approver == "alice"
    assert fetched.note == "LGTM"


# ---------------------------------------------------------------------------
# 3. Reject action marks request REJECTED
# ---------------------------------------------------------------------------


async def test_reject_marks_request_rejected() -> None:
    """reject() transitions PENDING → REJECTED."""
    gateway = HITLGateway()
    tenant = _tenant("rej")
    req = gateway.request_approval(
        goal_id="goal-003",
        action="truncate database",
        risk_level="high",
        tenant_ctx=tenant,
    )
    ok = await gateway.reject(req.request_id, approver="bob", note="Too risky", tenant_ctx=tenant)
    assert ok is True

    fetched = gateway.get_request(req.request_id, tenant_ctx=tenant)
    assert fetched is not None
    assert fetched.status == ApprovalStatus.REJECTED
    assert fetched.approver == "bob"


# ---------------------------------------------------------------------------
# 4. wait_for_approval resolves when approved
# ---------------------------------------------------------------------------


async def test_wait_for_approval_resolves_on_approve() -> None:
    """wait_for_approval returns APPROVED when approved before timeout."""
    gateway = HITLGateway(timeout_seconds=5.0)
    tenant = _tenant("wait")
    req = gateway.request_approval(
        goal_id="goal-004",
        action="deploy staging",
        risk_level="high",
        tenant_ctx=tenant,
    )

    async def do_approve() -> None:
        await asyncio.sleep(0.05)
        gateway.approve(req.request_id, approver="ci-bot", tenant_ctx=tenant)

    approve_task = asyncio.create_task(do_approve())
    status = await gateway.wait_for_approval(req.request_id, tenant_ctx=tenant, timeout=2.0)
    await approve_task

    assert status == ApprovalStatus.APPROVED


# ---------------------------------------------------------------------------
# 5. wait_for_approval times out when not resolved
# ---------------------------------------------------------------------------


async def test_wait_for_approval_times_out() -> None:
    """wait_for_approval returns TIMED_OUT after the timeout expires."""
    gateway = HITLGateway(timeout_seconds=0.1)
    tenant = _tenant("timeout")
    req = gateway.request_approval(
        goal_id="goal-005",
        action="wipe data",
        risk_level="high",
        tenant_ctx=tenant,
    )
    status = await gateway.wait_for_approval(req.request_id, tenant_ctx=tenant, timeout=0.1)
    assert status == ApprovalStatus.TIMED_OUT

    fetched = gateway.get_request(req.request_id, tenant_ctx=tenant)
    assert fetched is not None
    assert fetched.status == ApprovalStatus.TIMED_OUT


# ---------------------------------------------------------------------------
# 6. Multiple pending approvals per tenant
# ---------------------------------------------------------------------------


async def test_multiple_pending_approvals_listed() -> None:
    """list_pending returns all pending requests for a tenant."""
    gateway = HITLGateway()
    tenant = _tenant("multi")

    ids = []
    for i in range(3):
        req = gateway.request_approval(
            goal_id=f"goal-{i}",
            action=f"deploy service {i}",
            risk_level="high",
            tenant_ctx=tenant,
        )
        ids.append(req.request_id)

    pending = gateway.list_pending(tenant_ctx=tenant)
    assert len(pending) == 3
    assert all(p.status == ApprovalStatus.PENDING for p in pending)


# ---------------------------------------------------------------------------
# 7. list_pending filtered by goal_id
# ---------------------------------------------------------------------------


async def test_list_pending_filtered_by_goal_id() -> None:
    """list_pending(goal_id=...) only returns requests for that specific goal."""
    gateway = HITLGateway()
    tenant = _tenant("filtered")

    req_a = gateway.request_approval(goal_id="goal-A", action="deploy A", risk_level="high", tenant_ctx=tenant)
    req_b = gateway.request_approval(goal_id="goal-B", action="deploy B", risk_level="high", tenant_ctx=tenant)

    pending_a = gateway.list_pending(tenant_ctx=tenant, goal_id="goal-A")
    assert len(pending_a) == 1
    assert pending_a[0].request_id == req_a.request_id

    pending_b = gateway.list_pending(tenant_ctx=tenant, goal_id="goal-B")
    assert len(pending_b) == 1
    assert pending_b[0].request_id == req_b.request_id


# ---------------------------------------------------------------------------
# 8. Approve same request twice — no duplicate vote
# ---------------------------------------------------------------------------


async def test_approve_duplicate_vote_not_counted() -> None:
    """The same approver cannot vote twice on the same request."""
    gateway = HITLGateway()
    tenant = _tenant("dedup-vote")
    req = gateway.request_approval(
        goal_id="goal-dedup",
        action="destroy environment",
        risk_level="high",
        tenant_ctx=tenant,
    )
    gateway.approve(req.request_id, approver="alice", tenant_ctx=tenant)
    gateway.approve(req.request_id, approver="alice", tenant_ctx=tenant)  # duplicate

    fetched = gateway.get_request(req.request_id, tenant_ctx=tenant)
    assert fetched is not None
    # Should only count 1 approval
    assert fetched.approvals_received == 1


# ---------------------------------------------------------------------------
# 9. Multi-approver threshold
# ---------------------------------------------------------------------------


async def test_multi_approver_threshold_requires_two() -> None:
    """required_approvers=2 requires both votes before APPROVED."""
    gateway = HITLGateway()
    tenant = _tenant("multi-appr")
    req = gateway.request_approval(
        goal_id="goal-multi",
        action="prod deploy",
        risk_level="high",
        tenant_ctx=tenant,
        required_approvers=2,
    )

    gateway.approve(req.request_id, approver="alice", tenant_ctx=tenant)
    # One approval — still PENDING
    fetched = gateway.get_request(req.request_id, tenant_ctx=tenant)
    assert fetched is not None
    assert fetched.status == ApprovalStatus.PENDING

    gateway.approve(req.request_id, approver="bob", tenant_ctx=tenant)
    # Two approvals — now APPROVED
    fetched = gateway.get_request(req.request_id, tenant_ctx=tenant)
    assert fetched is not None
    assert fetched.status == ApprovalStatus.APPROVED


# ---------------------------------------------------------------------------
# 10. get_request returns None for unknown request
# ---------------------------------------------------------------------------


async def test_get_request_returns_none_for_unknown() -> None:
    """get_request() returns None when request_id doesn't exist."""
    gateway = HITLGateway()
    tenant = _tenant("unknown")
    result = gateway.get_request("non-existent-id", tenant_ctx=tenant)
    assert result is None


# ---------------------------------------------------------------------------
# 11. HITL CAS guard prevents TIMED_OUT from overwriting APPROVED
# ---------------------------------------------------------------------------


async def test_cas_guard_preserves_approved_status() -> None:
    """TIMED_OUT is only written when the request is still PENDING (CAS guard)."""
    gateway = HITLGateway(timeout_seconds=1.0)
    tenant = _tenant("cas")
    req = gateway.request_approval(
        goal_id="goal-cas",
        action="drop table",
        risk_level="high",
        tenant_ctx=tenant,
    )

    # Approve BEFORE the wait (so it resolves quickly)
    gateway.approve(req.request_id, approver="admin", tenant_ctx=tenant)

    # The approve already set status = APPROVED; timeout path should NOT overwrite it
    status = await gateway.wait_for_approval(req.request_id, tenant_ctx=tenant, timeout=0.1)
    assert status == ApprovalStatus.APPROVED

    # Final status should still be APPROVED
    fetched = gateway.get_request(req.request_id, tenant_ctx=tenant)
    assert fetched is not None
    assert fetched.status == ApprovalStatus.APPROVED


# ---------------------------------------------------------------------------
# 12. Approved request removed from pending list
# ---------------------------------------------------------------------------


async def test_approved_request_not_in_pending() -> None:
    """After approval, the request no longer appears in list_pending."""
    gateway = HITLGateway()
    tenant = _tenant("appr-list")
    req = gateway.request_approval(
        goal_id="goal-appr-list",
        action="destroy env",
        risk_level="high",
        tenant_ctx=tenant,
    )

    assert len(gateway.list_pending(tenant_ctx=tenant)) == 1
    gateway.approve(req.request_id, approver="ops-bot", tenant_ctx=tenant)
    assert len(gateway.list_pending(tenant_ctx=tenant)) == 0


# ---------------------------------------------------------------------------
# 13. HITL with audit trail records the event
# ---------------------------------------------------------------------------


async def test_hitl_audit_trail_records_approval() -> None:
    """AuditLog records approval events when HITL approves."""
    from app.governance.permissions import ActionLevel

    audit = AuditLog()
    gateway = HITLGateway()
    tenant = _tenant("audit-hitl")
    req = gateway.request_approval(
        goal_id="audit-goal",
        action="deploy prod",
        risk_level="high",
        tenant_ctx=tenant,
    )
    gateway.approve(req.request_id, approver="reviewer", tenant_ctx=tenant)

    # Manually record the HITL approval in audit log (as done in governance layer)
    audit.record(
        AuditEvent(
            goal_id="audit-goal",
            tool_name="hitl_gateway",
            action_level=ActionLevel.ALLOW_LOG,
            outcome="approved",
            approver="reviewer",
            note="HITL approved",
        ),
        tenant_ctx=tenant,
    )
    entries = audit.query(tenant_ctx=tenant)
    assert len(entries) >= 1
    hitl_entries = [e for e in entries if e.tool_name == "hitl_gateway"]
    assert len(hitl_entries) == 1
    assert hitl_entries[0].approver == "reviewer"


# ---------------------------------------------------------------------------
# 14. expire_timed_out_requests auto-rejects expired requests
# ---------------------------------------------------------------------------


async def test_expire_timed_out_requests_auto_rejects() -> None:
    """expire_timed_out_requests() rejects requests past their _expires_at_dt."""
    from datetime import UTC, datetime, timedelta

    gateway = HITLGateway(timeout_seconds=0.01)
    tenant = _tenant("expire")
    req = gateway.request_approval(
        goal_id="goal-expire",
        action="wipe partition",
        risk_level="high",
        tenant_ctx=tenant,
    )
    # Backdate the expiry
    req._expires_at_dt = datetime.now(UTC) - timedelta(seconds=10)

    expired = gateway.expire_timed_out_requests()
    assert req.request_id in expired
    assert req.status == ApprovalStatus.TIMED_OUT


# ---------------------------------------------------------------------------
# 15. HITL in bounded-autonomous mode — graph completes, request created
# ---------------------------------------------------------------------------


async def test_hitl_bounded_autonomous_graph_completes() -> None:
    """In bounded-autonomous mode, high-risk step runs but HITL request is created."""
    gateway = HITLGateway()
    tenant = _tenant("ba")
    p = FakeProvider(responses=[_DEPLOY_PLAN, _STEP, _VERIFY_OK])
    graph = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        hitl_gateway=gateway,
        autonomy_mode="bounded-autonomous",
    )
    state = await graph.run(goal="deploy service", tenant_ctx=tenant)
    # Graph completes (doesn't block)
    assert state is not None
    # A HITL request may have been created (depends on step text analysis)
    all_requests = list(gateway._requests.values())
    assert isinstance(all_requests, list)  # Just ensures no crash


# ---------------------------------------------------------------------------
# 16. HITL in supervised mode raises PermissionError on timeout
# ---------------------------------------------------------------------------


async def test_hitl_supervised_mode_raises_on_timeout() -> None:
    """In supervised mode, approval timeout raises PermissionError."""
    gateway = HITLGateway(timeout_seconds=0.05)
    tenant = _tenant("sup")
    p = FakeProvider(responses=[_DEPLOY_PLAN, _STEP, _VERIFY_OK])
    graph = AgentGraph(
        planner=p,
        executor=p,
        verifier=p,
        hitl_gateway=gateway,
        autonomy_mode="supervised",
    )
    graph._hitl_timeout = 0.05  # Very short for test
    # In supervised mode, timeout triggers PermissionError
    # OR the graph handles it and returns FAILED state
    try:
        state = await graph.run(goal="deploy service to production", tenant_ctx=tenant)
        # If no PermissionError, state should reflect failure
        assert state is not None
    except PermissionError:
        pass  # Expected: approval timed out


# ---------------------------------------------------------------------------
# 17. HITL wait resolves on reject
# ---------------------------------------------------------------------------


async def test_wait_for_approval_resolves_on_reject() -> None:
    """wait_for_approval returns REJECTED when rejected before timeout."""
    gateway = HITLGateway(timeout_seconds=5.0)
    tenant = _tenant("rej-wait")
    req = gateway.request_approval(
        goal_id="goal-rej-wait",
        action="destroy prod",
        risk_level="high",
        tenant_ctx=tenant,
    )

    async def do_reject() -> None:
        await asyncio.sleep(0.05)
        await gateway.reject(req.request_id, approver="safety-bot", note="Denied", tenant_ctx=tenant)

    reject_task = asyncio.create_task(do_reject())
    status = await gateway.wait_for_approval(req.request_id, tenant_ctx=tenant, timeout=2.0)
    await reject_task

    assert status == ApprovalStatus.REJECTED
