"""CORE-28: the HITL gateway's process-local request cache is bounded.

Every approval created or read from Postgres used to be inserted into
``HITLGateway._requests`` and never removed, so a long-running API replica
leaked memory proportional to total approval volume (and the timeout scan
walked the whole dict). Postgres is the only source of truth; the cache now
holds only what a live in-process waiter needs, under an LRU cap.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.governance.hitl import ApprovalStatus, HITLGateway
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-cache", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _FakeApprovalDB:
    """approval_requests stand-in bound onto a gateway's DB hooks."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def bind(self, gw: HITLGateway) -> HITLGateway:
        db = self

        async def _persist(req: Any, tenant_id: str, *, raise_errors: bool = False) -> None:
            db.rows[req.request_id] = {
                "id": req.request_id, "tenant_id": tenant_id, "goal_id": req.goal_id,
                "action": req.action, "risk_level": req.risk_level, "status": "pending",
                "required_approvers": 1,
            }

        async def _fetch(request_id: str, tenant_id: str) -> Any:
            row = db.rows.get(request_id)
            return dict(row) if row else None

        async def _resolve(
            request_id: str, tenant_id: str, status: str, approver: str = "", note: str = ""
        ) -> bool:
            row = db.rows.get(request_id)
            if row is None or row["status"] != "pending":
                return False
            row["status"] = status
            return True

        async def _read_status(request_id: str, tenant_id: str) -> str | None:
            row = db.rows.get(request_id)
            return str(row["status"]) if row else None

        gw._db_persist_approval_request = _persist  # type: ignore[method-assign]
        gw._db_fetch_request = _fetch  # type: ignore[method-assign]
        gw._db_update_resolution = _resolve  # type: ignore[method-assign]
        gw._db_read_status = _read_status  # type: ignore[method-assign]
        return gw


@pytest.mark.asyncio
async def test_creating_and_resolving_10k_requests_keeps_the_cache_bounded() -> None:
    db = _FakeApprovalDB()
    gw = db.bind(HITLGateway(db_session_factory=object()))
    for i in range(10_000):
        rid = await gw.request_approval_async(goal_id=f"g{i}", action="x", tenant_ctx=CTX)
        assert await gw.approve_async(rid, approver="ops", tenant_ctx=CTX)
    assert len(gw._requests) <= HITLGateway.CACHE_MAX_ENTRIES
    assert len(db.rows) == 10_000  # Postgres stays the source of truth


def test_in_memory_gateway_evicts_resolved_requests_but_keeps_pending_ones() -> None:
    gw = HITLGateway(cache_max_entries=50)
    pending = [str(gw.request_approval(goal_id="keep", tenant_ctx=CTX)) for _ in range(10)]
    for i in range(500):
        rid = str(gw.request_approval(goal_id=f"g{i}", tenant_ctx=CTX))
        assert gw.approve(rid, approver="ops", tenant_ctx=CTX)
    assert len(gw._requests) <= 50
    # Without a DB the cache IS the store: pending requests are never evicted.
    assert {r.request_id for r in gw.list_pending(tenant_ctx=CTX)} == set(pending)


@pytest.mark.asyncio
async def test_listing_and_reading_db_rows_does_not_grow_the_cache() -> None:
    db = _FakeApprovalDB()
    gw = db.bind(HITLGateway(db_session_factory=object()))
    for i in range(500):
        db.rows[f"r{i}"] = {
            "id": f"r{i}", "tenant_id": CTX.tenant_id, "goal_id": "g", "action": "a",
            "risk_level": "high", "status": "pending", "required_approvers": 1,
        }
    for i in range(500):
        assert await gw.aget_request(f"r{i}", tenant_ctx=CTX) is not None
    assert len(gw._requests) == 0


@pytest.mark.asyncio
async def test_waiter_is_unblocked_after_unrelated_entries_are_evicted() -> None:
    db = _FakeApprovalDB()
    gw = db.bind(HITLGateway(db_session_factory=object(), cache_max_entries=20))
    rid = await gw.request_approval_async(goal_id="waited", action="x", tenant_ctx=CTX)
    waiter = asyncio.create_task(gw.wait_for_approval(rid, tenant_ctx=CTX, timeout=5))
    await asyncio.sleep(0)
    for i in range(200):  # churn far past the cap while the waiter is parked
        other = await gw.request_approval_async(goal_id=f"g{i}", action="x", tenant_ctx=CTX)
        await gw.approve_async(other, approver="ops", tenant_ctx=CTX)
    assert len(gw._requests) <= 20
    assert await gw.approve_async(rid, approver="ops", tenant_ctx=CTX)
    assert await asyncio.wait_for(waiter, 2) == ApprovalStatus.APPROVED
    # The resolved, no-longer-waited request is dropped from the cache.
    assert (CTX.tenant_id, rid) not in gw._requests


@pytest.mark.asyncio
async def test_waiter_on_a_request_raised_elsewhere_is_released_by_a_local_approval() -> None:
    db = _FakeApprovalDB()
    raiser = db.bind(HITLGateway(db_session_factory=object()))
    waiter_gw = db.bind(HITLGateway(db_session_factory=object()))
    rid = await raiser.request_approval_async(goal_id="g", action="x", tenant_ctx=CTX)
    waiter = asyncio.create_task(waiter_gw.wait_for_approval(rid, tenant_ctx=CTX, timeout=5))
    await asyncio.sleep(0.05)
    assert await waiter_gw.approve_async(rid, approver="ops", tenant_ctx=CTX)
    assert await asyncio.wait_for(waiter, 2) == ApprovalStatus.APPROVED
