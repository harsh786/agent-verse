"""B7-3: a workflow approval decision fires hitl_approved / hitl_rejected triggers.

``HITLWorkflowGateway.decide`` (API decide, bulk decide, magic link) recorded the
decision and resumed the run, but published nothing on the HITL trigger stream:
only goal approvals (``HITLGateway``) did, so a HITL trigger never fired for an
approval gate in a workflow.
"""

from __future__ import annotations

import json
from typing import Any

import fakeredis
import pytest

from app.core.config import get_settings
from app.workflow.hitl_extension import HITLWorkflowGateway

S = get_settings()


async def _hitl_entries(redis: Any) -> list[tuple[str, dict[str, Any]]]:
    return [
        (f["channel"], json.loads(f["data"]))
        for _, f in await redis.xrange(S.trigger_bus_stream_hitl)
    ]


async def _approval(gw: HITLWorkflowGateway) -> str:
    return await gw.create_workflow_approval(
        run_id="run-1", step_id="gate", tenant_id="t-wf", workflow_id="wf-1",
        strategy="specific_user", specific_user="alice",
    )


@pytest.mark.parametrize(
    ("action", "channel"), [("approve", "hitl.approved"), ("reject", "hitl.rejected")]
)
async def test_decision_publishes_the_hitl_trigger_event(action: str, channel: str) -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    gw = HITLWorkflowGateway(event_redis=redis)
    rid = await _approval(gw)

    await gw.decide(rid, action, "alice", note="ok", tenant_id="t-wf")
    # An idempotent repeat of the same decision is not a second event.
    await gw.decide(rid, action, "alice", note="ok", tenant_id="t-wf")

    entries = await _hitl_entries(redis)
    assert [c for c, _ in entries] == [channel]
    data = entries[0][1]
    assert data["tenant_id"] == "t-wf"
    assert data["request_id"] == rid
    assert data["workflow_run_id"] == "run-1" and data["workflow_id"] == "wf-1"
    assert data["approver"] == "alice" and data["source"] == "workflow"
    assert data["goal_id"] == ""


async def test_a_custom_action_is_not_an_approval_event() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    gw = HITLWorkflowGateway(event_redis=redis)
    rid = await _approval(gw)
    await gw.decide(rid, "needs_more_info", "alice", tenant_id="t-wf")
    assert await _hitl_entries(redis) == []


async def test_set_event_redis_binds_the_publisher() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    gw = HITLWorkflowGateway()
    gw.set_event_redis(redis)
    rid = await _approval(gw)
    await gw.decide(rid, "approve", "alice", tenant_id="t-wf")
    assert len(await _hitl_entries(redis)) == 1


async def test_published_decision_drives_the_hitl_consumer_once() -> None:
    from app.triggers.consumers.hitl import HITLTriggerConsumer
    from app.triggers.models import TriggerSpec, TriggerType

    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    gw = HITLWorkflowGateway(event_redis=redis)
    rid = await _approval(gw)
    await gw.decide(rid, "approve", "alice", tenant_id="t-wf")
    [(channel, data)] = await _hitl_entries(redis)

    spec = TriggerSpec(trigger_type=TriggerType.HITL_APPROVED, goal_template="after approval")
    calls: list[tuple[Any, ...]] = []

    class _Store:
        async def find_by_type_async(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
            return [{"spec": spec}]

    class _Disp:
        async def resolve_tenant_plan(self, tenant_id: str) -> str:
            return "free"

        async def dispatch(self, *args: Any, **kwargs: Any) -> None:
            calls.append((args, kwargs))

    consumer = HITLTriggerConsumer(trigger_store=_Store(), dispatcher=_Disp())
    await consumer._handle({"channel": channel, "data": json.dumps(data)})
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] is spec and args[2].tenant_id == "t-wf"
    assert kwargs["completion_event_id"] == f"{rid}:hitl.approved"
