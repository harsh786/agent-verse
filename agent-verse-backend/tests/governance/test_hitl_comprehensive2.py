"""Comprehensive tests for app/governance/hitl.py — targeting 90%+ coverage."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.governance.hitl import (
    ApprovalRequest,
    ApprovalStatus,
    HITLGateway,
    _AwaitableBool,
)
from app.tenancy.context import TenantContext, PlanTier


def _ctx(tenant_id: str = "t1") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k1")


# ── _AwaitableBool ────────────────────────────────────────────────────────────

class TestAwaitableBool:
    def test_truthy(self) -> None:
        assert bool(_AwaitableBool(True)) is True

    def test_falsy(self) -> None:
        assert bool(_AwaitableBool(False)) is False

    def test_eq_bool(self) -> None:
        assert _AwaitableBool(True) == True  # noqa: E712
        assert _AwaitableBool(False) == False  # noqa: E712

    def test_eq_awaitable_bool(self) -> None:
        assert _AwaitableBool(True) == _AwaitableBool(True)
        assert _AwaitableBool(True) != _AwaitableBool(False)

    def test_repr(self) -> None:
        assert repr(_AwaitableBool(True)) == "True"

    def test_not_implemented_for_other_types(self) -> None:
        result = _AwaitableBool(True).__eq__("not_a_bool")
        assert result is NotImplemented


# ── ApprovalRequest ───────────────────────────────────────────────────────────

class TestApprovalRequest:
    def test_request_id_auto_generated(self) -> None:
        req = ApprovalRequest(goal_id="g1", action="deploy", risk_level="high")
        assert len(req.request_id) == 32

    def test_str_returns_request_id(self) -> None:
        req = ApprovalRequest(goal_id="g1", action="deploy", risk_level="high")
        assert str(req) == req.request_id

    def test_eq_with_string(self) -> None:
        req = ApprovalRequest(goal_id="g1", action="deploy", risk_level="high")
        assert req == req.request_id
        assert req != "different-id"

    def test_eq_with_approval_request(self) -> None:
        req1 = ApprovalRequest(goal_id="g1", action="a", risk_level="h")
        req2 = ApprovalRequest(goal_id="g1", action="a", risk_level="h")
        assert req1 != req2  # different request_ids

    def test_hash_same_as_request_id(self) -> None:
        req = ApprovalRequest(goal_id="g1", action="a", risk_level="h")
        assert hash(req) == hash(req.request_id)

    def test_eq_not_implemented_for_other(self) -> None:
        req = ApprovalRequest(goal_id="g1", action="a", risk_level="h")
        result = req.__eq__(123)
        assert result is NotImplemented

    def test_repr(self) -> None:
        req = ApprovalRequest(goal_id="g1", action="a", risk_level="h")
        assert "ApprovalRequest" in repr(req)
        assert req.request_id in repr(req)

    def test_default_status_is_pending(self) -> None:
        req = ApprovalRequest(goal_id="g1", action="a", risk_level="h")
        assert req.status == ApprovalStatus.PENDING

    def test_required_approvers_default_one(self) -> None:
        req = ApprovalRequest(goal_id="g1", action="a", risk_level="h")
        assert req.required_approvers == 1


# ── HITLGateway ───────────────────────────────────────────────────────────────

class TestHITLGateway:
    def test_request_approval_returns_approval_request(self) -> None:
        gw = HITLGateway()
        req = gw.request_approval(goal_id="g1", action="deploy", tenant_ctx=_ctx())
        assert isinstance(req, ApprovalRequest)
        assert req.goal_id == "g1"
        assert req.action == "deploy"

    def test_request_approval_step_description_alias(self) -> None:
        gw = HITLGateway()
        req = gw.request_approval(
            goal_id="g1",
            step_description="Deploying to prod",
            tenant_ctx=_ctx(),
        )
        assert req.action == "Deploying to prod"

    def test_request_approval_stored(self) -> None:
        gw = HITLGateway()
        ctx = _ctx()
        req = gw.request_approval(goal_id="g1", action="act", tenant_ctx=ctx)
        assert gw.get_request(req.request_id, tenant_ctx=ctx) is req

    def test_get_request_unknown_returns_none(self) -> None:
        gw = HITLGateway()
        result = gw.get_request("nonexistent", tenant_ctx=_ctx())
        assert result is None

    def test_approve_returns_awaitable_bool_true(self) -> None:
        gw = HITLGateway()
        ctx = _ctx()
        req = gw.request_approval(goal_id="g1", action="act", tenant_ctx=ctx)
        result = gw.approve(req.request_id, approver="alice", tenant_ctx=ctx)
        assert bool(result) is True

    def test_approve_sets_status_approved(self) -> None:
        gw = HITLGateway()
        ctx = _ctx()
        req = gw.request_approval(goal_id="g1", action="act", tenant_ctx=ctx)
        gw.approve(req.request_id, approver="alice", tenant_ctx=ctx)
        assert req.status == ApprovalStatus.APPROVED

    def test_approve_records_approver(self) -> None:
        gw = HITLGateway()
        ctx = _ctx()
        req = gw.request_approval(goal_id="g1", action="act", tenant_ctx=ctx)
        gw.approve(req.request_id, approver="alice", note="LGTM", tenant_ctx=ctx)
        assert req.approver == "alice"
        assert req.note == "LGTM"

    def test_approve_unknown_request_returns_false(self) -> None:
        gw = HITLGateway()
        result = gw.approve("nonexistent", approver="alice", tenant_ctx=_ctx())
        assert bool(result) is False

    def test_approve_already_approved_request_returns_false(self) -> None:
        gw = HITLGateway()
        ctx = _ctx()
        req = gw.request_approval(goal_id="g1", action="act", tenant_ctx=ctx)
        gw.approve(req.request_id, approver="alice", tenant_ctx=ctx)
        result = gw.approve(req.request_id, approver="bob", tenant_ctx=ctx)
        assert bool(result) is False

    def test_approve_multi_person_threshold(self) -> None:
        gw = HITLGateway()
        ctx = _ctx()
        req = gw.request_approval(
            goal_id="g1", action="act", tenant_ctx=ctx, required_approvers=2
        )
        gw.approve(req.request_id, approver="alice", tenant_ctx=ctx)
        assert req.status == ApprovalStatus.PENDING  # not yet
        gw.approve(req.request_id, approver="bob", tenant_ctx=ctx)
        assert req.status == ApprovalStatus.APPROVED

    def test_approve_duplicate_approver_ignored(self) -> None:
        gw = HITLGateway()
        ctx = _ctx()
        req = gw.request_approval(
            goal_id="g1", action="act", tenant_ctx=ctx, required_approvers=2
        )
        gw.approve(req.request_id, approver="alice", tenant_ctx=ctx)
        gw.approve(req.request_id, approver="alice", tenant_ctx=ctx)
        assert req.approvals_received == 1

    async def test_reject_sets_status_rejected(self) -> None:
        gw = HITLGateway()
        ctx = _ctx()
        req = gw.request_approval(goal_id="g1", action="act", tenant_ctx=ctx)
        result = await gw.reject(req.request_id, approver="bob", note="No", tenant_ctx=ctx)
        assert result is True
        assert req.status == ApprovalStatus.REJECTED

    async def test_reject_unknown_request_returns_false(self) -> None:
        gw = HITLGateway()
        result = await gw.reject("nonexistent", tenant_ctx=_ctx())
        assert result is False

    async def test_reject_already_closed_returns_false(self) -> None:
        gw = HITLGateway()
        ctx = _ctx()
        req = gw.request_approval(goal_id="g1", action="act", tenant_ctx=ctx)
        gw.approve(req.request_id, approver="alice", tenant_ctx=ctx)
        result = await gw.reject(req.request_id, tenant_ctx=ctx)
        assert result is False

    async def test_reject_publishes_to_redis_when_available(self) -> None:
        mock_redis = AsyncMock()
        gw = HITLGateway()
        gw._redis = mock_redis
        ctx = _ctx()
        req = gw.request_approval(goal_id="g1", action="act", tenant_ctx=ctx)
        await gw.reject(req.request_id, approver="alice", note="denied", tenant_ctx=ctx)
        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args[0]
        assert "hitl_rejected:g1" in call_args[0]

    async def test_reject_redis_error_suppressed(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = Exception("Redis down")
        gw = HITLGateway()
        gw._redis = mock_redis
        ctx = _ctx()
        req = gw.request_approval(goal_id="g1", action="act", tenant_ctx=ctx)
        result = await gw.reject(req.request_id, tenant_ctx=ctx)
        assert result is True  # error suppressed

    def test_list_pending_returns_only_pending(self) -> None:
        gw = HITLGateway()
        ctx = _ctx()
        req1 = gw.request_approval(goal_id="g1", action="a1", tenant_ctx=ctx)
        req2 = gw.request_approval(goal_id="g2", action="a2", tenant_ctx=ctx)
        gw.approve(req1.request_id, approver="alice", tenant_ctx=ctx)
        pending = gw.list_pending(tenant_ctx=ctx)
        assert req2 in pending
        assert req1 not in pending

    def test_list_pending_scoped_by_tenant(self) -> None:
        gw = HITLGateway()
        gw.request_approval(goal_id="g1", action="a", tenant_ctx=_ctx("t1"))
        gw.request_approval(goal_id="g2", action="a", tenant_ctx=_ctx("t2"))
        pending = gw.list_pending(tenant_ctx=_ctx("t1"))
        assert len(pending) == 1

    async def test_wait_for_approval_approved(self) -> None:
        gw = HITLGateway(timeout_seconds=5.0)
        ctx = _ctx()
        req = gw.request_approval(goal_id="g1", action="act", tenant_ctx=ctx)

        async def approve_later() -> None:
            await asyncio.sleep(0.05)
            gw.approve(req.request_id, approver="alice", tenant_ctx=ctx)

        asyncio.create_task(approve_later())
        status = await gw.wait_for_approval(req.request_id, tenant_ctx=ctx)
        assert status == ApprovalStatus.APPROVED

    async def test_wait_for_approval_timeout(self) -> None:
        gw = HITLGateway(timeout_seconds=0.1)
        ctx = _ctx()
        req = gw.request_approval(goal_id="g1", action="act", tenant_ctx=ctx)
        status = await gw.wait_for_approval(req.request_id, tenant_ctx=ctx, timeout=0.1)
        assert status == ApprovalStatus.TIMED_OUT

    async def test_wait_for_approval_unknown_request(self) -> None:
        gw = HITLGateway()
        status = await gw.wait_for_approval("nonexistent", tenant_ctx=_ctx())
        assert status == ApprovalStatus.REJECTED

    def test_expire_timed_out_requests(self) -> None:
        from datetime import UTC, datetime, timedelta
        gw = HITLGateway()
        ctx = _ctx()
        req = gw.request_approval(goal_id="g1", action="act", tenant_ctx=ctx)
        # Force expiry by setting _expires_at_dt in the past
        req._expires_at_dt = datetime.now(UTC) - timedelta(seconds=1)
        expired = gw.expire_timed_out_requests()
        assert req.request_id in expired
        assert req.status == ApprovalStatus.TIMED_OUT

    def test_expire_skips_non_pending(self) -> None:
        from datetime import UTC, datetime, timedelta
        gw = HITLGateway()
        ctx = _ctx()
        req = gw.request_approval(goal_id="g1", action="act", tenant_ctx=ctx)
        gw.approve(req.request_id, approver="alice", tenant_ctx=ctx)
        req._expires_at_dt = datetime.now(UTC) - timedelta(seconds=1)
        expired = gw.expire_timed_out_requests()
        assert req.request_id not in expired

    async def test_publish_resolution_pushes_to_redis(self) -> None:
        mock_redis = AsyncMock()
        gw = HITLGateway()
        gw._redis = mock_redis
        await gw.publish_resolution("req1", "approve", "alice", "looks good")
        mock_redis.rpush.assert_called_once()
        mock_redis.expire.assert_called_once()

    async def test_publish_resolution_no_redis_noop(self) -> None:
        gw = HITLGateway()
        gw._redis = None
        await gw.publish_resolution("req1", "approve", "alice")  # no exception

    async def test_publish_resolution_redis_error_suppressed(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.rpush.side_effect = Exception("Redis down")
        gw = HITLGateway()
        gw._redis = mock_redis
        await gw.publish_resolution("req1", "approve", "alice")  # no exception

    async def test_wait_for_result_no_redis_returns_none(self) -> None:
        gw = HITLGateway()
        gw._redis = None
        result = await gw._wait_for_result("req1", timeout=0.1)
        assert result is None

    async def test_wait_for_result_returns_payload(self) -> None:
        mock_redis = AsyncMock()
        payload = {"action": "approve", "approver": "alice", "note": "ok"}
        import json
        mock_redis.blpop = AsyncMock(
            return_value=("key", json.dumps(payload).encode())
        )
        gw = HITLGateway()
        gw._redis = mock_redis
        result = await gw._wait_for_result("req1", timeout=5.0)
        assert result == payload

    async def test_wait_for_result_timeout_returns_none(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.blpop = AsyncMock(return_value=None)
        gw = HITLGateway()
        gw._redis = mock_redis
        result = await gw._wait_for_result("req1", timeout=0.05)
        assert result is None

    async def test_load_pending_from_db_no_db_returns_0(self) -> None:
        gw = HITLGateway()
        count = await gw.load_pending_from_db(None, "t1")
        assert count == 0

    async def test_load_pending_from_db_error_returns_0(self) -> None:
        async def bad_factory():
            raise RuntimeError("DB error")
        gw = HITLGateway()
        count = await gw.load_pending_from_db(bad_factory, "t1")
        assert count == 0

    async def test_load_pending_from_db_full_no_db(self) -> None:
        gw = HITLGateway()
        count = await gw.load_pending_from_db_full(None)
        assert count == 0

    async def test_startup_restore_no_db_returns_0(self) -> None:
        gw = HITLGateway()
        count = await gw.startup_restore(None)
        assert count == 0

    def test_request_approval_with_notification_service(self) -> None:
        gw = HITLGateway()
        mock_ns = MagicMock()
        gw._notification_service = mock_ns
        mock_loop = MagicMock()
        mock_loop.create_task = MagicMock()

        with patch("asyncio.get_running_loop", return_value=mock_loop):
            gw.request_approval(goal_id="g1", action="act", tenant_ctx=_ctx())

        mock_loop.create_task.assert_called()

    def test_request_approval_notification_no_loop(self) -> None:
        gw = HITLGateway()
        mock_ns = MagicMock()
        gw._notification_service = mock_ns

        with patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")):
            req = gw.request_approval(goal_id="g1", action="act", tenant_ctx=_ctx())

        assert req is not None  # should not raise
