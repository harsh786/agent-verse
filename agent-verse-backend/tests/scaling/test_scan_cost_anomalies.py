"""a10-F246-01/02/03/06: the hourly ``scan_cost_anomalies`` maintenance task.

It used the blocking ``KEYS cost:daily:*``, scanned an arbitrary 50 tenants
while reporting every tenant as scanned, swallowed per-tenant errors, only
counted anomalies (nothing raised or delivered), returned its own failures as a
SUCCESS result, and closed Redis only on the success path.

Uses fakeredis (in-process) — never the live Redis.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest

from app.scaling import tasks

_DAY = "2026-10-07"


def _ewma(mean: float, var: float) -> str:
    return json.dumps({"mean": mean, "var": var})


async def _seed(r: Any) -> None:
    for tid in ("t-hot", "t-calm", "t-idle"):
        await r.set(f"cost:daily:{tid}:{_DAY}", "1.5")
    await r.set("cost_ewma:t-hot:agent-7", _ewma(4.0, 16.0))  # sustained high + noisy
    await r.set("cost_ewma:t-calm:tenant", _ewma(0.02, 0.0001))
    await r.set("cost_ewma:t-other:tenant", _ewma(9.0, 81.0))  # no recent activity


class _Recorder:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.commands: list[str] = []

    def __getattr__(self, name: str) -> Any:
        self.commands.append(name)
        return getattr(self._inner, name)


async def test_scan_is_non_blocking_raises_each_anomaly_once_and_reports_honestly() -> None:
    r = fakeredis.FakeAsyncRedis()
    await _seed(r)
    rec = _Recorder(r)
    pubsub = r.pubsub()
    await pubsub.subscribe("cost:anomaly:t-hot")
    notify = AsyncMock(return_value=1)
    with patch(
        "app.services.notification_service.NotificationService.notify_cost_anomaly", notify
    ):
        first = await tasks._scan_cost_anomalies_async(rec, db_factory=object())
        second = await tasks._scan_cost_anomalies_async(rec, db_factory=object())

    assert "keys" not in rec.commands  # KEYS blocks Redis on a large keyspace
    assert first == {
        "tenants_found": 3,
        "tenants_scanned": 3,
        "tenants_skipped": 0,
        "anomalies_found": 1,
        "alerts_raised": 1,
    }
    assert second["anomalies_found"] == 1 and second["alerts_raised"] == 0  # once per day
    notify.assert_awaited_once()
    alert = notify.await_args.args[0]
    assert alert["tenant_id"] == "t-hot" and alert["agent_id"] == "agent-7"
    assert alert["anomaly_type"] == "sustained_high"
    msg = None
    for _ in range(5):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if msg:
            break
    assert msg is not None and json.loads(msg["data"])["tenant_id"] == "t-hot"
    await pubsub.aclose()
    await r.aclose()


async def test_tenant_cap_is_reported_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    r = fakeredis.FakeAsyncRedis()
    await _seed(r)
    monkeypatch.setattr(tasks, "_COST_ANOMALY_MAX_TENANTS", 2)
    out = await tasks._scan_cost_anomalies_async(r, db_factory=None)
    assert out["tenants_found"] == 3
    assert out["tenants_scanned"] == 2
    assert out["tenants_skipped"] == 1
    await r.aclose()


def test_redis_failure_fails_the_task_and_closes_the_client() -> None:
    client = AsyncMock()
    client.scan_iter = lambda **_: _boom()
    with (
        patch("redis.asyncio.from_url", return_value=client),
        patch("app.db.session.get_session_factory", return_value=None),
        pytest.raises(ConnectionError),
    ):
        tasks.scan_cost_anomalies.run()
    client.aclose.assert_awaited_once()


async def _boom() -> Any:
    raise ConnectionError("redis down")
    yield  # pragma: no cover - makes this an async generator
