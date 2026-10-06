"""B7 live open item 2: HITL queue filters now apply to workflow approval gates.

Goal approvals publish their derived queues (``agent:<id>``, ``risk:<tier>``) on
``hitl.approved`` / ``hitl.rejected``; workflow approval decisions published
``hitl_queue_ids: []``, so a HITL trigger with a ``hitl_queue_id`` filter could
never fire for a workflow gate (operators had to fall back to ``condition_cel``
on ``payload.workflow_id``). A workflow gate now belongs to
``workflow:<workflow_id>`` and ``risk:<priority>``, and the same ``matches``
filter decides for both kinds of approval.
"""

from __future__ import annotations

import json
from typing import Any

import fakeredis
import pytest

from app.core.config import get_settings
from app.governance import hitl_queues
from app.triggers.consumers.hitl import HITLTriggerConsumer
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.validation import validate_spec
from app.workflow.hitl_extension import HITLWorkflowGateway

S = get_settings()


# ── derivation / validation ──────────────────────────────────────────────────


def test_workflow_queue_ids_are_workflow_and_priority_tier() -> None:
    assert hitl_queues.workflow_queue_ids("wf-1", "High") == ["workflow:wf-1", "risk:high"]
    assert hitl_queues.workflow_queue_ids("wf-1", "") == ["workflow:wf-1"]
    assert hitl_queues.workflow_queue_ids("", "critical") == ["risk:critical"]
    # Not a tier: no risk queue rather than an unmatchable one.
    assert hitl_queues.workflow_queue_ids("wf-1", "urgent!") == ["workflow:wf-1"]
    assert hitl_queues.workflow_queue_ids(None, None) == []


@pytest.mark.parametrize("value", ["workflow:wf-1", "workflow:3f2a9c0d", "workflow:a.b_c-d"])
def test_workflow_queue_filter_is_valid(value: str) -> None:
    assert hitl_queues.queue_id_error(value) is None
    validate_spec(TriggerSpec(trigger_type=TriggerType.HITL_APPROVED, hitl_queue_id=value))


@pytest.mark.parametrize("value", ["workflow:", "workflow:has space", "WORKFLOW:x", "wf:x"])
def test_malformed_workflow_queue_filter_is_refused(value: str) -> None:
    assert hitl_queues.queue_id_error(value)
    with pytest.raises(ValueError, match="hitl_queue_id"):
        validate_spec(TriggerSpec(trigger_type=TriggerType.HITL_REJECTED, hitl_queue_id=value))


# ── publisher → consumer ─────────────────────────────────────────────────────


async def _decided_event(action: str = "approve", priority: str = "high") -> tuple[str, dict]:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    gw = HITLWorkflowGateway(event_redis=redis)
    rid = await gw.create_workflow_approval(
        run_id="run-1", step_id="gate", tenant_id="t-wfq", workflow_id="wf-7",
        strategy="specific_user", specific_user="alice", priority=priority,  # type: ignore[arg-type]
    )
    await gw.decide(rid, action, "alice", note="ok", tenant_id="t-wfq")
    [(_, fields)] = await redis.xrange(S.trigger_bus_stream_hitl)
    return fields["channel"], json.loads(fields["data"])


async def test_workflow_decision_event_carries_its_derived_queues() -> None:
    channel, data = await _decided_event("approve", "high")
    assert channel == "hitl.approved"
    assert data["hitl_queue_ids"] == ["workflow:wf-7", "risk:high"]
    assert data["hitl_queue_id"] == "workflow:wf-7"
    assert data["risk_level"] == "high"


async def test_rejection_carries_them_too() -> None:
    channel, data = await _decided_event("reject", "medium")
    assert channel == "hitl.rejected"
    assert data["hitl_queue_ids"] == ["workflow:wf-7", "risk:medium"]


class _Disp:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def resolve_tenant_plan(self, tenant_id: str) -> str:
        return "free"

    async def dispatch(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(args)


@pytest.mark.parametrize(
    ("watch", "fires"),
    [
        ("", True),  # unfiltered: as before
        ("workflow:wf-7", True),
        ("risk:high", True),
        ("workflow:wf-other", False),
        ("risk:low", False),
        ("agent:agent-7", False),  # a workflow gate has no agent queue
    ],
)
async def test_queue_filtered_trigger_applies_to_a_workflow_gate(watch: str, fires: bool) -> None:
    channel, data = await _decided_event("approve", "high")
    spec = TriggerSpec(
        trigger_type=TriggerType.HITL_APPROVED, goal_template="after", hitl_queue_id=watch
    )

    class _Store:
        async def find_by_type_async(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
            return [{"spec": spec}]

    disp = _Disp()
    await HITLTriggerConsumer(trigger_store=_Store(), dispatcher=disp)._handle(
        {"channel": channel, "data": json.dumps(data)}
    )
    assert len(disp.calls) == (1 if fires else 0)


async def test_goal_and_workflow_approvals_share_one_filter_rule() -> None:
    """The same risk:<tier> filter selects a goal gate and a workflow gate alike."""
    _, wf_event = await _decided_event("approve", "high")
    goal_event = {"hitl_queue_ids": hitl_queues.queue_ids("agent-1", "high")}
    for event in (wf_event, goal_event):
        assert hitl_queues.matches("risk:high", event)
        assert not hitl_queues.matches("risk:critical", event)
