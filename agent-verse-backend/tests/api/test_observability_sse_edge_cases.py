"""Additional SSE edge-case coverage for app/api/observability.py::stream_logs.

tests/api/test_observability.py already covers the happy path plus a couple
of disconnect scenarios (historical burst, redis path, no-redis heartbeat,
disconnect-during-burst). This file adds the specific scenarios called out
by the audit that weren't covered:

  - client disconnect via GET /observability/logs/stream returning 401
    without a tenant (a "malformed subscription" has no valid identity);
  - backpressure: a slow consumer (many is_disconnected() checks before it
    finally disconnects) still receives every queued historical entry, in
    order, with none dropped;
  - a persistent Redis failure on the event-driven path must not turn the
    generator into a zero-backoff busy loop — regression test for the fix
    applied alongside this file (a missing `await asyncio.sleep` when
    stream_new_since returns immediately with no results).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import observability as obs


def _make_app(*, with_tenant: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(obs.router)
    app.state.goal_service = None

    @app.middleware("http")
    async def inject_tenant(request, call_next):  # type: ignore[no-untyped-def]
        if with_tenant:
            request.state.tenant = SimpleNamespace(tenant_id="tenant-1", plan="free", api_key="k")
        return await call_next(request)

    return app


class _FakeRequest:
    def __init__(self, disconnect_after: int = 0) -> None:
        self.state = SimpleNamespace(tenant=SimpleNamespace(tenant_id="tenant-1"))
        self.app = SimpleNamespace(state=SimpleNamespace(goal_service=None))
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._calls += 1
        return self._calls > self._disconnect_after


def _restore_log_store_methods() -> None:
    """Other test modules (e.g. test_observability.py) monkeypatch
    ``obs.log_store.query`` / ``.stream_new_since`` via direct instance
    attribute assignment with no teardown, which permanently shadows the
    class method on the shared module-level singleton for the rest of the
    test session. Strip any such instance-level override so this file's
    tests see the real implementation regardless of run order."""
    for name in ("query", "stream_new_since"):
        if name in vars(obs.log_store):
            delattr(obs.log_store, name)


def setup_function() -> None:
    obs.log_store._redis = None
    obs.log_store._memory_buffer = {}
    _restore_log_store_methods()


def teardown_function() -> None:
    obs.log_store._redis = None
    obs.log_store._memory_buffer = {}
    _restore_log_store_methods()


# ── malformed subscription: no tenant identity ──────────────────────────────────


def test_stream_logs_401_without_tenant() -> None:
    app = _make_app(with_tenant=False)
    client = TestClient(app)
    with client.stream("GET", "/observability/logs/stream") as resp:
        assert resp.status_code == 401


# ── backpressure: slow consumer still receives every queued entry, in order ────


async def test_stream_logs_slow_consumer_receives_all_historical_entries_in_order() -> None:
    """Simulate a slow client: is_disconnected() is polled many times before
    the client actually goes away. Every queued historical log entry must
    still be delivered, in the original order, with none dropped — the
    generator is pull-based so a slow consumer just delays iteration, it
    never causes entries to be skipped or corrupted.

    Uses 15 entries (under the historical burst's own limit=20) and stops
    the disconnect budget exactly at the end of that burst, so the test
    never falls into the live-tail no-redis branch's real 5s heartbeat
    sleep.
    """
    for i in range(15):
        await obs.log_store.emit("tenant-1", "info", f"entry-{i}")

    # 15 is_disconnected() checks (one per historical entry) all return
    # False; the 16th (top of the live-tail while-loop) returns True and
    # breaks *before* the heartbeat/sleep branch is ever reached.
    request = _FakeRequest(disconnect_after=15)
    response = await obs.stream_logs(request)  # type: ignore[arg-type]

    chunks = [chunk async for chunk in response.body_iterator]
    joined = "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)

    for i in range(15):
        assert f"entry-{i}" in joined

    # Order preserved: entry-0 must appear before entry-14 in the stream.
    assert joined.index("entry-0") < joined.index("entry-14")


async def test_stream_logs_backpressure_does_not_yield_more_than_available() -> None:
    """A consumer that never disconnects during the historical burst still
    only ever receives the entries that exist — the generator terminates the
    burst loop cleanly and moves on to the live-tail phase without
    fabricating or duplicating entries."""
    await obs.log_store.emit("tenant-1", "info", "only-one")

    request = _FakeRequest(disconnect_after=1)  # disconnects right after the burst
    response = await obs.stream_logs(request)  # type: ignore[arg-type]
    chunks = [chunk async for chunk in response.body_iterator]
    joined = "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)

    assert joined.count("only-one") == 1


# ── persistent Redis failure must not busy-loop ─────────────────────────────────


async def test_stream_logs_redis_failure_storm_backs_off_between_iterations(
    monkeypatch: Any,
) -> None:
    """Regression test: if stream_new_since keeps returning [] immediately
    (e.g. Redis is down and every call fails before the blocking read even
    starts), the generator must still pause between iterations instead of
    spinning the event loop and hammering Redis with reconnect attempts."""
    fake_redis = AsyncMock()
    obs.log_store._redis = fake_redis
    obs.log_store.query = AsyncMock(return_value=[])  # type: ignore[method-assign]

    async def _always_empty(tenant_id: str, last_id: str = "$") -> list[dict[str, Any]]:
        return []  # simulates stream_new_since swallowing a persistent Redis error

    obs.log_store.stream_new_since = _always_empty  # type: ignore[method-assign]

    sleep_calls: list[float] = []

    async def _tracking_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(obs.asyncio, "sleep", _tracking_sleep)

    # Let the loop run several iterations before disconnecting.
    request = _FakeRequest(disconnect_after=5)
    response = await obs.stream_logs(request)  # type: ignore[arg-type]
    _ = [chunk async for chunk in response.body_iterator]

    # Every empty-result iteration on the Redis path must have backed off —
    # without the fix this list would be empty (zero backoff, busy loop).
    assert len(sleep_calls) >= 3
    assert all(s > 0 for s in sleep_calls)


async def test_stream_logs_redis_path_with_results_does_not_add_extra_backoff() -> None:
    """When entries ARE flowing, no artificial sleep should be inserted —
    the backoff only guards the empty-result case."""
    fake_redis = AsyncMock()
    obs.log_store._redis = fake_redis
    obs.log_store.query = AsyncMock(return_value=[])  # type: ignore[method-assign]

    call_count = {"n": 0}

    async def _stream_new_since(tenant_id: str, last_id: str = "$") -> list[dict[str, Any]]:
        call_count["n"] += 1
        return [{"id": f"e{call_count['n']}", "message": "live", "_stream_id": f"{call_count['n']}-0"}]

    obs.log_store.stream_new_since = _stream_new_since  # type: ignore[method-assign]

    sleep_calls: list[float] = []

    async def _tracking_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    import app.api.observability as obs_module

    orig_sleep = obs_module.asyncio.sleep
    obs_module.asyncio.sleep = _tracking_sleep  # type: ignore[assignment]
    try:
        request = _FakeRequest(disconnect_after=2)
        response = await obs.stream_logs(request)  # type: ignore[arg-type]
        _ = [chunk async for chunk in response.body_iterator]
    finally:
        obs_module.asyncio.sleep = orig_sleep  # type: ignore[assignment]

    assert sleep_calls == []
