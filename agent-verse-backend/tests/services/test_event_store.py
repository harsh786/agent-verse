"""Tests for durable goal event storage."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.services.event_store import EventStore
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="tenant-events", plan=PlanTier.PROFESSIONAL, api_key_id="key")


class _ScalarResult:
    def __init__(self, value: int | None = None, rows: list[Any] | None = None) -> None:
        self._value = value
        self._rows = rows or []

    def scalar_one_or_none(self) -> int | None:
        return self._value

    def scalars(self) -> _ScalarResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self._events: list[Any] = []

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    def begin(self) -> _FakeSession:
        return self

    def add(self, instance: Any) -> None:
        self.added.append(instance)
        self._events.append(instance)

    async def execute(
        self, statement: object, params: dict[str, str] | None = None
    ) -> _ScalarResult:
        query = str(statement)
        if "max" in query.lower():
            sequences = [event.sequence for event in self._events]
            return _ScalarResult(max(sequences) if sequences else None)
        return _ScalarResult(rows=list(self._events))


@asynccontextmanager
async def _noop_rls_context(session: Any, tenant_id: str) -> AsyncIterator[Any]:
    yield session


async def test_event_store_append_and_list_preserves_payload_order(
    monkeypatch: Any,
) -> None:
    from app.services import event_store as event_store_module

    monkeypatch.setattr(event_store_module, "sqlalchemy_rls_context", _noop_rls_context)

    session = _FakeSession()
    store = EventStore(lambda: session)

    await store.append_event("goal-1", {"type": "goal_started"}, tenant_ctx=TENANT)
    await store.append_event("goal-1", {"type": "goal_complete"}, tenant_ctx=TENANT)

    assert [event.sequence for event in session.added] == [1, 2]
    assert await store.list_events("goal-1", tenant_ctx=TENANT) == [
        {"type": "goal_started"},
        {"type": "goal_complete"},
    ]
