"""SSE subscribers must survive a GoalRecord refresh from the DB (Celery mode).

Regression: ``subscribe_events`` registered its queue on the in-memory record,
then ``_refresh_goal_from_db_if_needed`` -> ``_db_get_goal_record`` replaced
``self._goals[goal_id]`` with a brand-new record (empty ``subscribers``). The
Celery Redis bridge looks the record up in ``self._goals`` and so fed only the
new object: the SSE client never saw live events nor its end-of-stream marker
and hung forever.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext


def _ctx() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")


def _async_cm(value: Any) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=value)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _db_returning_goal_row() -> MagicMock:
    row = MagicMock()
    row.id = "g1"
    row.goal_text = "do it"
    row.status = "executing"
    row.tenant_id = "t1"
    row.priority = "normal"
    row.dry_run = False
    row.created_at = datetime.now(UTC)
    row.agent_id = None
    row.workflow_mode = "single_agent"
    row.execution_context = {}
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=row))
    )
    session.begin = MagicMock(return_value=_async_cm(None))
    db = MagicMock()
    db.return_value = _async_cm(session)
    return db


class _FakePubSub:
    def __init__(self, inbox: asyncio.Queue[dict[str, Any]]) -> None:
        self._inbox = inbox

    async def psubscribe(self, *_: Any) -> None:
        return None

    async def listen(self) -> Any:
        while True:
            yield await self._inbox.get()


class _FakeRedis:
    def __init__(self, inbox: asyncio.Queue[dict[str, Any]]) -> None:
        self._inbox = inbox

    async def __aenter__(self) -> _FakeRedis:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self._inbox)


def _msg(etype: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "pmessage",
        "data": json.dumps(
            {"goal_id": "g1", "tenant_id": "t1", "type": etype, "payload": payload or {}}
        ),
    }


@pytest.mark.asyncio
async def test_subscriber_survives_db_refresh_and_gets_bridge_events() -> None:
    svc = GoalService(db_session_factory=_db_returning_goal_row(), task_queue=MagicMock())
    original = GoalRecord(
        goal_id="g1",
        goal_text="do it",
        status=GoalStatus.EXECUTING,
        tenant_id="t1",
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
    )
    svc._goals["g1"] = original
    svc._events_for_replay = AsyncMock(return_value=[])  # type: ignore[method-assign]

    received: list[dict[str, Any]] = []

    async def _consume() -> None:
        async for ev in svc.subscribe_events("g1", _ctx()):
            received.append(ev)

    inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    with patch("redis.asyncio.from_url", return_value=_FakeRedis(inbox)):
        consumer = asyncio.create_task(_consume())
        # Wait until subscribe_events has refreshed the record from the DB
        # (Celery mode always refreshes) and is blocked on its queue.
        for _ in range(200):
            if svc._goals["g1"] is not original:
                break
            await asyncio.sleep(0.005)
        assert svc._goals["g1"] is not original, "refresh from DB did not happen"
        await asyncio.sleep(0.01)

        bridge = asyncio.create_task(svc._subscribe_celery_goal_events("redis://fake"))
        inbox.put_nowait(_msg("step_complete", {"step": 1}))
        inbox.put_nowait(_msg("goal_complete", {"result": "ok"}))
        try:
            await asyncio.wait_for(consumer, timeout=2.0)
        finally:
            bridge.cancel()
            consumer.cancel()
            await asyncio.gather(bridge, consumer, return_exceptions=True)

    types = [e["type"] for e in received]
    assert types == ["step_complete", "goal_complete"]
    # Queue was deregistered from the shared registry on exit.
    assert svc._goals["g1"].subscribers == []
