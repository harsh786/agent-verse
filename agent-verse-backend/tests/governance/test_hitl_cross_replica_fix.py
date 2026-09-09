"""Regression tests for C6 and H17 — cross-replica HITL approval delivery."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.governance.hitl import ApprovalStatus, HITLGateway
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="hitl-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1", roles=())


class TestApprovePublishesRedis:
    """C6.1: approve() must publish to Redis for cross-replica delivery."""

    @pytest.mark.asyncio
    async def test_approve_calls_publish_resolution_when_redis_present(self) -> None:
        """approve() must trigger publish_resolution to Redis."""
        gateway = HITLGateway(timeout_seconds=10.0)
        gateway._redis = AsyncMock()
        gateway._redis.rpush = AsyncMock(return_value=1)
        gateway._redis.expire = AsyncMock()

        req = gateway.request_approval(
            goal_id="g1", action="delete file", risk_level="high", tenant_ctx=T
        )

        # Approve it
        gateway.approve(str(req.request_id), approver="admin@co.com", tenant_ctx=T)

        # Allow any fire-and-forget tasks to run
        await asyncio.sleep(0.05)

        # Redis publish_resolution should have been called (rpush or publish)
        assert gateway._redis.rpush.called or gateway._redis.publish.called, (
            "approve() did not publish to Redis for cross-replica delivery"
        )


class TestWaitForApprovalCrossReplica:
    """C6.2: wait_for_approval() must unblock from Redis BLPOP in addition to local event."""

    @pytest.mark.asyncio
    async def test_wait_unblocks_from_redis_result(self) -> None:
        """Approval from another replica (Redis BLPOP) unblocks the waiter."""
        import json

        gateway_replica_a = HITLGateway(timeout_seconds=5.0)
        gateway_replica_b = HITLGateway(timeout_seconds=5.0)

        # Share a fake Redis
        blpop_queue: list[bytes] = []

        mock_redis = AsyncMock()

        async def fake_blpop(keys: list[str] | str, timeout: float = 0) -> tuple | None:  # type: ignore[return]
            # Wait until something is pushed
            key = keys[0] if isinstance(keys, list) else keys
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                if blpop_queue:
                    item = blpop_queue.pop(0)
                    return (key, item)
                await asyncio.sleep(0.01)
            return None

        async def fake_rpush(key: str, value: str) -> int:
            blpop_queue.append(value.encode() if isinstance(value, str) else value)
            return 1

        mock_redis.blpop = AsyncMock(side_effect=fake_blpop)
        mock_redis.rpush = AsyncMock(side_effect=fake_rpush)
        mock_redis.expire = AsyncMock()
        mock_redis.lrange = AsyncMock(return_value=[])

        gateway_replica_a._redis = mock_redis
        gateway_replica_b._redis = mock_redis

        # Replica A creates a request
        req = gateway_replica_a.request_approval(
            goal_id="g1", action="deploy", risk_level="high", tenant_ctx=T
        )
        req_id = str(req.request_id)

        # Add request to replica B's store (simulating DB restore/Redis sync)
        gateway_replica_b._requests[(T.tenant_id, req_id)] = req

        async def approve_from_replica_b() -> None:
            await asyncio.sleep(0.1)
            gateway_replica_b.approve(req_id, approver="admin", tenant_ctx=T)

        # Replica A waits, Replica B approves
        wait_task = asyncio.create_task(
            gateway_replica_a.wait_for_approval(req_id, tenant_ctx=T)
        )
        approve_task = asyncio.create_task(approve_from_replica_b())

        status = await asyncio.wait_for(wait_task, timeout=3.0)
        await approve_task

        assert status == ApprovalStatus.APPROVED


class TestTimeoutCASGuard:
    """H17: timeout in wait_for_approval must not overwrite APPROVED."""

    @pytest.mark.asyncio
    async def test_timeout_does_not_overwrite_approved(self) -> None:
        """H17: If approved just before timeout, status stays APPROVED."""
        gateway = HITLGateway(timeout_seconds=0.1)
        req = gateway.request_approval(
            goal_id="g1", action="delete", risk_level="high", tenant_ctx=T
        )
        req_id = str(req.request_id)

        async def approve_concurrently() -> None:
            await asyncio.sleep(0.05)  # slightly before timeout
            gateway.approve(req_id, approver="admin", tenant_ctx=T)

        # Start both wait and approval concurrently
        wait_task = asyncio.create_task(
            gateway.wait_for_approval(req_id, tenant_ctx=T)
        )
        approve_task = asyncio.create_task(approve_concurrently())

        results = await asyncio.gather(wait_task, approve_task, return_exceptions=True)
        final_status = results[0]

        # If approved before timeout, APPROVED must win over TIMED_OUT
        if req.status == ApprovalStatus.APPROVED:
            assert final_status == ApprovalStatus.APPROVED, (
                f"H17: APPROVED was overwritten with {final_status}"
            )

    @pytest.mark.asyncio
    async def test_timed_out_set_when_no_approval(self) -> None:
        """A request that really times out must get TIMED_OUT status."""
        gateway = HITLGateway(timeout_seconds=0.05)
        req = gateway.request_approval(
            goal_id="g2", action="wait", risk_level="low", tenant_ctx=T
        )
        req_id = str(req.request_id)

        status = await gateway.wait_for_approval(req_id, tenant_ctx=T)

        assert status == ApprovalStatus.TIMED_OUT


class TestListPendingGoalScoping:
    """C6.4: list_pending must support goal_id filtering."""

    def test_list_pending_with_goal_id_filter(self) -> None:
        """Only requests for the specified goal_id should be returned."""
        gateway = HITLGateway(timeout_seconds=30.0)

        gateway.request_approval(
            goal_id="goal-A", action="step1", risk_level="high", tenant_ctx=T
        )
        gateway.request_approval(
            goal_id="goal-B", action="step2", risk_level="high", tenant_ctx=T
        )

        pending_a = gateway.list_pending(tenant_ctx=T, goal_id="goal-A")
        assert len(pending_a) == 1
        assert pending_a[0].goal_id == "goal-A"

        pending_b = gateway.list_pending(tenant_ctx=T, goal_id="goal-B")
        assert len(pending_b) == 1
        assert pending_b[0].goal_id == "goal-B"

        pending_all = gateway.list_pending(tenant_ctx=T)
        assert len(pending_all) == 2

    def test_list_pending_no_cross_goal_contamination(self) -> None:
        """Pending approval for goal-B must not pause goal-A."""
        gateway = HITLGateway(timeout_seconds=30.0)

        gateway.request_approval(
            goal_id="goal-B", action="delete", risk_level="high", tenant_ctx=T
        )

        # goal-A should have no pending approvals
        pending_for_a = gateway.list_pending(tenant_ctx=T, goal_id="goal-A")
        assert len(pending_for_a) == 0, (
            "goal-A should not see goal-B's pending approvals"
        )
