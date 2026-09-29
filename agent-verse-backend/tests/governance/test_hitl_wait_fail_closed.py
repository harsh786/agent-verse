"""Regression: a Redis error during the HITL wait must never read as "not rejected".

``_wait_for_result`` swallowed a BLPOP exception and returned ``None``; the
Redis task therefore finished first and ``wait_for_approval`` returned the
request's current status -- ``PENDING`` -- immediately. The supervised
``write_high`` tool path only blocked on REJECTED/TIMED_OUT, so a brief Redis
blip ran a high-risk tool with no human approval.

The wait now fails closed: it never returns PENDING, and it only returns
APPROVED when an approval was actually observed.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.governance.hitl import ApprovalStatus, HITLGateway, HITLWaitUnavailableError
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-wait", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _broken_redis() -> Any:
    redis = MagicMock()
    redis.blpop = AsyncMock(side_effect=ConnectionError("redis blip"))
    return redis


@pytest.mark.asyncio
async def test_wait_for_result_raises_on_redis_error() -> None:
    gw = HITLGateway()
    gw._redis = _broken_redis()
    with pytest.raises(HITLWaitUnavailableError):
        await gw._wait_for_result("req-1", timeout=1.0)


@pytest.mark.asyncio
async def test_redis_error_does_not_return_pending() -> None:
    gw = HITLGateway()
    gw._redis = _broken_redis()
    req = gw.request_approval(goal_id="g", action="deploy prod", tenant_ctx=CTX)

    status = await gw.wait_for_approval(str(req), tenant_ctx=CTX, timeout=0.3)

    assert status != ApprovalStatus.PENDING
    assert status == ApprovalStatus.TIMED_OUT


@pytest.mark.asyncio
async def test_redis_error_still_honours_a_local_approval() -> None:
    gw = HITLGateway()
    gw._redis = MagicMock()
    gw._redis.blpop = AsyncMock(side_effect=ConnectionError("redis blip"))
    gw._redis.rpush = AsyncMock(side_effect=ConnectionError("redis blip"))
    gw._redis.expire = AsyncMock()
    gw._redis.publish = AsyncMock()
    req = gw.request_approval(goal_id="g", action="deploy prod", tenant_ctx=CTX)

    async def _approve_later() -> None:
        await asyncio.sleep(0.1)
        gw.approve(str(req), approver="alice", tenant_ctx=CTX)

    task = asyncio.create_task(_approve_later())
    status = await gw.wait_for_approval(str(req), tenant_ctx=CTX, timeout=2.0)
    await task

    assert status == ApprovalStatus.APPROVED


@pytest.mark.asyncio
async def test_redis_error_falls_back_to_db_status() -> None:
    """With Redis down, a resolution made on another replica is read from Postgres."""
    gw = HITLGateway()
    gw._redis = _broken_redis()
    gw._db_session_factory = object()  # only presence matters; the read is stubbed
    gw._db_read_status = AsyncMock(return_value="rejected")  # type: ignore[method-assign]
    req = gw.request_approval(goal_id="g", action="deploy prod", tenant_ctx=CTX)

    status = await gw.wait_for_approval(str(req), tenant_ctx=CTX, timeout=2.0)

    assert status == ApprovalStatus.REJECTED


@pytest.mark.asyncio
async def test_wait_never_returns_pending_when_redis_times_out_first() -> None:
    """A BLPOP that returns ``None`` at its deadline must not leak PENDING either."""
    gw = HITLGateway()
    gw._redis = MagicMock()
    gw._redis.blpop = AsyncMock(return_value=None)
    req = gw.request_approval(goal_id="g", action="deploy prod", tenant_ctx=CTX)

    status = await gw.wait_for_approval(str(req), tenant_ctx=CTX, timeout=0.2)

    assert status == ApprovalStatus.TIMED_OUT
