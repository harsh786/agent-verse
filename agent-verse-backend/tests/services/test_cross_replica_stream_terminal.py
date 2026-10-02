"""SVC-06: the cross-replica goal stream ends on every terminal event, emits
heartbeats while idle, and fails loudly when it cannot deliver.

The loop stopped only on goal_complete/failed/cancelled, so worker_failed
(timeout, crash, lock failure) left the stream open forever; there was no
heartbeat, so WAITING_HUMAN goals held idle connections; with no Redis URL it
returned an empty stream.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import fakeredis
import fakeredis.aioredis
import pytest

from app.core.errors import ServiceUnavailableError
from app.services import goal_service as gs_mod
from app.services.goal_service import GoalRecord, GoalService, GoalStatus
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-x", plan=PlanTier.FREE, api_key_id="k")


def _db_record(goal_id: str, status: GoalStatus = GoalStatus.EXECUTING) -> GoalRecord:
    return GoalRecord(
        goal_id=goal_id,
        goal_text="g",
        status=status,
        tenant_id=CTX.tenant_id,
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
    )


def _remote_svc(monkeypatch: pytest.MonkeyPatch, server: Any, goal_id: str) -> GoalService:
    import redis.asyncio as aioredis

    svc = GoalService()
    svc._redis_url_for_pubsub = "redis://fake"
    svc._db_get_goal_record = AsyncMock(return_value=_db_record(goal_id))  # type: ignore[method-assign]
    svc._list_persisted_events = AsyncMock(return_value=[])  # type: ignore[method-assign]
    svc._list_events_since_persisted = AsyncMock(return_value=[])  # type: ignore[method-assign]
    monkeypatch.setattr(
        aioredis,
        "from_url",
        lambda *_a, **_k: fakeredis.aioredis.FakeRedis(server=server, decode_responses=True),
    )
    return svc


async def _publish_soon(server: Any, channel: str, payloads: list[dict[str, Any]]) -> None:
    pub = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    await asyncio.sleep(0.2)
    for p in payloads:
        await pub.publish(channel, json.dumps(p))


async def test_worker_failed_ends_the_cross_replica_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = fakeredis.FakeServer()
    svc = _remote_svc(monkeypatch, server, "g-wf")
    channel = f"goal_events:{CTX.tenant_id}:g-wf"
    envelope = {
        "goal_id": "g-wf",
        "tenant_id": CTX.tenant_id,
        "type": "worker_failed",
        "payload": {"type": "worker_failed", "error": "timeout"},
    }
    pub = asyncio.create_task(_publish_soon(server, channel, [envelope]))

    async def _collect() -> list[dict[str, Any]]:
        return [e async for e in svc.subscribe_events("g-wf", CTX)]

    events = await asyncio.wait_for(_collect(), timeout=5)
    await pub
    assert [e["type"] for e in events if e["type"] != "_sse_heartbeat"] == ["worker_failed"]
    assert events[-1].get("error") == "timeout"  # the worker envelope is unwrapped


async def test_worker_complete_waiting_human_does_not_end_the_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = fakeredis.FakeServer()
    svc = _remote_svc(monkeypatch, server, "g-wh")
    channel = f"goal_events:{CTX.tenant_id}:g-wh"
    msgs = [
        {
            "goal_id": "g-wh",
            "type": "worker_complete",
            "payload": {"type": "worker_complete", "status": "waiting_human"},
        },
        {"goal_id": "g-wh", "type": "goal_complete", "payload": {"type": "goal_complete"}},
    ]
    pub = asyncio.create_task(_publish_soon(server, channel, msgs))

    async def _collect() -> list[str]:
        return [e["type"] async for e in svc.subscribe_events("g-wh", CTX)]

    types = await asyncio.wait_for(_collect(), timeout=5)
    await pub
    assert [t for t in types if t != "_sse_heartbeat"] == ["worker_complete", "goal_complete"]


async def test_idle_cross_replica_stream_emits_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gs_mod, "_SSE_HEARTBEAT_SECONDS", 0.05)
    server = fakeredis.FakeServer()
    svc = _remote_svc(monkeypatch, server, "g-idle")
    gen = svc.subscribe_events("g-idle", CTX)
    first = await asyncio.wait_for(gen.__anext__(), timeout=5)
    await gen.aclose()
    assert first["type"] == "_sse_heartbeat"


async def test_no_pubsub_url_is_unavailable_not_an_empty_stream() -> None:
    svc = GoalService()
    svc._db_get_goal_record = AsyncMock(return_value=_db_record("g-n"))  # type: ignore[method-assign]
    svc._list_persisted_events = AsyncMock(return_value=[])  # type: ignore[method-assign]
    with pytest.raises(ServiceUnavailableError):
        async for _ in svc.subscribe_events("g-n", CTX):
            pass


async def test_idle_local_stream_emits_heartbeats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gs_mod, "_SSE_HEARTBEAT_SECONDS", 0.05)
    svc = GoalService()
    rec = _db_record("g-loc", GoalStatus.WAITING_HUMAN)
    svc._goals["g-loc"] = rec
    gen = svc.subscribe_events("g-loc", CTX)
    first = await asyncio.wait_for(gen.__anext__(), timeout=5)
    await gen.aclose()
    assert first["type"] == "_sse_heartbeat"
    assert rec.subscribers == []  # closing the stream unregisters it


async def test_goal_that_ended_before_subscribe_ends_the_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The terminal event was published before SUBSCRIBE (pub/sub keeps no
    history): the stream must still end, delivering the persisted tail once."""
    server = fakeredis.FakeServer()
    svc = _remote_svc(monkeypatch, server, "g-race")
    svc._db_get_goal_record = AsyncMock(  # type: ignore[method-assign]
        side_effect=[_db_record("g-race"), _db_record("g-race", GoalStatus.COMPLETE)]
    )
    svc._list_persisted_events = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"type": "step_started"}]
    )
    svc._list_events_since_persisted = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {"type": "step_started", "_seq": 1},  # already replayed: not repeated
            {"type": "goal_complete", "_seq": 2},
        ]
    )

    async def _collect() -> list[dict[str, Any]]:
        return [e async for e in svc.subscribe_events("g-race", CTX)]

    events = await asyncio.wait_for(_collect(), timeout=5)
    assert [e["type"] for e in events] == ["step_started", "goal_complete"]


async def test_idle_stream_ends_when_the_goal_row_turns_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker published its terminal event before the goal row said so: the
    idle re-check (with backoff) ends the stream instead of heartbeating forever."""
    monkeypatch.setattr(gs_mod, "_SSE_HEARTBEAT_SECONDS", 0.02)
    server = fakeredis.FakeServer()
    svc = _remote_svc(monkeypatch, server, "g-late")
    statuses = [GoalStatus.EXECUTING, GoalStatus.EXECUTING, GoalStatus.EXECUTING]

    async def _record(*_a: Any, **_k: Any) -> GoalRecord:
        return _db_record("g-late", statuses.pop(0) if statuses else GoalStatus.FAILED)

    svc._db_get_goal_record = _record  # type: ignore[method-assign]
    svc._list_events_since_persisted = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"type": "worker_failed", "_seq": 7, "error": "timeout"}]
    )

    async def _collect() -> list[dict[str, Any]]:
        return [e async for e in svc.subscribe_events("g-late", CTX)]

    events = await asyncio.wait_for(_collect(), timeout=5)
    assert events[-1]["type"] == "worker_failed"
    assert events[:-1] and all(e["type"] == "_sse_heartbeat" for e in events[:-1])
