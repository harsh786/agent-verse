"""TRG-18: every trigger-event publisher appends to its family stream.

Pub/sub alone is at-most-once; each publisher now goes through
``app.triggers.bus.publish_trigger_event`` so the event is durably in a Redis
Stream for the consumer groups (and still dual-published to the legacy channel).
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest

from app.agent.state import GoalStatus
from app.core.config import get_settings
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-bus", plan=PlanTier.STARTER, api_key_id="k")
S = get_settings()


def _entries(redis: Any, stream: str) -> list[tuple[str, dict[str, Any]]]:
    return [(f["channel"], json.loads(f["data"])) for _, f in redis.xrange(stream)]


async def _aentries(redis: Any, stream: str) -> list[tuple[str, dict[str, Any]]]:
    return [(f["channel"], json.loads(f["data"])) for _, f in await redis.xrange(stream)]


@pytest.fixture
def aredis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis(decode_responses=True)


async def test_goal_service_chain_events_go_to_the_goal_stream(aredis: Any) -> None:
    from app.services.goal_service import GoalRecord, GoalService

    svc = GoalService()
    svc._redis = aredis
    svc._goals["g1"] = GoalRecord(
        goal_id="g1",
        goal_text="g",
        status=GoalStatus.EXECUTING,
        tenant_id=CTX.tenant_id,
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
        agent_id="agent-1",
        execution_context={},
    )
    await svc._dispatch_event("g1", {"type": "goal_complete"}, tenant_ctx=CTX)
    await svc._publish_chain_event(svc._goals["g1"], "goal.score_below", CTX, score=0.2)

    entries = await _aentries(aredis, S.trigger_bus_stream_goal)
    assert [c for c, _ in entries] == ["goal.completed", "goal.score_below"]
    assert entries[0][1]["completion_event_id"] == "g1:goal.completed"
    assert entries[1][1]["score"] == 0.2


async def test_hitl_decisions_go_to_the_hitl_stream(aredis: Any) -> None:
    from app.governance.hitl import HITLGateway

    gw = HITLGateway()
    gw._redis = aredis
    gw._db_update_resolution = AsyncMock(return_value=True)  # type: ignore[method-assign]
    rid = str(gw.request_approval(goal_id="g2", action="delete prod", tenant_ctx=CTX))
    assert await gw.reject(rid, approver="bob", note="no", tenant_ctx=CTX)

    [(channel, data)] = await _aentries(aredis, S.trigger_bus_stream_hitl)
    assert channel == "hitl.rejected"
    assert data["request_id"] == rid and data["tenant_id"] == CTX.tenant_id


async def test_memory_created_goes_to_the_memory_stream(aredis: Any) -> None:
    from app.memory.long_term import LongTermMemory, LongTermMemoryStore

    ltm = LongTermMemoryStore()
    ltm.set_event_redis(aredis)
    mem = LongTermMemory(content="x", source_goal_id="g", memory_type="domain_fact")
    with patch("app.memory.long_term._GUARDRAILS_AVAILABLE", True):
        await ltm.store_async(memory=mem, tenant_ctx=CTX)

    [(channel, data)] = await _aentries(aredis, S.trigger_bus_stream_memory)
    assert channel == "memory.created"
    assert data["memory_id"] == mem.memory_id


async def test_state_machine_transition_goes_to_the_event_stream(aredis: Any) -> None:
    from app.triggers.state_machine import STATE_TRANSITION_CHANNEL, StateMachine

    sm = StateMachine()
    sm.set_event_redis(aredis)
    ok = await sm._publish_transition(
        CTX.tenant_id, "m1", "e1", {"from_state": "a", "to_state": "b", "event": "go"}
    )

    assert ok is True
    [(channel, data)] = await _aentries(aredis, S.trigger_bus_stream_event)
    assert channel == STATE_TRANSITION_CHANNEL
    assert data["state"] == "b" and data["event_id"]


async def test_custom_and_conversational_events_go_to_the_event_stream(aredis: Any) -> None:
    from app.triggers.consumers.conversational import publish_conversational_event
    from app.triggers.consumers.event import publish_trigger_event

    await publish_trigger_event(
        aredis, event_channel="orders", tenant_id=CTX.tenant_id, payload={"n": 1}
    )
    await publish_conversational_event(aredis, tenant_id=CTX.tenant_id, event={"text": "hi"})

    entries = await _aentries(aredis, S.trigger_bus_stream_event)
    assert [c for c, _ in entries] == ["trigger:event:orders", "trigger:event:conversational"]
    assert all(d["tenant_id"] == CTX.tenant_id and d["event_id"] for _, d in entries)


def test_worker_score_below_goes_to_the_goal_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks

    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: redis)
    state = SimpleNamespace(context={"eval_scorecard": SimpleNamespace(average_score=lambda: 0.3)})

    assert tasks._publish_worker_score_below(
        state,
        tenant_id=CTX.tenant_id,
        goal_id="gw",
        agent_id="a",
        plan="free",
        trigger_chain_depth=0,
        source_trigger_id="",
    )
    [(channel, data)] = _entries(redis, S.trigger_bus_stream_goal)
    assert channel == "goal.score_below" and data["score"] == 0.3


def test_run_goal_lifecycle_publish_uses_the_bus_helper() -> None:
    """run_goal's chain publish (an inner callback that needs real Postgres/Redis
    to drive) goes through the shared helper rather than a raw PUBLISH."""
    from app.scaling import tasks

    source = inspect.getsource(tasks)
    start = source.index("_chain_channel = _chain_channel_for_worker_event(")
    block = source[start : source.index("_chain_published.add(_chain_channel)", start)]
    assert "publish_trigger_event" in block
    assert "_rc.publish(" not in block
