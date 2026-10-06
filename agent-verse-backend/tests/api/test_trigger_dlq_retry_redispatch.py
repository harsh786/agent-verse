"""Regression: POST /triggers/dlq/{id}/retry must actually re-fire the trigger.

The route called ``dispatcher.dispatch(spec, tenant_id=..., payload=...)`` — not
the real signature ``dispatch(trigger_spec, payload, tenant_ctx, ...)`` — so it
raised a TypeError that ``contextlib.suppress(Exception)`` swallowed. The route
answered ``202 {"status": "queued"}`` and nothing was ever re-dispatched. The
dispatcher here is autospecced from the real class so a signature mismatch
fails loudly, and a dispatch failure must be surfaced, not reported as queued.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import create_autospec, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.triggers import router
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.events import TriggerEvent
from app.triggers.models import TriggerSpec, TriggerType

TENANT = "tenant-dlq"


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _Session:
    def __init__(self, idempotency_key: str | None = None) -> None:
        self.updates: list[dict[str, Any]] = []
        self.statements: list[str] = []
        # None = a row dead-lettered before trigger_dlq.idempotency_key existed.
        self.idempotency_key = idempotency_key

    async def execute(self, stmt: Any, params: dict[str, Any]) -> _Result:
        if str(stmt).lstrip().upper().startswith("SELECT"):
            return _Result(("trg-1", {"marker": "retry-me"}, 0, self.idempotency_key))
        self.updates.append(params)
        self.statements.append(str(stmt))
        return _Result(None)

    def resolved(self) -> bool:
        return any("resolved_at = NOW()" in s for s in self.statements)

    def begin(self) -> contextlib.AbstractAsyncContextManager[None]:
        return contextlib.nullcontext()  # type: ignore[return-value]

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Store:
    async def get_async(self, schedule_id: str, tenant_ctx: Any) -> dict[str, Any] | None:
        spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK, goal_template="Handle it")
        spec.trigger_id = schedule_id  # type: ignore[attr-defined]
        return {"schedule_id": schedule_id, "spec": spec, "agent_id": "", "goal_template": ""}


def _client(dispatcher: Any, idempotency_key: str | None = None) -> tuple[TestClient, _Session]:
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = SimpleNamespace(tenant_id=TENANT, plan="free")
        return await call_next(request)

    app.include_router(router)
    session = _Session(idempotency_key)
    app.state.db_session_factory = lambda: session
    app.state.trigger_dispatcher = dispatcher
    app.state.schedule_store = _Store()
    return TestClient(app, raise_server_exceptions=False), session


@pytest.fixture(autouse=True)
def _no_rls() -> Any:
    with patch(
        "app.db.rls.sqlalchemy_rls_context",
        lambda session, tenant_id: contextlib.nullcontext(),
    ):
        yield


def _event(**kw: Any) -> TriggerEvent:
    from datetime import UTC, datetime

    base: dict[str, Any] = {
        "event_id": "e1",
        "tenant_id": TENANT,
        "trigger_id": "trg-1",
        "trigger_type": "webhook",
        "idempotency_key": "k",
        "fired_at": datetime.now(UTC),
        "payload": {},
        "goal_created": True,
        "goal_id": "g-retried",
        "skip_reason": None,
    }
    base.update(kw)
    return TriggerEvent(**base)


def test_retry_redispatches_with_real_signature() -> None:
    dispatcher = create_autospec(TriggerDispatcher, instance=True)
    dispatcher.dispatch.return_value = _event()
    client, session = _client(dispatcher)

    r = client.post("/triggers/dlq/dlq-1/retry")

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["dispatched"] is True
    assert body["status"] == "dispatched"
    assert body["goal_id"] == "g-retried"
    assert session.updates and session.updates[0]["n"] == 1
    call = dispatcher.dispatch.await_args
    spec, payload, tenant_ctx = call.args[:3]
    assert spec.trigger_id == "trg-1"
    assert payload == {"marker": "retry-me"}
    assert tenant_ctx.tenant_id == TENANT
    # a row dead-lettered before the key was recorded keeps the old
    # per-attempt message id
    assert call.kwargs["message_id"] == "dlq-retry:dlq-1:1"
    assert "idempotency_key" not in call.kwargs
    assert session.resolved()  # it ran: nothing left to retry


def test_retry_dispatch_failure_is_surfaced() -> None:
    dispatcher = create_autospec(TriggerDispatcher, instance=True)
    dispatcher.dispatch.side_effect = RuntimeError("downstream exploded")
    client, _ = _client(dispatcher)

    r = client.post("/triggers/dlq/dlq-1/retry")

    assert r.status_code == 502
    assert "downstream exploded" in r.json()["detail"]


def test_retry_skip_is_reported_not_queued() -> None:
    dispatcher = create_autospec(TriggerDispatcher, instance=True)
    dispatcher.dispatch.return_value = _event(
        goal_created=False, goal_id=None, skip_reason="rate_limit"
    )
    client, _ = _client(dispatcher)

    r = client.post("/triggers/dlq/dlq-1/retry")

    assert r.status_code == 202
    assert r.json()["status"] == "skipped"
    assert r.json()["skip_reason"] == "rate_limit"
    assert r.json()["dispatched"] is False


# ── B2-OPEN-2: the retry replays the ORIGINAL firing under its dedup key ──────


def test_retry_dispatches_under_the_original_firings_key() -> None:
    dispatcher = create_autospec(TriggerDispatcher, instance=True)
    dispatcher.dispatch.return_value = _event()
    client, session = _client(dispatcher, idempotency_key="orig-key-123")

    r = client.post("/triggers/dlq/dlq-1/retry")

    assert r.status_code == 202, r.text
    call = dispatcher.dispatch.await_args
    assert call.kwargs["idempotency_key"] == "orig-key-123"
    assert "message_id" not in call.kwargs
    # A throttled retry is not dead-lettered again: this entry stays open.
    assert call.kwargs["dead_letter_throttled"] is False
    assert r.json()["status"] == "dispatched"
    assert session.resolved()


def test_retry_after_the_sender_redelivered_is_a_resolved_no_op() -> None:
    dispatcher = create_autospec(TriggerDispatcher, instance=True)
    dispatcher.dispatch.return_value = _event(goal_created=False, goal_id=None, skip_reason="dedup")
    client, session = _client(dispatcher, idempotency_key="orig-key-123")

    r = client.post("/triggers/dlq/dlq-1/retry")

    assert r.status_code == 202, r.text
    assert r.json()["status"] == "already_delivered"
    assert r.json()["dispatched"] is False
    assert session.resolved()


def test_throttled_retry_keeps_the_entry_open() -> None:
    dispatcher = create_autospec(TriggerDispatcher, instance=True)
    dispatcher.dispatch.return_value = _event(
        goal_created=False, goal_id=None, skip_reason="rate_limit"
    )
    client, session = _client(dispatcher, idempotency_key="orig-key-123")

    r = client.post("/triggers/dlq/dlq-1/retry")

    assert r.json()["status"] == "skipped"
    assert not session.resolved()
