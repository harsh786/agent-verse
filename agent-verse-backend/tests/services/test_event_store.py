"""Tests for durable goal event storage."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from app.services.event_store import EventStore
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="tenant-events", plan=PlanTier.PROFESSIONAL, api_key_id="key")


class _FakeSession:
    """Simulates an async SQLAlchemy session supporting raw SQL INSERT/SELECT.

    The current EventStore.append_event uses a single INSERT ... SELECT MAX(sequence) + 1
    statement (via session.execute(text(...))), so this fake simulates the sequence
    computation and stores appended payloads in memory for list_events to return.
    """

    def __init__(self) -> None:
        self._events: list[SimpleNamespace] = []
        self._call_count: int = 0

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    def begin(self) -> _FakeSession:
        return self

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        """Handle both INSERT (append_event) and SELECT (list_events) statements."""
        query = str(statement)
        self._call_count += 1

        if params and "gid" in params:
            # INSERT path: compute next sequence and store the event
            next_seq = len(self._events) + 1
            import json as _json
            payload_str = params.get("payload", "{}")
            # Remove ::jsonb cast suffix if present
            if isinstance(payload_str, str):
                payload_data = _json.loads(payload_str)
            else:
                payload_data = {}
            self._events.append(
                SimpleNamespace(
                    sequence=next_seq,
                    payload=payload_data,
                    tenant_id=params.get("tid"),
                    goal_id=params.get("gid"),
                )
            )
            return SimpleNamespace(rowcount=1)

        # SELECT path (list_events): return all stored events
        class _Result:
            def __init__(self, rows: list[Any]) -> None:
                self._rows = rows

            def scalars(self) -> _Result:
                return self

            def all(self) -> list[Any]:
                return self._rows

        return _Result(list(self._events))


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

    assert [event.sequence for event in session._events] == [1, 2]
    assert await store.list_events("goal-1", tenant_ctx=TENANT) == [
        {"type": "goal_started"},
        {"type": "goal_complete"},
    ]
