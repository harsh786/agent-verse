"""B7-NEW-1: a goal approval's ``hitl.approved`` event is published before the
decision call returns.

``HITLGateway.approve_async`` (every request path: ``POST
/governance/approvals/{id}/approve``, batch approve, Slack/Teams buttons) used to
hand the ``hitl.approved`` trigger event and the cross-replica resolution to
fire-and-forget ``loop.create_task`` calls. A process shutting down (a redeploy,
a crashed replica) between the response and the task running lost the event,
so hitl_approved triggers silently never fired for that decision — while the
symmetric ``reject`` path always awaited its publish.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.governance.hitl import HITLGateway
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-b7-approve", plan=PlanTier.ENTERPRISE, api_key_id="k")


def _published(redis: AsyncMock, channel: str) -> list[dict]:
    return [json.loads(c.args[1]) for c in redis.publish.await_args_list if c.args[0] == channel]


def _gateway_with_db() -> tuple[HITLGateway, AsyncMock, str]:
    gw = HITLGateway()
    rid = str(gw.request_approval(goal_id="g-b7", action="deploy to prod", tenant_ctx=CTX))
    redis = AsyncMock()
    gw._redis = redis
    # A DB-backed gateway (as on the live stack): resolve + CAS through Postgres.
    gw._db_session_factory = object()
    req = gw.get_request(rid, tenant_ctx=CTX)
    gw.aget_request = AsyncMock(return_value=req)  # type: ignore[method-assign]
    gw._db_update_resolution = AsyncMock(return_value=True)  # type: ignore[method-assign]
    gw._goal_agent_id = AsyncMock(return_value="agent-1")  # type: ignore[method-assign]
    return gw, redis, rid


@pytest.mark.asyncio
async def test_approve_async_publishes_the_trigger_event_before_returning() -> None:
    gw, redis, rid = _gateway_with_db()

    assert await gw.approve_async(rid, approver="ada", tenant_ctx=CTX)

    # No yield to the event loop after the call: the event is already out.
    (event,) = _published(redis, "hitl.approved")
    assert event["request_id"] == rid and event["goal_id"] == "g-b7"
    assert event["approver"] == "ada"
    # ...and the cross-replica resolution for a waiter on another replica.
    assert [c.args[0] for c in redis.rpush.await_args_list] == [f"hitl_result:{rid}"]


@pytest.mark.asyncio
async def test_approve_async_publish_failure_does_not_undo_the_decision() -> None:
    gw, redis, rid = _gateway_with_db()
    redis.publish.side_effect = ConnectionError("redis down")
    redis.xadd.side_effect = ConnectionError("redis down")

    assert await gw.approve_async(rid, approver="ada", tenant_ctx=CTX)
    req = gw.get_request(rid, tenant_ctx=CTX)
    assert req is not None and str(getattr(req.status, "value", req.status)) == "approved"


@pytest.mark.asyncio
async def test_lost_cas_publishes_nothing() -> None:
    gw, redis, rid = _gateway_with_db()
    gw._db_update_resolution = AsyncMock(return_value=False)  # type: ignore[method-assign]
    gw._heal_from_db = AsyncMock()  # type: ignore[method-assign]

    assert not await gw.approve_async(rid, approver="ada", tenant_ctx=CTX)
    assert _published(redis, "hitl.approved") == []
