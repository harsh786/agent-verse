"""Unit tests for the TriggerConsumerSupervisor (WT-4).

The supervisor owns the long-running trigger consumers (chain/HITL/memory),
spawning one asyncio task per consumer whose dependencies are satisfied and
cancelling+awaiting them all on stop.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from app.triggers.supervisor import TriggerConsumerSupervisor

# ── Fakes ─────────────────────────────────────────────────────────────────────


class _FakePubSub:
    """Minimal async pub/sub: records subscriptions, then blocks forever."""

    def __init__(self, record: list[str]) -> None:
        self._record = record

    async def subscribe(self, *channels: str) -> None:
        self._record.extend(channels)

    async def listen(self) -> Any:
        # One non-"message" frame (consumers skip it), then block until cancelled.
        yield {"type": "subscribe", "channel": b"chan", "data": 1}
        while True:
            await asyncio.sleep(3600)


class FakeRedis:
    def __init__(self) -> None:
        self.subscribed_channels: list[str] = []

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self.subscribed_channels)

    async def set(self, *_a: Any, **_k: Any) -> Any:
        return True


class FakeStore:
    async def find_by_type_async(self, *_a: Any, **_k: Any) -> list[Any]:
        return []


class FakeDispatcher:
    async def dispatch(self, *_a: Any, **_k: Any) -> None:
        return None


def _full_supervisor(**overrides: Any) -> TriggerConsumerSupervisor:
    kwargs: dict[str, Any] = {
        "schedule_store": FakeStore(),
        "dispatcher": FakeDispatcher(),
        "redis": FakeRedis(),
    }
    kwargs.update(overrides)
    return TriggerConsumerSupervisor(**kwargs)


# ── start() spawns one task per available consumer ──────────────────────────────


async def test_start_spawns_one_task_per_core_consumer() -> None:
    sup = _full_supervisor()
    try:
        await sup.start()
        # chain, hitl, memory
        assert len(sup.tasks) == 3
        assert {c.__class__.__name__ for c in sup.consumers} == {
            "ChainTriggerConsumer",
            "HITLTriggerConsumer",
            "MemoryTriggerConsumer",
        }
        assert not sup.skipped
    finally:
        await sup.stop()


async def test_start_skips_all_when_redis_missing() -> None:
    sup = _full_supervisor(redis=None)
    try:
        await sup.start()
        assert sup.tasks == []
        # All three core consumers skipped for the missing dependency.
        assert len(sup.skipped) == 3
        assert all(reason == "missing_deps" for _, reason in sup.skipped)
    finally:
        await sup.stop()


async def test_start_skips_when_dispatcher_missing() -> None:
    sup = _full_supervisor(dispatcher=None)
    try:
        await sup.start()
        assert sup.tasks == []
        assert len(sup.skipped) == 3
    finally:
        await sup.stop()


async def test_extended_families_skipped_when_flag_off() -> None:
    # Default: extended families are not built at all.
    sup = _full_supervisor()
    try:
        await sup.start()
        names = {c.__class__.__name__ for c in sup.consumers}
        assert "MQTTTriggerConsumer" not in names
    finally:
        await sup.stop()


async def test_extended_families_mix_when_flag_on() -> None:
    # With the flag on but no external clients wired, core consumers start
    # while the extended (MQTT) consumer is skipped — a genuine mixed result.
    sup = _full_supervisor(enable_extended=True)
    try:
        await sup.start()
        started = {c.__class__.__name__ for c in sup.consumers}
        assert "ChainTriggerConsumer" in started
        assert len(sup.tasks) == 3
        skipped_names = {name for name, _ in sup.skipped}
        assert "MQTTTriggerConsumer" in skipped_names
    finally:
        await sup.stop()


# ── stop() cancels+awaits all ───────────────────────────────────────────────────


async def test_stop_cancels_and_awaits_all_tasks() -> None:
    sup = _full_supervisor()
    await sup.start()
    tasks = list(sup.tasks)
    assert tasks and all(not t.done() for t in tasks)

    await sup.stop()

    assert all(t.done() for t in tasks)
    assert sup.tasks == []
    # Idempotent: a second stop is a no-op.
    await sup.stop()


async def test_start_is_idempotent() -> None:
    sup = _full_supervisor()
    try:
        await sup.start()
        first = list(sup.tasks)
        await sup.start()  # second call must not double-spawn
        assert sup.tasks == first
    finally:
        await sup.stop()


# ── Under an ASGI lifespan, a consumer's .start is invoked ───────────────────────


@contextlib.asynccontextmanager
async def _drive_asgi_lifespan(app: Any) -> Any:
    """Drive the ASGI lifespan protocol (equivalent to asgi_lifespan)."""
    to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    startup_done = asyncio.Event()
    shutdown_done = asyncio.Event()

    async def receive() -> dict[str, Any]:
        return await to_app.get()

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "lifespan.startup.complete":
            startup_done.set()
        elif message["type"] == "lifespan.shutdown.complete":
            shutdown_done.set()

    app_task = asyncio.create_task(app({"type": "lifespan"}, receive, send))
    await to_app.put({"type": "lifespan.startup"})
    await asyncio.wait_for(startup_done.wait(), timeout=5)
    try:
        yield
    finally:
        await to_app.put({"type": "lifespan.shutdown"})
        await asyncio.wait_for(shutdown_done.wait(), timeout=5)
        with contextlib.suppress(asyncio.CancelledError):
            await app_task


async def test_consumer_start_invoked_under_lifespan() -> None:
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    fake_redis = FakeRedis()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        sup = TriggerConsumerSupervisor(
            schedule_store=FakeStore(),
            dispatcher=FakeDispatcher(),
            redis=fake_redis,
        )
        app.state.trigger_consumers = sup
        await sup.start()
        try:
            yield
        finally:
            await sup.stop()

    app = FastAPI(lifespan=lifespan)

    async with _drive_asgi_lifespan(app):
        # start() ran → each consumer subscribed to its Redis channels.
        await asyncio.sleep(0.05)
        assert fake_redis.subscribed_channels, "consumer .start() was not invoked"
        assert "goal.completed" in fake_redis.subscribed_channels
        sup = app.state.trigger_consumers
        assert len(sup.tasks) == 3

    # After shutdown, tasks were cancelled and awaited.
    assert app.state.trigger_consumers.tasks == []
