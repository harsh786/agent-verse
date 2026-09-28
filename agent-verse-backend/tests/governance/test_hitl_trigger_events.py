"""Regression: HITL_APPROVED / HITL_REJECTED triggers could never fire.

HITLTriggerConsumer subscribes to ``hitl.approved`` / ``hitl.rejected`` but the
HITL gateway never published to those channels.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.governance.hitl import HITLGateway
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.consumers.hitl import HITLTriggerConsumer

CTX = TenantContext(tenant_id="t-hitl", plan=PlanTier.STARTER, api_key_id="k")


def _channel_payloads(redis: AsyncMock, channel: str) -> list[dict]:
    return [json.loads(c.args[1]) for c in redis.publish.await_args_list if c.args[0] == channel]


@pytest.mark.asyncio
async def test_approve_publishes_hitl_approved() -> None:
    gw = HITLGateway()
    redis = AsyncMock()
    gw._redis = redis
    rid = str(gw.request_approval(goal_id="g1", action="deploy", tenant_ctx=CTX))
    assert gw.approve(rid, approver="ada", tenant_ctx=CTX)
    await asyncio.sleep(0)  # let the scheduled publish run
    await asyncio.sleep(0)
    (ev,) = _channel_payloads(redis, "hitl.approved")
    assert ev["tenant_id"] == CTX.tenant_id and ev["goal_id"] == "g1"
    assert ev["request_id"] == rid and ev["approver"] == "ada"
    assert ev["tenant_plan"] == "starter"


@pytest.mark.asyncio
async def test_reject_publishes_hitl_rejected() -> None:
    gw = HITLGateway()
    redis = AsyncMock()
    gw._redis = redis
    gw._db_update_resolution = AsyncMock(return_value=True)  # type: ignore[method-assign]
    rid = str(gw.request_approval(goal_id="g2", action="delete prod", tenant_ctx=CTX))
    assert await gw.reject(rid, approver="bob", note="no", tenant_ctx=CTX)
    (ev,) = _channel_payloads(redis, "hitl.rejected")
    assert ev["goal_id"] == "g2" and ev["note"] == "no"


@pytest.mark.asyncio
async def test_published_payload_drives_the_hitl_consumer() -> None:
    gw = HITLGateway()
    redis = AsyncMock()
    gw._redis = redis
    gw._db_update_resolution = AsyncMock(return_value=True)  # type: ignore[method-assign]
    rid = str(gw.request_approval(goal_id="g3", action="x", tenant_ctx=CTX))
    await gw.reject(rid, approver="bob", tenant_ctx=CTX)
    (raw,) = [c.args[1] for c in redis.publish.await_args_list if c.args[0] == "hitl.rejected"]

    store = SimpleNamespace(
        find_by_type_async=AsyncMock(return_value=[{"spec": SimpleNamespace(hitl_queue_id="")}])
    )
    dispatcher = SimpleNamespace(dispatch=AsyncMock())
    consumer = HITLTriggerConsumer(trigger_store=store, dispatcher=dispatcher)
    await consumer._handle({"type": "message", "channel": "hitl.rejected", "data": raw})
    store.find_by_type_async.assert_awaited_once_with("hitl_rejected", tenant_id=CTX.tenant_id)
    dispatcher.dispatch.assert_awaited_once()
