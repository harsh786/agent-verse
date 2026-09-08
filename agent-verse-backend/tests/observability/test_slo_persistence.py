"""SLO burn-rate tracker must survive process restart when a Redis backend is supplied.

D-21b: the in-memory SLOTracker resets burn-rate/error-budget state on restart. With an
optional Redis-like backend, recorded events are persisted so that a *fresh* tracker instance
bound to the same backend reads the same state back.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.observability.slo_tracker import SLODefinition, SLOTracker


class _InMemoryRedisDouble:
    """Minimal synchronous redis-like double: get/set over a shared dict.

    Stands in for a real synchronous redis client (redis.Redis) in tests where
    fakeredis is not installed.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: Any) -> None:
        self.store[key] = value if isinstance(value, str) else str(value)


@pytest.fixture
def slo() -> SLODefinition:
    return SLODefinition(name="api_success", target=0.99, window_hours=24, tenant_id="t1")


def test_in_memory_tracker_still_works_without_backend(slo: SLODefinition) -> None:
    tracker = SLOTracker()
    tracker.record_event(True, slo)
    tracker.record_event(False, slo)
    status = tracker.burn_rate(slo)
    assert status.total_events == 2
    assert status.successful_events == 1


def test_state_survives_a_fresh_instance_via_shared_backend(slo: SLODefinition) -> None:
    backend = _InMemoryRedisDouble()

    writer = SLOTracker(redis=backend)
    writer.record_event(True, slo)
    writer.record_event(True, slo)
    writer.record_event(False, slo)

    # Simulate a process restart: a brand-new instance, same backend, empty memory.
    reader = SLOTracker(redis=backend)
    status = reader.burn_rate(slo)

    assert status.total_events == 3
    assert status.successful_events == 2
    # And it shows up in the cross-instance summary too.
    summary = reader.summary(tenant_id="t1")
    assert any(row["slo_name"] == "api_success" and row["total_events"] == 3 for row in summary)


def test_backend_actually_holds_serializable_state(slo: SLODefinition) -> None:
    backend = _InMemoryRedisDouble()
    tracker = SLOTracker(redis=backend)
    tracker.record_event(True, slo)

    # Something was written, and it round-trips through JSON.
    assert backend.store, "expected the tracker to persist to the backend"
    for raw in backend.store.values():
        json.loads(raw)  # must not raise


def test_persistence_failure_does_not_break_recording(slo: SLODefinition) -> None:
    class _BrokenRedis:
        def get(self, key: str) -> str | None:
            raise RuntimeError("redis down")

        def set(self, key: str, value: Any) -> None:
            raise RuntimeError("redis down")

    tracker = SLOTracker(redis=_BrokenRedis())
    # Must not raise even though the backend is unusable; falls back to in-memory.
    tracker.record_event(True, slo)
    status = tracker.burn_rate(slo)
    assert status.total_events == 1
