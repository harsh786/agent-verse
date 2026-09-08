"""Per-goal cost breakdown must optionally survive process restart.

D-21c: the cost breakdown is read back after a goal runs via GET /goals/{id}/cost-metrics
(app.observability.cost_breakdown_api -> get_breakdown). Production never calls
finalize_breakdown, so the breakdown is meant to survive for later retrieval — but the
default in-memory registry loses it on restart. With an optional Redis-like backend the
breakdown is persisted so a fresh process can still serve it.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.observability import cost_breakdown as cb


class _InMemoryRedisDouble:
    """Minimal synchronous redis-like double: get/set/delete over a shared dict."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: Any) -> None:
        self.store[key] = value if isinstance(value, str) else str(value)

    def delete(self, key: str) -> None:
        self.store.pop(key, None)


@pytest.fixture(autouse=True)
def _reset_backend() -> Any:
    # Ensure other tests / the in-memory default are unaffected.
    cb.reset_persistence()
    cb._goal_breakdowns.clear()
    yield
    cb.reset_persistence()
    cb._goal_breakdowns.clear()


def test_in_memory_path_is_default_and_unchanged() -> None:
    cb.record_role_cost("g_mem", "planner", "gpt-5.2", 100, 20, 0.01)
    cb.record_role_cost("g_mem", "planner", "gpt-5.2", 50, 10, 0.005)
    bd = cb.get_breakdown("g_mem").to_dict()
    assert bd["goal_id"] == "g_mem"
    assert bd["roles"][0]["calls"] == 2
    assert bd["roles"][0]["input_tokens"] == 150
    assert bd["total_cost_usd"] == pytest.approx(0.015)


def test_breakdown_survives_a_restart_via_shared_backend() -> None:
    backend = _InMemoryRedisDouble()
    cb.configure_persistence(backend)

    cb.record_role_cost("g_persist", "planner", "gpt-5.2", 100, 20, 0.01)
    cb.record_role_cost("g_persist", "executor", "gpt-4o-mini", 200, 40, 0.002)

    # Simulate a restart: drop the in-process registry entirely; only the backend remains.
    cb._goal_breakdowns.clear()

    bd = cb.get_breakdown("g_persist").to_dict()
    roles = {r["role"]: r for r in bd["roles"]}
    assert set(roles) == {"planner", "executor"}
    assert roles["planner"]["input_tokens"] == 100
    assert roles["executor"]["output_tokens"] == 40
    assert bd["total_cost_usd"] == pytest.approx(0.012)


def test_accumulation_persists_across_records() -> None:
    backend = _InMemoryRedisDouble()
    cb.configure_persistence(backend)
    cb.record_role_cost("g_acc", "planner", "gpt-5.2", 10, 2, 0.001)
    cb._goal_breakdowns.clear()  # force a reload between records
    cb.record_role_cost("g_acc", "planner", "gpt-5.2", 30, 6, 0.003)

    role = cb.get_breakdown("g_acc").to_dict()["roles"][0]
    assert role["calls"] == 2
    assert role["input_tokens"] == 40
    assert role["output_tokens"] == 8


def test_finalize_removes_from_backend() -> None:
    backend = _InMemoryRedisDouble()
    cb.configure_persistence(backend)
    cb.record_role_cost("g_fin", "planner", "gpt-5.2", 10, 2, 0.001)

    result = cb.finalize_breakdown("g_fin")
    assert result["goal_id"] == "g_fin"
    assert result["roles"][0]["input_tokens"] == 10

    # Gone from the backend; a fresh read yields an empty breakdown.
    assert not any("g_fin" in k for k in backend.store)
    assert cb.get_breakdown("g_fin").to_dict()["roles"] == []


def test_backend_holds_serializable_state() -> None:
    backend = _InMemoryRedisDouble()
    cb.configure_persistence(backend)
    cb.record_role_cost("g_json", "planner", "gpt-5.2", 10, 2, 0.001)
    assert backend.store
    for raw in backend.store.values():
        json.loads(raw)


def test_persistence_failure_falls_back_gracefully() -> None:
    class _BrokenRedis:
        def get(self, key: str) -> str | None:
            raise RuntimeError("redis down")

        def set(self, key: str, value: Any) -> None:
            raise RuntimeError("redis down")

        def delete(self, key: str) -> None:
            raise RuntimeError("redis down")

    cb.configure_persistence(_BrokenRedis())
    # Must not raise; degrades to in-memory.
    cb.record_role_cost("g_broken", "planner", "gpt-5.2", 10, 2, 0.001)
    bd = cb.get_breakdown("g_broken").to_dict()
    assert bd["roles"][0]["input_tokens"] == 10
