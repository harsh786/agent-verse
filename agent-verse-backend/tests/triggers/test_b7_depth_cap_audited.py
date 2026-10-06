"""B7-L3: a firing suppressed by the chain depth cap leaves an audit row.

The dispatcher audits ``chain_depth_exceeded`` (B7-1), but all three platform-
event consumers returned early — with a log line only — when the source goal
was already at MAX_CHAIN_DEPTH, so the dispatcher never saw the firing and the
trigger's ``GET /triggers/{id}/events`` showed the chain simply stopping, with no
row saying why. The consumers now hand the firing to the dispatcher, which
refuses and audits it per matching trigger.
"""

from __future__ import annotations

import pytest

from app.triggers.consumers.chain import ChainTriggerConsumer, build_chain_event
from app.triggers.consumers.hitl import HITLTriggerConsumer
from app.triggers.consumers.memory import MemoryTriggerConsumer
from app.triggers.lineage import MAX_CHAIN_DEPTH, GoalLineage
from app.triggers.models import TriggerSpec, TriggerType
from tests.triggers.test_b7_trigger_lineage import (
    CTX,
    _Dispatcher,
    _hitl_msg,
    _memory_msg,
    _store_with,
)


@pytest.mark.parametrize(
    ("ttype", "channel"),
    [
        (TriggerType.GOAL_COMPLETED, "goal.completed"),
        (TriggerType.GOAL_FAILED, "goal.failed"),
        (TriggerType.GOAL_SCORE_BELOW, "goal.score_below"),
    ],
)
async def test_goal_event_at_the_depth_cap_is_audited(ttype: TriggerType, channel: str) -> None:
    spec = TriggerSpec(trigger_type=ttype, score_threshold=0.9, allow_self_trigger=True)
    store, trigger_id = _store_with(spec)
    disp = _Dispatcher()
    consumer = ChainTriggerConsumer(trigger_store=store, dispatcher=disp)
    deep = build_chain_event(
        channel=channel, tenant_id=CTX.tenant_id, goal_id="g-deep",
        trigger_chain_depth=MAX_CHAIN_DEPTH, source_trigger_id=trigger_id, score=0.1,
    )
    await consumer._handle({"channel": channel, "data": deep})
    assert disp.goals.calls == []
    assert [(e.trigger_id, e.skip_reason) for e in disp.audit] == [
        (trigger_id, "chain_depth_exceeded")
    ]


async def test_goal_event_one_below_the_cap_still_fires() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED, allow_self_trigger=True)
    store, trigger_id = _store_with(spec)
    disp = _Dispatcher()
    consumer = ChainTriggerConsumer(trigger_store=store, dispatcher=disp)
    event = build_chain_event(
        channel="goal.completed", tenant_id=CTX.tenant_id, goal_id="g-9",
        trigger_chain_depth=MAX_CHAIN_DEPTH - 1, source_trigger_id=trigger_id,
    )
    await consumer._handle({"channel": "goal.completed", "data": event})
    assert [c["trigger_chain_depth"] for c in disp.goals.calls] == [MAX_CHAIN_DEPTH]
    assert disp.audit and not any(e.skip_reason for e in disp.audit)


async def test_memory_event_at_the_depth_cap_is_audited() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.MEMORY_CREATED, allow_self_trigger=True)
    store, trigger_id = _store_with(spec)
    disp = _Dispatcher({"g-deep": GoalLineage(depth=MAX_CHAIN_DEPTH, source_trigger_id="x")})
    consumer = MemoryTriggerConsumer(trigger_store=store, dispatcher=disp)
    await consumer._handle(_memory_msg("m-deep", "g-deep"))
    assert disp.goals.calls == []
    assert [e.skip_reason for e in disp.audit] == ["chain_depth_exceeded"]


@pytest.mark.parametrize("channel", ["hitl.approved", "hitl.rejected"])
async def test_hitl_event_at_the_depth_cap_is_audited(channel: str) -> None:
    ttype = TriggerType.HITL_APPROVED if "approved" in channel else TriggerType.HITL_REJECTED
    store, _ = _store_with(TriggerSpec(trigger_type=ttype))
    disp = _Dispatcher({"g-deep": GoalLineage(depth=MAX_CHAIN_DEPTH, source_trigger_id="x")})
    consumer = HITLTriggerConsumer(trigger_store=store, dispatcher=disp)
    await consumer._handle(_hitl_msg(channel, "req-deep", "g-deep"))
    assert disp.goals.calls == []
    assert [e.skip_reason for e in disp.audit] == ["chain_depth_exceeded"]
