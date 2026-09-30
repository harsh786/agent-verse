"""TRG-38: a state-machine transition is reported (and fires triggers) only
when it was durably saved.

``transition_async`` swallowed DB write errors — and skipped a missing row —
yet returned ``transitioned=True``, so the API published a
``state_machine.transition`` trigger event for a transition that never
persisted. Only the API route published at all. Now a failed write raises
(API → 503), a missing row raises (→ 404), and the registry itself publishes
after the commit, so every caller fires STATE_TRANSITION triggers.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.tenancy.context import PlanTier, TenantContext
from app.triggers.state_machine import (
    STATE_TRANSITION_CHANNEL,
    StateDefinition,
    StateMachine,
    StateMachineDefinition,
    StateMachineInstance,
    StateMachineInstanceNotFoundError,
    StateMachineStoreUnavailableError,
    TransitionDefinition,
)

TENANT = "t-sm-integrity"
CTX = TenantContext(tenant_id=TENANT, plan=PlanTier.FREE, api_key_id="k")


def _definition() -> StateMachineDefinition:
    return StateMachineDefinition(
        machine_id="m1", tenant_id=TENANT, name="order",
        states=[StateDefinition(name="new", is_initial=True), StateDefinition(name="paid")],
        transitions=[TransitionDefinition(from_state="new", to_state="paid", event="pay")],
    )


class _Redis:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, channel: str, data: str) -> int:
        self.published.append((channel, json.loads(data)))
        return 1


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row


class _Session:
    """Answers the GUC call, then the row SELECT; optionally fails on commit."""

    def __init__(self, row: Any, *, fail_commit: bool = False) -> None:
        self.row = row
        self.fail_commit = fail_commit
        self.calls = 0

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def begin(self) -> _Session:
        return self

    async def execute(self, *_: Any, **__: Any) -> _Result:
        self.calls += 1
        return _Result(self.row)

    def add(self, obj: Any) -> None:
        self.row = obj

    async def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("db write failed")


def _registry(session: _Session, redis: _Redis) -> StateMachine:
    sm = StateMachine()
    sm._db_factory = lambda: session
    sm.set_event_redis(redis)
    instance = StateMachineInstance(
        instance_id="i1", machine_id="m1", tenant_id=TENANT, entity_id="e1", current_state="new"
    )

    async def _get_instance(*_: Any, **__: Any) -> StateMachineInstance:
        return instance

    async def _get_definition(*_: Any, **__: Any) -> StateMachineDefinition:
        return _definition()

    sm.get_instance_async = _get_instance  # type: ignore[method-assign]
    sm.get_definition_async = _get_definition  # type: ignore[method-assign]
    return sm


def _row() -> Any:
    return SimpleNamespace(current_state="new", history=[], status="running")


async def test_failed_write_raises_and_publishes_nothing() -> None:
    redis = _Redis()
    sm = _registry(_Session(_row(), fail_commit=True), redis)
    with pytest.raises(StateMachineStoreUnavailableError):
        await sm.transition_async("m1", "e1", "pay", TENANT)
    assert redis.published == []


async def test_missing_row_raises_not_found() -> None:
    redis = _Redis()
    sm = _registry(_Session(None), redis)
    with pytest.raises(StateMachineInstanceNotFoundError):
        await sm.transition_async("m1", "e1", "pay", TENANT)
    assert redis.published == []


async def test_store_level_transition_publishes_after_commit() -> None:
    redis = _Redis()
    row = _row()
    sm = _registry(_Session(row), redis)
    result = await sm.transition_async("m1", "e1", "pay", TENANT)
    assert result["transitioned"] is True and result["trigger_event_published"] is True
    assert row.current_state == "paid"
    (channel, event), = redis.published
    assert channel == STATE_TRANSITION_CHANNEL
    assert event["state"] == "paid" and event["state_machine_id"] == "m1"


def test_api_maps_a_failed_write_to_503_and_publishes_nothing() -> None:
    from app.api.state_machines import router

    redis = _Redis()
    app = FastAPI()

    @app.middleware("http")
    async def _t(request: Request, call_next: Any) -> Any:
        request.state.tenant = CTX
        return await call_next(request)

    app.include_router(router)
    app.state.state_machine_registry = _registry(_Session(_row(), fail_commit=True), redis)
    resp = TestClient(app).post("/state-machines/m1/instances/e1/transition", json={"event": "pay"})
    assert resp.status_code == 503
    assert redis.published == []


async def test_create_instance_db_failure_raises() -> None:
    class _Broken(_Session):
        async def execute(self, *_: Any, **__: Any) -> _Result:
            raise RuntimeError("db down")

    sm = StateMachine()
    sm._db_factory = lambda: _Broken(None)

    async def _get_definition(*_: Any, **__: Any) -> StateMachineDefinition:
        return _definition()

    sm.get_definition_async = _get_definition  # type: ignore[method-assign]
    with pytest.raises(StateMachineStoreUnavailableError):
        await sm.create_instance_async("m1", "e1", TENANT)
