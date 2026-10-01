"""SVC-08: the durable event store never silently drops events or replays an
outage as an empty history.

* append_event swallowed the final failure after 3 retries (and only logged a
  missing goal row); both callers swallowed again -> events vanished.
* list_events / list_events_since returned [] on a DB error -> a client saw a
  goal with no history.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest

from app.core.errors import ServiceUnavailableError
from app.services import event_store as es_mod
from app.services.event_store import (
    GOAL_EVENT_OUTBOX_KEY,
    EventAppendError,
    EventStore,
    buffer_failed_event,
    drain_event_outbox,
)
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-ev", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _failing_db() -> Any:
    @asynccontextmanager
    async def _factory() -> AsyncIterator[Any]:
        raise ConnectionError("db down")
        yield  # pragma: no cover

    return _factory


def _db_returning(seq: int | None) -> Any:
    @asynccontextmanager
    async def _factory() -> AsyncIterator[Any]:
        session = MagicMock()
        session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=seq))
        )

        @asynccontextmanager
        async def _begin() -> AsyncIterator[None]:
            yield None

        session.begin = _begin
        yield session

    return _factory


async def test_append_returns_the_allocated_sequence() -> None:
    assert await EventStore(_db_returning(7)).append_event("g", {"type": "x"}, tenant_ctx=T) == 7


async def test_append_failure_after_retries_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(es_mod.asyncio, "sleep", AsyncMock())
    with pytest.raises(EventAppendError):
        await EventStore(_failing_db()).append_event("g", {"type": "x"}, tenant_ctx=T)


async def test_append_to_missing_goal_row_raises() -> None:
    with pytest.raises(EventAppendError):
        await EventStore(_db_returning(None)).append_event("g", {"type": "x"}, tenant_ctx=T)


async def test_list_failures_surface_as_503() -> None:
    store = EventStore(_failing_db())
    with pytest.raises(ServiceUnavailableError):
        await store.list_events("g", tenant_ctx=T)
    with pytest.raises(ServiceUnavailableError):
        await store.list_events_since("g", 0, tenant_ctx=T)


async def test_failed_append_is_buffered_and_replayed_by_the_drain() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    assert await buffer_failed_event(redis, tenant_id="t-ev", goal_id="g1", event={"type": "a"})
    assert await buffer_failed_event(redis, tenant_id="t-ev", goal_id="g1", event={"type": "b"})

    down = MagicMock()
    down.append_event = AsyncMock(side_effect=EventAppendError("still down"))
    assert await drain_event_outbox(down, redis) == {"replayed": 0, "dropped": 0}
    assert await redis.llen(GOAL_EVENT_OUTBOX_KEY) == 2  # nothing lost

    up = MagicMock()
    up.append_event = AsyncMock(return_value=1)
    assert await drain_event_outbox(up, redis) == {"replayed": 2, "dropped": 0}
    order = [c.args[1]["type"] for c in up.append_event.await_args_list]
    assert order == ["a", "b"]  # oldest first
    assert all(c.kwargs["tenant_ctx"].tenant_id == "t-ev" for c in up.append_event.await_args_list)
    assert await redis.llen(GOAL_EVENT_OUTBOX_KEY) == 0


async def test_event_that_never_lands_is_eventually_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(es_mod, "_OUTBOX_MAX_ATTEMPTS", 2)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await buffer_failed_event(redis, tenant_id="t", goal_id="ghost", event={"type": "a"})
    down = MagicMock()
    down.append_event = AsyncMock(side_effect=EventAppendError("no goal row"))
    await drain_event_outbox(down, redis)
    assert await drain_event_outbox(down, redis) == {"replayed": 0, "dropped": 1}
    assert await redis.llen(GOAL_EVENT_OUTBOX_KEY) == 0


async def test_goal_service_buffers_a_failed_append() -> None:
    from app.services.goal_service import GoalRecord, GoalService, GoalStatus

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = MagicMock()
    store.append_event = AsyncMock(side_effect=EventAppendError("down"))
    svc = GoalService(event_store=store)
    svc._redis = redis
    rec = GoalRecord(
        goal_id="g9",
        goal_text="x",
        status=GoalStatus.EXECUTING,
        tenant_id="t-ev",
        priority="normal",
        dry_run=False,
        created_at="",
    )
    await svc._persist_event("g9", {"type": "step_started"}, rec, T)
    raw = await redis.lrange(GOAL_EVENT_OUTBOX_KEY, 0, -1)
    assert [json.loads(r)["event"]["type"] for r in raw] == ["step_started"]


async def test_goal_service_replay_does_not_hide_an_outage() -> None:
    from app.services.goal_service import GoalRecord, GoalService, GoalStatus

    store = MagicMock()
    store.list_events = AsyncMock(side_effect=ServiceUnavailableError("down"))
    svc = GoalService(event_store=store)
    rec = GoalRecord(
        goal_id="g9",
        goal_text="x",
        status=GoalStatus.COMPLETE,
        tenant_id="t-ev",
        priority="normal",
        dry_run=False,
        created_at="",
    )
    with pytest.raises(ServiceUnavailableError):
        await svc._events_for_replay("g9", rec, T)
