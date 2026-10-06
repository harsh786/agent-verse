"""Worker goal-task governance wiring fails toward the restrictive side.

* a03-F062-04: the per-goal cost controller is Redis-backed whenever the worker
  can reach Redis — through ``REDIS_URL`` *or* the Celery broker URL — and a
  construction problem is logged, not swallowed.
* a03-F055-07: grant enforcement is never left off because the compliance
  ceiling lookup (or anything after it) raised.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.governance.cost import CostController, RedisCostController
from app.scaling import tasks


class _Redis:
    pass


def test_cost_controller_uses_the_broker_redis_without_redis_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "redis://broker:6379/0")
    seen: list[str] = []

    import redis.asyncio as aioredis

    def _from_url(url: str, **_kw: Any) -> _Redis:
        seen.append(url)
        return _Redis()

    monkeypatch.setattr(aioredis, "from_url", _from_url)
    cost = tasks._worker_cost_controller(None)
    assert isinstance(cost, RedisCostController)
    assert seen == ["redis://broker:6379/0"]


def test_cost_controller_without_any_redis_is_in_process_and_reads_budget_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tasks, "_worker_async_redis", lambda: None)
    db = object()
    calls: list[Any] = []
    monkeypatch.setattr(CostController, "set_budget_db", lambda self, f: calls.append(f))
    cost = tasks._worker_cost_controller(db)
    assert type(cost) is CostController
    assert calls == [db]


def test_grants_stay_enforced_when_the_ceiling_lookup_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: Any) -> str:
        raise RuntimeError("ceiling lookup exploded")

    monkeypatch.setattr(tasks, "_worker_compliance_ceiling", _boom)
    mode, store, enforce = tasks._worker_grant_governance("fully-autonomous", object(), "t-1")
    assert enforce is True
    assert store is None  # under enforcement: every tool call is denied
    assert mode == "supervised"


def test_grant_governance_happy_path_clamps_and_builds_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tasks, "_worker_compliance_ceiling", lambda *_a: "bounded-autonomous")
    mode, store, enforce = tasks._worker_grant_governance("fully-autonomous", object(), "t-1")
    assert mode == "bounded-autonomous"
    assert store is not None
    assert enforce is True
