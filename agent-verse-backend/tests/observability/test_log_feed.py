"""The structlog → tenant log store feed behind ``GET /observability/logs``.

Regression: ``StructuredLogStore.emit`` had no callers, so the logs endpoint was
always empty. The ``feed_tenant_log_store`` structlog processor now records every
log line emitted while a tenant is bound (structlog contextvars, or the goal-run
OTel baggage) into the bounded, shared store. It must never raise into the
logging path and never alter the line being logged.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog

from app.observability import log_store as log_store_mod
from app.observability.log_store import StructuredLogStore, log_store
from app.observability.logging import configure_logging, feed_tenant_log_store

_T = "tenant-feed"


@pytest.fixture(autouse=True)
def _isolate() -> Any:
    log_store.reset()
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()
    log_store.reset()
    structlog.reset_defaults()


def _memory(tenant: str = _T) -> list[dict[str, Any]]:
    return log_store.memory_snapshot(tenant)


def test_records_when_tenant_bound_in_contextvars() -> None:
    structlog.contextvars.bind_contextvars(tenant_id=_T, goal_id="g1")
    event = {"event": "step_done", "level": "info", "step": 3, "timestamp": "2026-01-01T00:00:00Z"}
    out = feed_tenant_log_store(None, "info", dict(event))

    assert out == event  # the logged line is untouched
    [entry] = _memory()
    assert entry["message"] == "step_done"
    assert entry["level"] == "info"
    assert entry["goal_id"] == "g1"
    assert entry["step"] == "3"
    assert entry["timestamp"] == "2026-01-01T00:00:00Z"


def test_no_tenant_bound_records_nothing() -> None:
    feed_tenant_log_store(None, "info", {"event": "boot", "level": "info"})
    # An explicit tenant_id kwarg is not a binding: only contextvars/baggage count.
    feed_tenant_log_store(None, "info", {"event": "x", "tenant_id": _T})
    assert log_store.memory_tenants() == []


def test_records_when_tenant_comes_from_goal_run_baggage() -> None:
    from app.observability.trace_propagation import run_context

    with run_context(goal_id="g-bag", tenant_id=_T):
        feed_tenant_log_store(None, "error", {"event": "tool_failed", "level": "error"})
    [entry] = _memory()
    assert entry["goal_id"] == "g-bag"
    assert entry["level"] == "error"


def test_secret_looking_fields_are_redacted_and_values_truncated() -> None:
    structlog.contextvars.bind_contextvars(tenant_id=_T)
    feed_tenant_log_store(
        None,
        "info",
        {
            "event": "call",
            "api_key": "sk-live-123",
            "authorization": "Bearer abc",
            "client_secret": "s3",
            "payload": "x" * 5000,
        },
    )
    [entry] = _memory()
    assert entry["api_key"] == "[redacted]"
    assert entry["authorization"] == "[redacted]"
    assert entry["client_secret"] == "[redacted]"
    assert len(entry["payload"]) <= StructuredLogStore.MAX_FIELD_CHARS + 1


def test_processor_never_raises_when_store_breaks(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("store exploded")

    monkeypatch.setattr(log_store, "record_nowait", _boom)
    structlog.contextvars.bind_contextvars(tenant_id=_T)
    event = {"event": "still_logged"}
    assert feed_tenant_log_store(None, "info", event) is event


def test_processor_is_not_reentrant(monkeypatch: pytest.MonkeyPatch) -> None:
    """A log line emitted *while* recording must not recurse into the store."""
    calls = {"n": 0}
    original = log_store.record_nowait

    def _recording(tenant_id: str, entry: dict[str, Any]) -> None:
        calls["n"] += 1
        feed_tenant_log_store(None, "info", {"event": "nested"})
        original(tenant_id, entry)

    monkeypatch.setattr(log_store, "record_nowait", _recording)
    structlog.contextvars.bind_contextvars(tenant_id=_T)
    feed_tenant_log_store(None, "info", {"event": "outer"})
    assert calls["n"] == 1
    assert [e["message"] for e in _memory()] == ["outer"]


def test_memory_buffer_is_bounded_per_tenant_and_in_tenant_count() -> None:
    store = StructuredLogStore(max_memory_per_tenant=5, max_memory_tenants=3)
    for i in range(20):
        store.record_nowait("t0", {"message": f"m{i}", "level": "info"})
    assert [e["message"] for e in store.memory_snapshot("t0")] == [f"m{i}" for i in range(15, 20)]

    for t in ("t1", "t2", "t3"):
        store.record_nowait(t, {"message": "x", "level": "info"})
    # Oldest tenant evicted; never more than max_memory_tenants buffers.
    assert len(store.memory_tenants()) == 3
    assert "t0" not in store.memory_tenants()


async def test_redis_mode_writes_bounded_stream_with_ttl() -> None:
    redis = AsyncMock()
    store = StructuredLogStore()
    store.set_redis(redis)  # captures the running loop

    store.record_nowait(_T, {"message": "hello", "level": "info", "goal_id": ""})
    for _ in range(5):
        await asyncio.sleep(0)
    await store.drain()

    redis.xadd.assert_awaited_once()
    key, fields = redis.xadd.await_args.args
    assert key == f"av:logs:{_T}"
    assert fields["message"] == "hello"
    assert "goal_id" not in fields  # empty values dropped
    assert redis.xadd.await_args.kwargs["maxlen"] == StructuredLogStore.MAX_STREAM_LEN
    redis.expire.assert_awaited_once_with(key, StructuredLogStore.STREAM_TTL_S)
    assert store.memory_tenants() == []  # nothing split into a per-process buffer


async def test_redis_write_failure_is_counted_not_raised() -> None:
    redis = AsyncMock()
    redis.xadd.side_effect = ConnectionError("down")
    store = StructuredLogStore()
    store.set_redis(redis)
    store.record_nowait(_T, {"message": "lost", "level": "info"})
    for _ in range(5):
        await asyncio.sleep(0)
    await store.drain()
    assert store.stats()["write_errors"] == 1


async def test_redis_mode_sheds_load_when_too_many_writes_in_flight() -> None:
    gate = asyncio.Event()

    async def _slow_xadd(*_a: Any, **_k: Any) -> None:
        await gate.wait()

    redis = AsyncMock()
    redis.xadd.side_effect = _slow_xadd
    store = StructuredLogStore(max_inflight=2)
    store.set_redis(redis)
    for i in range(5):
        store.record_nowait(_T, {"message": f"m{i}", "level": "info"})
    assert store.stats()["dropped"] == 3
    gate.set()
    for _ in range(5):
        await asyncio.sleep(0)
    await store.drain()


async def test_emit_redis_failure_raises_to_direct_callers() -> None:
    redis = AsyncMock()
    redis.xadd.side_effect = ConnectionError("down")
    store = StructuredLogStore()
    store.set_redis(redis)
    with pytest.raises(ConnectionError):
        await store.emit(_T, "info", "direct")


def test_configure_logging_installs_the_feed_before_rendering(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", json_logs=True)
    procs = structlog.get_config()["processors"]
    assert feed_tenant_log_store in procs
    assert procs.index(feed_tenant_log_store) < len(procs) - 1  # renderer stays last
    assert procs.index(structlog.contextvars.merge_contextvars) < procs.index(
        feed_tenant_log_store
    )

    structlog.contextvars.bind_contextvars(tenant_id=_T)
    structlog.get_logger("t").warning("through_the_pipeline", n=1)
    capsys.readouterr()
    [entry] = _memory()
    assert entry["message"] == "through_the_pipeline"
    assert entry["level"] == "warning"


def test_module_singleton_is_shared_with_the_api() -> None:
    from app.api import observability as obs

    assert obs.log_store is log_store_mod.log_store
