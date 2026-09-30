"""TRG-23: HITL queue ids are derived — ``agent:<agent_id>`` and ``risk:<tier>``.

HITL events always carried ``hitl_queue_id=""``, so a hitl_approved / rejected
trigger filtered by queue never fired. There is no separate queue concept: a
request belongs to its agent's queue and its risk tier's queue, both published on
the event, and the trigger filter matches either. Any other filter format is
rejected at trigger create / update (422) instead of silently never firing.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.governance import hitl_queues
from app.governance.hitl import HITLGateway
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.consumers.hitl import HITLTriggerConsumer
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.validation import validate_spec

CTX = TenantContext(tenant_id="t-q", plan=PlanTier.STARTER, api_key_id="k")


# ── derivation / validation ───────────────────────────────────────────────


def test_queue_ids_are_agent_and_risk_tier() -> None:
    assert hitl_queues.queue_ids("agent-1", "High") == ["agent:agent-1", "risk:high"]
    assert hitl_queues.queue_ids(None, "write_high") == ["risk:write_high"]
    assert hitl_queues.queue_ids("", "") == []


@pytest.mark.parametrize(
    "value", ["agent:agent-1", "agent:3f2a9c", "risk:high", "risk:critical", "risk:destructive"]
)
def test_valid_queue_filters(value: str) -> None:
    assert hitl_queues.queue_id_error(value) is None


@pytest.mark.parametrize(
    "value",
    ["queue-A", "agent:", "risk:", "risk:bogus", "agent:has space", "team:ops", "AGENT:x"],
)
def test_invalid_queue_filters(value: str) -> None:
    assert hitl_queues.queue_id_error(value)


def test_matches_either_published_id() -> None:
    event = {"hitl_queue_ids": ["agent:a1", "risk:high"], "hitl_queue_id": "agent:a1"}
    assert hitl_queues.matches("", event)
    assert hitl_queues.matches("agent:a1", event)
    assert hitl_queues.matches("risk:high", event)
    assert not hitl_queues.matches("agent:a2", event)
    assert not hitl_queues.matches("risk:low", event)


@pytest.mark.parametrize("trigger_type", [TriggerType.HITL_APPROVED, TriggerType.HITL_REJECTED])
def test_trigger_create_rejects_a_non_derived_queue_id(trigger_type: TriggerType) -> None:
    with pytest.raises(ValueError, match="hitl_queue_id"):
        validate_spec(TriggerSpec(trigger_type=trigger_type, hitl_queue_id="queue-A"))
    validate_spec(TriggerSpec(trigger_type=trigger_type, hitl_queue_id="agent:a1"))
    validate_spec(TriggerSpec(trigger_type=trigger_type, hitl_queue_id="risk:high"))
    validate_spec(TriggerSpec(trigger_type=trigger_type))


# ── publisher → consumer ──────────────────────────────────────────────────


def _payloads(redis: AsyncMock, channel: str) -> list[dict]:
    return [json.loads(c.args[1]) for c in redis.publish.await_args_list if c.args[0] == channel]


async def _approve(agent_id: str | None) -> dict:
    gw = HITLGateway()
    redis = AsyncMock()
    gw._redis = redis
    gw._goal_agent_id = AsyncMock(return_value=agent_id)  # type: ignore[method-assign]
    rid = str(
        gw.request_approval(goal_id="g1", action="deploy", risk_level="High", tenant_ctx=CTX)
    )
    assert gw.approve(rid, approver="ada", tenant_ctx=CTX)
    for _ in range(3):
        await asyncio.sleep(0)
    (ev,) = _payloads(redis, "hitl.approved")
    return ev


@pytest.mark.asyncio
async def test_hitl_event_carries_both_derived_queue_ids() -> None:
    ev = await _approve("agent-7")
    assert ev["hitl_queue_ids"] == ["agent:agent-7", "risk:high"]
    assert ev["hitl_queue_id"] == "agent:agent-7"


@pytest.mark.asyncio
async def test_hitl_event_without_an_agent_still_carries_the_risk_queue() -> None:
    ev = await _approve(None)
    assert ev["hitl_queue_ids"] == ["risk:high"]
    assert ev["hitl_queue_id"] == "risk:high"


@pytest.mark.asyncio
async def test_goal_agent_lookup_failure_does_not_block_the_event() -> None:
    gw = HITLGateway()
    gw._db_session_factory = lambda: (_ for _ in ()).throw(RuntimeError("db down"))
    assert await gw._goal_agent_id("g1", CTX.tenant_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("watch", "fires"),
    [("agent:agent-7", True), ("risk:high", True), ("", True), ("agent:other", False),
     ("risk:low", False)],
)
async def test_trigger_filtered_by_queue_fires_on_a_decision_in_that_queue(
    watch: str, fires: bool
) -> None:
    ev = await _approve("agent-7")
    store = SimpleNamespace(
        find_by_type_async=AsyncMock(
            return_value=[{"spec": SimpleNamespace(hitl_queue_id=watch)}]
        )
    )
    dispatcher = SimpleNamespace(dispatch=AsyncMock(), tenant_service=None)
    consumer = HITLTriggerConsumer(trigger_store=store, dispatcher=dispatcher)
    await consumer._handle(
        {"type": "message", "channel": "hitl.approved", "data": json.dumps(ev)}
    )
    assert dispatcher.dispatch.await_count == (1 if fires else 0)
