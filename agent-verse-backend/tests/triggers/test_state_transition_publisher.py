"""Regression: STATE_TRANSITION triggers could never fire.

ConditionTriggerConsumer evaluates them on ``trigger:event:*``, but a state
machine transition published nothing; and the evaluator matched ANY event that
carried a ``state`` field, regardless of the trigger's ``state_machine_id``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.state_machines import STATE_TRANSITION_CHANNEL, router
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.consumers.condition import ConditionTriggerConsumer
from app.triggers.state_machine import StateMachine

CTX = TenantContext(tenant_id="t-sm", plan=PlanTier.FREE, api_key_id="k")


def _client(redis: Any) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _t(request: Request, call_next: Any) -> Any:
        request.state.tenant = CTX
        return await call_next(request)

    app.include_router(router)
    app.state.state_machine_registry = StateMachine()
    app.state._redis = redis
    return TestClient(app, raise_server_exceptions=False)


def _machine(client: TestClient) -> str:
    r = client.post(
        "/state-machines",
        json={
            "name": "order",
            "states": [{"name": "new", "is_initial": True}, {"name": "paid"}],
            "transitions": [{"from_state": "new", "to_state": "paid", "event": "pay"}],
        },
    )
    assert r.status_code == 201, r.text
    return str(r.json()["machine_id"])


def test_transition_publishes_on_the_trigger_event_bus() -> None:
    redis = AsyncMock()
    client = _client(redis)
    mid = _machine(client)
    r = client.post(f"/state-machines/{mid}/instances/o-1/transition", json={"event": "pay"})
    assert r.status_code == 200, r.text
    assert r.json()["trigger_event_published"] is True
    (call,) = [c for c in redis.publish.await_args_list if c.args[0] == STATE_TRANSITION_CHANNEL]
    ev = json.loads(call.args[1])
    assert ev.pop("event_id")  # one id per committed transition (TRG-19)
    assert ev == {
        "tenant_id": CTX.tenant_id, "event_type": "state_machine.transition",
        "state_machine_id": mid, "entity_id": "o-1", "state": "paid",
        "from_state": "new", "event": "pay",
    }


def _consumer(spec: Any) -> tuple[ConditionTriggerConsumer, AsyncMock]:
    dispatcher = SimpleNamespace(dispatch=AsyncMock())
    store = SimpleNamespace(
        find_by_type_async=AsyncMock(
            side_effect=lambda t, tenant_id, strict=False: [{"spec": spec}] if t == "state_transition" else []
        )
    )
    return ConditionTriggerConsumer(trigger_store=store, dispatcher=dispatcher), dispatcher.dispatch


async def test_published_transition_fires_a_matching_trigger_only() -> None:
    spec = SimpleNamespace(
        trigger_id="tr-1", trigger_type="state_transition", state_machine_id="m-1",
        from_state="new", to_state="paid",
    )
    consumer, dispatch = _consumer(spec)
    base = {"tenant_id": CTX.tenant_id, "entity_id": "o-1", "state": "paid", "from_state": "new"}
    await consumer._dispatch_matching({**base, "state_machine_id": "other-machine"})
    dispatch.assert_not_awaited()
    await consumer._dispatch_matching({**base, "state_machine_id": "m-1"})
    dispatch.assert_awaited_once()
