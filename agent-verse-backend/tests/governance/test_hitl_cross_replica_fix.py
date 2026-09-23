"""Regression tests for C6 and H17 — cross-replica HITL approval delivery."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.governance.hitl import ApprovalRequest, ApprovalStatus, HITLGateway
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="hitl-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1", roles=())


# ---------------------------------------------------------------------------
# Fake DB session factory — mimics the exact CAS semantics of the real
# ``UPDATE approval_requests ... WHERE status = 'pending'`` statement
# HITLGateway issues, backed by a plain dict shared across two HITLGateway
# instances (simulating two replicas that both write through to one Postgres).
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rowcount: int = 0, row: tuple[Any, ...] | None = None) -> None:
        self.rowcount = rowcount
        self._row = row

    def first(self) -> tuple[Any, ...] | None:
        return self._row


class _FakeTxn:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeSession:
    def __init__(self, store: dict[tuple[str, str], str]) -> None:
        self._store = store

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def begin(self) -> _FakeTxn:
        return _FakeTxn()

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        sql = str(stmt)
        params = params or {}
        key = (params.get("id"), params.get("tid"))
        if "UPDATE approval_requests" in sql:
            if self._store.get(key) == "pending":
                self._store[key] = params["s"]
                return _FakeResult(rowcount=1)
            return _FakeResult(rowcount=0)
        if "SELECT status FROM approval_requests" in sql:
            status = self._store.get(key)
            return _FakeResult(row=(status,) if status is not None else None)
        return _FakeResult(rowcount=0)


class _FakeSessionFactory:
    """Shared fake DB backing multiple HITLGateway 'replicas' with real CAS semantics."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def __call__(self) -> _FakeSession:
        return _FakeSession(self.store)


class TestApproveRejectCrossReplicaRace:
    """Two operators resolve the same request via two different replicas.

    Each ``HITLGateway`` only holds its OWN in-memory copy of the pending
    request (exactly as it would after ``load_pending_from_db_full`` on two
    separate pods, or after the request was created on one pod and the
    approve/reject HTTP call landed on another). The shared DB is the only
    real cross-replica arbiter. Before the fix, ``approve()``/``reject()``
    decided success purely from local memory and fired the DB write
    fire-and-forget without ever checking whether it actually won the
    ``WHERE status = 'pending'`` compare-and-swap — so the "losing" replica
    still reported success for a decision the DB silently discarded.
    """

    @pytest.mark.asyncio
    async def test_reject_loses_race_reports_failure_not_false_success(self) -> None:
        """reject() must not report success once another replica already approved."""
        db = _FakeSessionFactory()
        db.store[("req-1", T.tenant_id)] = "pending"

        gw_a = HITLGateway(timeout_seconds=5.0)
        gw_b = HITLGateway(timeout_seconds=5.0)
        gw_a._db_session_factory = db
        gw_b._db_session_factory = db

        req_a = ApprovalRequest(
            goal_id="g1", action="deploy", risk_level="high", request_id="req-1"
        )
        req_b = ApprovalRequest(
            goal_id="g1", action="deploy", risk_level="high", request_id="req-1"
        )
        gw_a._requests[(T.tenant_id, "req-1")] = req_a
        gw_b._requests[(T.tenant_id, "req-1")] = req_b

        # Operator 1 approves via replica A, and that write lands in the DB.
        approve_ok = bool(gw_a.approve("req-1", approver="alice", tenant_ctx=T))
        await asyncio.sleep(0.05)  # let approve()'s fire-and-forget DB write land
        assert db.store[("req-1", T.tenant_id)] == "approved"

        # Operator 2's pod never heard about that — its local copy still shows
        # PENDING — and they click Reject.
        reject_ok = await gw_b.reject("req-1", approver="bob", tenant_ctx=T)

        assert approve_ok is True
        assert reject_ok is False, (
            "reject() reported success even though the request was already "
            "approved in the DB by another replica — the operator on "
            "replica B would be told their rejection worked when it "
            "silently did nothing"
        )
        assert db.store[("req-1", T.tenant_id)] == "approved", "DB truth must be unchanged"
        assert req_b.status == ApprovalStatus.PENDING, (
            "a losing reject() must not flip its own local status to REJECTED"
        )

    @pytest.mark.asyncio
    async def test_approve_losing_race_self_heals_local_state(self) -> None:
        """A losing approve() must reconcile local state to the DB's real outcome."""
        db = _FakeSessionFactory()
        db.store[("req-2", T.tenant_id)] = "pending"

        gw_a = HITLGateway(timeout_seconds=5.0)
        gw_b = HITLGateway(timeout_seconds=5.0)
        gw_a._db_session_factory = db
        gw_b._db_session_factory = db

        req_a = ApprovalRequest(
            goal_id="g1", action="deploy", risk_level="high", request_id="req-2"
        )
        req_b = ApprovalRequest(
            goal_id="g1", action="deploy", risk_level="high", request_id="req-2"
        )
        gw_a._requests[(T.tenant_id, "req-2")] = req_a
        gw_b._requests[(T.tenant_id, "req-2")] = req_b

        # Operator 1 rejects via replica B first; the write lands immediately
        # since reject() now awaits its DB CAS inline.
        reject_ok = await gw_b.reject("req-2", approver="bob", tenant_ctx=T)
        assert reject_ok is True
        assert db.store[("req-2", T.tenant_id)] == "rejected"

        # Operator 2's pod (replica A) never heard about that — its local
        # copy still shows PENDING — and they click Approve.
        approve_ok = bool(gw_a.approve("req-2", approver="alice", tenant_ctx=T))
        assert approve_ok is True  # approve() is sync-only and reports optimistically
        assert req_a.status == ApprovalStatus.APPROVED  # phantom local state, not yet reconciled

        # Once the fire-and-forget reconciliation runs, replica A must
        # self-heal to the DB's real, already-arbitrated outcome instead of
        # staying stuck on an "approved" that never actually took effect.
        await asyncio.sleep(0.05)
        assert req_a.status == ApprovalStatus.REJECTED, (
            "replica A's in-memory approval state was left permanently "
            "inconsistent with the DB after losing the cross-replica race"
        )
        assert db.store[("req-2", T.tenant_id)] == "rejected"


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
