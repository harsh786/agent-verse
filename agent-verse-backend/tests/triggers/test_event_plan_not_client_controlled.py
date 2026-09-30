"""TRG-05: an event body cannot raise the tenant's plan tier.

``publish_trigger_event`` merged the client body before stamping the tenant, so
``{"tenant_plan": "enterprise"}`` survived and every event-bus consumer used it
as the dispatch plan (enterprise rate cap, bulkhead, payload limit, queue) for a
free tenant. Publishers now strip reserved keys and consumers resolve the plan
from the tenant record through the dispatcher.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tenancy.context import PlanTier
from app.triggers.consumers.chain import ChainTriggerConsumer
from app.triggers.consumers.condition import ConditionTriggerConsumer
from app.triggers.consumers.conversational import ConversationalTriggerConsumer
from app.triggers.consumers.event import EventTriggerConsumer, publish_trigger_event
from app.triggers.consumers.hitl import HITLTriggerConsumer
from app.triggers.consumers.memory import MemoryTriggerConsumer
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.models import TriggerSpec, TriggerType

SPOOF = {"tenant_plan": "enterprise", "tenant_id": "victim", "event_channel": "other"}


class _Redis:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.streamed: list[tuple[str, dict[str, str]]] = []

    async def publish(self, channel: str, data: str) -> int:
        self.published.append((channel, json.loads(data)))
        return 1

    async def xadd(self, stream: str, fields: dict[str, str], **_: Any) -> str:
        # TRG-18: the durable trigger-bus copy (dual-published to the channel).
        self.streamed.append((stream, fields))
        return f"{len(self.streamed)}-0"


async def test_publish_trigger_event_strips_reserved_keys_and_stamps_server_values() -> None:
    redis = _Redis()
    await publish_trigger_event(
        redis, event_channel="deploys", tenant_id="t1", payload={**SPOOF, "x": 1}
    )
    (_, body), = redis.published
    assert body["tenant_id"] == "t1"
    assert body["event_channel"] == "deploys"
    assert "tenant_plan" not in body
    assert body["x"] == 1


def test_emit_endpoint_does_not_forward_client_plan() -> None:
    from app.api.triggers import router

    app = FastAPI()
    app.include_router(router)
    redis = _Redis()
    app.state.trigger_event_redis = redis

    @app.middleware("http")
    async def inject_tenant(req: Any, call_next: Any) -> Any:
        from app.tenancy.context import TenantContext

        req.state.tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")
        return await call_next(req)

    resp = TestClient(app).post("/triggers/events/deploys", json=SPOOF)
    assert resp.status_code == 202
    (_, body), = redis.published
    assert body["tenant_id"] == "t1"
    assert body.get("tenant_plan") in (None, "free")


# ── consumers take the plan from the tenant record, not the event ────────────


class _Dispatcher:
    def __init__(self, plan: PlanTier = PlanTier.FREE) -> None:
        self.plan = plan
        self.dispatch = AsyncMock()
        self.resolved: list[str] = []

    async def resolve_tenant_plan(self, tenant_id: str) -> PlanTier:
        self.resolved.append(tenant_id)
        return self.plan


class _Store:
    def __init__(self, recs_by_type: dict[str, list[Any]]) -> None:
        self._recs = recs_by_type

    async def find_by_type_async(self, trigger_type: str, *, tenant_id: str, **_: Any) -> list:
        return self._recs.get(trigger_type, [])


def _plan_of(dispatcher: _Dispatcher) -> Any:
    dispatcher.dispatch.assert_awaited()
    return dispatcher.dispatch.await_args.args[2].plan


def _msg(channel: str, data: dict[str, Any], *, pattern: bool = False) -> dict[str, Any]:
    return {"type": "pmessage" if pattern else "message", "channel": channel,
            "data": json.dumps(data)}


async def test_event_consumer_ignores_event_plan() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.EVENT, event_channel="deploys")
    d = _Dispatcher()
    c = EventTriggerConsumer(trigger_store=_Store({"event": [{"spec": spec}]}), dispatcher=d)
    await c._handle(_msg("trigger:event:deploys", {"tenant_id": "t1", "tenant_plan": "enterprise"},
                         pattern=True))
    assert _plan_of(d) == PlanTier.FREE
    assert d.resolved == ["t1"]


async def test_condition_consumer_ignores_event_plan() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.CONDITION, condition_expression="x == 1")
    spec.trigger_id = "c1"  # type: ignore[attr-defined]
    d = _Dispatcher()
    c = ConditionTriggerConsumer(trigger_store=_Store({"condition": [{"spec": spec}]}),
                                 dispatcher=d)
    await c._handle(_msg("trigger:event:metrics",
                         {"tenant_id": "t1", "tenant_plan": "enterprise", "x": 1}, pattern=True))
    assert _plan_of(d) == PlanTier.FREE


async def test_conversational_consumer_ignores_event_plan() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.CHAT_KEYWORD, keyword_pattern="deploy")
    d = _Dispatcher(PlanTier.STARTER)
    c = ConversationalTriggerConsumer(
        trigger_store=_Store({"chat_keyword": [{"spec": spec}]}), dispatcher=d
    )
    await c._handle(_msg("trigger:event:conversational", {
        "tenant_id": "t1", "tenant_plan": "enterprise", "conv": True,
        "channel_type": "slack", "text": "please deploy",
    }, pattern=True))
    assert _plan_of(d) == PlanTier.STARTER


@pytest.mark.parametrize(
    ("consumer_cls", "channel", "ttype"),
    [
        (HITLTriggerConsumer, "hitl.approved", "hitl_approved"),
        (MemoryTriggerConsumer, "memory.created", "memory_created"),
        (ChainTriggerConsumer, "goal.completed", "goal_completed"),
    ],
)
async def test_lifecycle_consumers_ignore_event_plan(consumer_cls, channel, ttype) -> None:
    spec = TriggerSpec(trigger_type=TriggerType(ttype))
    d = _Dispatcher()
    c = consumer_cls(trigger_store=_Store({ttype: [{"spec": spec}]}), dispatcher=d)
    await c._handle(_msg(channel, {"tenant_id": "t1", "goal_id": "g",
                                   "tenant_plan": "enterprise"}))
    assert _plan_of(d) == PlanTier.FREE


async def test_consumer_with_a_resolverless_dispatcher_falls_back_to_free() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.EVENT, event_channel="deploys")
    d = SimpleNamespace(dispatch=AsyncMock())
    c = EventTriggerConsumer(trigger_store=_Store({"event": [{"spec": spec}]}), dispatcher=d)
    await c._handle(_msg("trigger:event:deploys", {"tenant_id": "t1", "tenant_plan": "enterprise"},
                         pattern=True))
    assert d.dispatch.await_args.args[2].plan == PlanTier.FREE


# ── the dispatcher reads the plan from the tenant record ─────────────────────


async def test_dispatcher_resolves_plan_from_tenants_table() -> None:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalar = MagicMock(return_value="professional")
    session.execute = AsyncMock(return_value=result)
    d = TriggerDispatcher(db_session_factory=lambda: session)
    assert await d.resolve_tenant_plan("t1") == PlanTier.PROFESSIONAL
    assert await TriggerDispatcher().resolve_tenant_plan("t1") == PlanTier.FREE
