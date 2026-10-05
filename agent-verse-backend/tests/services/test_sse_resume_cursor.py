"""SVC-05: SSE resume uses durable sequence ids.

* live (API-dispatched and worker-bridged) and fully-replayed events carried no
  ``_seq``, so the SSE id fell back to a per-connection counter and
  Last-Event-ID drifted on every reconnect;
* resume replay stopped after the first 100 events (no paging);
* the cross-replica path replayed BEFORE subscribing, so whatever was
  published in between was lost.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import fakeredis
import fakeredis.aioredis
import pytest

from app.services import goal_service as gs_mod
from app.services.goal_service import GoalRecord, GoalService, GoalStatus
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-seq", plan=PlanTier.FREE, api_key_id="k")


class _Store:
    """Event-store fake with the real store's contract: gap-free sequences,
    keyset paging on ``after_sequence`` and a ``limit`` it honours."""

    def __init__(self, n: int = 0) -> None:
        self.events: list[dict[str, Any]] = [{"type": "step", "i": i} for i in range(1, n + 1)]
        self.on_list: Any = None
        self.calls: list[tuple[int, int]] = []

    async def append_event(self, goal_id: str, event: dict[str, Any], *, tenant_ctx: Any) -> int:
        assert "_seq" not in event  # the stored payload never carries the cursor
        self.events.append(dict(event))
        return len(self.events)

    async def list_events(self, goal_id: str, *, tenant_ctx: Any, **_: Any) -> list[dict[str, Any]]:
        return [dict(e) for e in self.events]

    async def list_events_since(
        self, goal_id: str, after_sequence: int, limit: int = 100, *, tenant_ctx: Any = None
    ) -> list[dict[str, Any]]:
        self.calls.append((after_sequence, limit))
        if self.on_list is not None:
            hook, self.on_list = self.on_list, None
            await hook()
        rows = [{**e, "_seq": i} for i, e in enumerate(self.events, start=1) if i > after_sequence]
        return rows[:limit]


def _record(goal_id: str, status: GoalStatus) -> GoalRecord:
    return GoalRecord(
        goal_id=goal_id,
        goal_text="g",
        status=status,
        tenant_id=CTX.tenant_id,
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
    )


async def _drain(gen: Any) -> list[dict[str, Any]]:
    return [e async for e in gen if e.get("type") != "_sse_heartbeat"]


# ── resume pages through the whole history (> 100 events) ─────────────────────


async def test_local_resume_pages_through_the_whole_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gs_mod, "_REPLAY_PAGE_SIZE", 100)
    store = _Store(300)
    store.events.append({"type": "goal_complete"})
    svc = GoalService(event_store=store)
    svc._goals["g"] = _record("g", GoalStatus.COMPLETE)

    events = await _drain(svc.subscribe_events("g", CTX, since_sequence=50))

    assert [e["_seq"] for e in events] == list(range(51, 302))
    # keyset-paged: each page starts after the last sequence of the one before
    assert store.calls == [(50, 100), (150, 100), (250, 100)]


async def test_local_full_replay_carries_sequences() -> None:
    store = _Store(150)
    store.events.append({"type": "goal_complete"})
    svc = GoalService(event_store=store)
    svc._goals["g"] = _record("g", GoalStatus.COMPLETE)

    events = await _drain(svc.subscribe_events("g", CTX))

    assert [e["_seq"] for e in events] == list(range(1, 152))


async def test_remote_resume_pages_through_the_whole_history() -> None:
    store = _Store(260)
    store.events.append({"type": "goal_complete"})
    svc = GoalService(event_store=store)
    svc._db_get_goal_record = AsyncMock(  # type: ignore[method-assign]
        return_value=_record("r", GoalStatus.COMPLETE)
    )

    events = await _drain(svc.subscribe_events("r", CTX, since_sequence=10))

    assert [e["_seq"] for e in events] == list(range(11, 262))


# ── every live event carries its durable sequence ─────────────────────────────


async def test_live_dispatched_events_carry_their_store_sequence() -> None:
    store = _Store(4)  # four earlier events: the store, not a counter, numbers them
    svc = GoalService(event_store=store)
    rec = _record("g2", GoalStatus.EXECUTING)
    svc._goals["g2"] = rec
    q: asyncio.Queue[Any] = asyncio.Queue()
    rec.subscribers.append(q)

    await svc._dispatch_event("g2", {"type": "step_started"}, tenant_ctx=CTX)
    await svc._dispatch_event("g2", {"type": "step_complete"}, tenant_ctx=CTX)

    assert [q.get_nowait()["_seq"], q.get_nowait()["_seq"]] == [5, 6]
    assert all("_seq" not in e for e in store.events)


async def test_in_memory_events_carry_their_position_as_sequence() -> None:
    svc = GoalService()  # no durable store (dev / tests)
    rec = _record("g3", GoalStatus.EXECUTING)
    svc._goals["g3"] = rec
    q: asyncio.Queue[Any] = asyncio.Queue()
    rec.subscribers.append(q)

    for etype in ("step_started", "step_complete", "goal_complete"):
        await svc._dispatch_event("g3", {"type": etype}, tenant_ctx=CTX)

    assert [q.get_nowait()["_seq"] for _ in range(3)] == [1, 2, 3]
    resumed = await _drain(svc.subscribe_events("g3", CTX, since_sequence=1))
    assert [(e["type"], e["_seq"]) for e in resumed] == [
        ("step_complete", 2),
        ("goal_complete", 3),
    ]


async def test_unpersisted_event_has_no_sequence() -> None:
    """A failed append (event parked in the outbox) has no durable sequence:
    it must not get a made-up one that would collide with a real one."""

    class _Failing(_Store):
        async def append_event(self, *a: Any, **k: Any) -> int:
            raise RuntimeError("db down")

    svc = GoalService(event_store=_Failing())
    rec = _record("g4", GoalStatus.EXECUTING)
    svc._goals["g4"] = rec
    q: asyncio.Queue[Any] = asyncio.Queue()
    rec.subscribers.append(q)

    await svc._dispatch_event("g4", {"type": "step_started"}, tenant_ctx=CTX)

    assert "_seq" not in q.get_nowait()


async def test_worker_bridge_events_keep_the_worker_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker events fed to local subscribers by the Celery bridge keep ``_seq``."""
    import redis.asyncio as aioredis

    server = fakeredis.FakeServer()
    svc = GoalService()
    rec = _record("gw", GoalStatus.EXECUTING)
    svc._goals["gw"] = rec
    q: asyncio.Queue[Any] = asyncio.Queue()
    rec.subscribers.append(q)
    monkeypatch.setattr(
        aioredis,
        "from_url",
        lambda *_a, **_k: fakeredis.aioredis.FakeRedis(server=server, decode_responses=True),
    )
    bridge = asyncio.create_task(svc._subscribe_celery_goal_events("redis://fake"))
    try:
        pub = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
        envelope = {
            "goal_id": "gw",
            "tenant_id": CTX.tenant_id,
            "type": "step_started",
            "payload": {"type": "step_started"},
            "_seq": 9,
        }
        for _ in range(50):
            await pub.publish(f"goal_events:{CTX.tenant_id}:gw", json.dumps(envelope))
            if not q.empty():
                break
            await asyncio.sleep(0.02)
        assert q.get_nowait()["_seq"] == 9
    finally:
        bridge.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await bridge


