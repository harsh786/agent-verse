"""HITL resolution without a startup warm cache.

Startup no longer pulls every tenant's pending approvals into memory (that scan
ran through system_session and failed under the API's NOBYPASSRLS role). So a
request path that meets a cache miss must resolve the approval in Postgres,
scoped to the caller's tenant, instead of answering "not found".
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from app.governance.hitl import ApprovalRequest, HITLGateway
from app.tenancy.context import PlanTier, TenantContext

CTX_A = TenantContext(tenant_id="tenant-a", plan=PlanTier.PROFESSIONAL, api_key_id="k-a")
CTX_B = TenantContext(tenant_id="tenant-b", plan=PlanTier.PROFESSIONAL, api_key_id="k-b")


def _gateway(rows: dict[tuple[str, str], dict[str, Any]]) -> HITLGateway:
    """A gateway whose DB holds ``rows`` keyed by (tenant_id, request_id)."""
    gw = HITLGateway(db_session_factory=object())

    async def _fetch(request_id: str, tenant_id: str) -> dict[str, Any] | None:
        return rows.get((tenant_id, request_id))

    gw._db_fetch_request = _fetch  # type: ignore[method-assign]
    gw._db_update_resolution = AsyncMock(return_value=True)  # type: ignore[method-assign]
    return gw


def _row(request_id: str, tenant_id: str, status: str = "pending") -> dict[str, Any]:
    return {
        "id": request_id,
        "tenant_id": tenant_id,
        "goal_id": "goal-1",
        "action": "deploy to prod",
        "risk_level": "high",
        "status": status,
    }


async def test_reject_resolves_an_uncached_pending_approval_from_the_db() -> None:
    gw = _gateway({("tenant-a", "req-1"): _row("req-1", "tenant-a")})
    assert gw._requests == {}  # fresh process: nothing warmed

    assert await gw.reject("req-1", approver="op", note="no", tenant_ctx=CTX_A) is True
    # CORE-28: a row read only to resolve it is not cached (no waiter here).
    assert gw.get_request("req-1", tenant_ctx=CTX_A) is None
    gw._db_update_resolution.assert_awaited_once_with(  # type: ignore[attr-defined]
        "req-1", "tenant-a", "rejected", "op", "no"
    )


async def test_reject_cannot_reach_another_tenants_approval() -> None:
    gw = _gateway({("tenant-a", "req-1"): _row("req-1", "tenant-a")})

    assert await gw.reject("req-1", approver="op", tenant_ctx=CTX_B) is False
    assert gw.get_request("req-1", tenant_ctx=CTX_B) is None
    gw._db_update_resolution.assert_not_awaited()  # type: ignore[attr-defined]


async def test_reject_of_an_already_resolved_db_row_fails() -> None:
    gw = _gateway({("tenant-a", "req-1"): _row("req-1", "tenant-a", status="approved")})

    assert await gw.reject("req-1", approver="op", tenant_ctx=CTX_A) is False
    gw._db_update_resolution.assert_not_awaited()  # type: ignore[attr-defined]


async def test_reject_of_an_unknown_request_fails() -> None:
    gw = _gateway({})
    assert await gw.reject("nope", approver="op", tenant_ctx=CTX_A) is False


async def test_approve_async_resolves_an_uncached_pending_approval() -> None:
    gw = _gateway({("tenant-a", "req-2"): _row("req-2", "tenant-a")})
    gw._reconcile_after_db_resolution = AsyncMock()  # type: ignore[method-assign]
    gw._schedule_db_resolution = lambda *a, **k: None  # type: ignore[method-assign]

    assert await gw.approve_async("req-2", approver="op", tenant_ctx=CTX_A) is True
    gw._db_update_resolution.assert_awaited_once()  # type: ignore[attr-defined]
    assert gw.get_request("req-2", tenant_ctx=CTX_A) is None  # CORE-28: not cached
    assert await gw.approve_async("req-2", approver="op", tenant_ctx=CTX_B) is False


async def test_cached_request_still_takes_the_fast_path() -> None:
    gw = _gateway({})
    req = ApprovalRequest(goal_id="g", action="a", risk_level="high", request_id="req-3")
    gw._requests[("tenant-a", "req-3")] = req
    gw.aget_request = AsyncMock()  # type: ignore[method-assign]

    assert await gw.reject("req-3", approver="op", tenant_ctx=CTX_A) is True
    gw.aget_request.assert_not_awaited()