# ── no gap and no duplicate between replay and live ───────────────────────────


async def test_local_event_dispatched_during_replay_is_delivered_once() -> None:
    store = _Store(3)
    svc = GoalService(event_store=store)
    rec = _record("l", GoalStatus.EXECUTING)
    svc._goals["l"] = rec

    async def _arrives_during_replay() -> None:
        # Persisted (seq 4) and queued while the replay reads: both copies exist.
        await svc._dispatch_event("l", {"type": "step", "i": 4}, tenant_ctx=CTX)

    store.on_list = _arrives_during_replay

    async def _finish() -> None:
        await asyncio.sleep(0.2)
        await svc._dispatch_event("l", {"type": "goal_complete"}, tenant_ctx=CTX)

    finisher = asyncio.create_task(_finish())
    events = await asyncio.wait_for(_drain(svc.subscribe_events("l", CTX)), timeout=5)
    await finisher
    assert [e["_seq"] for e in events] == [1, 2, 3, 4, 5]


async def test_remote_event_published_during_replay_is_delivered_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import redis.asyncio as aioredis

    server = fakeredis.FakeServer()
    monkeypatch.setattr(
        aioredis,
        "from_url",
        lambda *_a, **_k: fakeredis.aioredis.FakeRedis(server=server, decode_responses=True),
    )
    store = _Store(3)
    svc = GoalService(event_store=store)
    svc._redis_url_for_pubsub = "redis://fake"
    svc._db_get_goal_record = AsyncMock(  # type: ignore[method-assign]
        return_value=_record("x", GoalStatus.EXECUTING)
    )
    channel = f"goal_events:{CTX.tenant_id}:x"
    pub = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    async def _arrives_during_replay() -> None:
        # The owning replica persists seq 4 and publishes it while we replay:
        # with replay-then-subscribe it was lost, now it must arrive once.
        store.events.append({"type": "step", "i": 4})
        await pub.publish(channel, json.dumps({"type": "step", "i": 4, "_seq": 4}))

    store.on_list = _arrives_during_replay

    async def _finish() -> None:
        await asyncio.sleep(0.3)
        store.events.append({"type": "step", "i": 5})
        await pub.publish(
            channel,
            json.dumps(
                {
                    "goal_id": "x",
                    "tenant_id": CTX.tenant_id,
                    "type": "step",
                    "payload": {"type": "step", "i": 5},
                    "_seq": 5,
                }
            ),
        )
        store.events.append({"type": "goal_complete"})
        await pub.publish(channel, json.dumps({"type": "goal_complete", "_seq": 6}))

    finisher = asyncio.create_task(_finish())
    events = await asyncio.wait_for(_drain(svc.subscribe_events("x", CTX)), timeout=5)
    await finisher
    assert [e["_seq"] for e in events] == [1, 2, 3, 4, 5, 6]
    assert events[4] == {"type": "step", "i": 5, "_seq": 5}  # worker envelope unwrapped


# ── the SSE endpoint ──────────────────────────────────────────────────────────


def test_sse_endpoint_ids_come_only_from_the_durable_sequence() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.goals import router
    from app.tenancy.middleware import TenantMiddleware

    class _Svc:
        async def get_goal(self, **_: Any) -> dict[str, Any]:
            return {"tenant_id": CTX.tenant_id}

        async def subscribe_events(self, **_: Any) -> Any:
            yield {"type": "step", "_seq": 41}
            yield {"type": "unpersisted"}
            yield {"type": "goal_complete", "_seq": 42}

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return CTX if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    app.state.goal_service = _Svc()
    body = TestClient(app).get("/goals/g/stream", headers={"X-API-Key": "k"}).text
    ids = [line for line in body.splitlines() if line.startswith("id:")]
    assert ids == ["id: 41", "id: 42"]
