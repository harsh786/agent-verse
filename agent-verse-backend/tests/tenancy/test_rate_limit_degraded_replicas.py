"""RATE-03: degraded (Redis-down) rate limiting stays within the plan limit cluster-wide.

During a Redis outage each replica enforced the limit locally, so N replicas
allowed N x the plan limit. The per-pod budget is now the plan limit divided by
the configured replica count (``RATE_LIMIT_REPLICA_COUNT``), every degraded
decision is counted on a metric for alerting, and the fallback table is
bounded (it was an unbounded per-tenant dict).
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.tenancy import middleware
from app.tenancy.middleware import _check_rate_limit_with_fallback, _fallback_counters


@pytest.fixture(autouse=True)
def _clean() -> None:
    _fallback_counters.clear()


async def _allowed(tenant: str, rpm: int, n: int) -> int:
    return sum(
        [await _check_rate_limit_with_fallback(tenant, None, rpm_limit=rpm) for _ in range(n)]
    )


async def test_per_pod_budget_is_the_plan_limit_divided_by_replicas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_replica_count", 4)
    assert await _allowed("t-4", rpm=60, n=100) == 15  # 4 pods x 15 = the plan's 60/min


async def test_single_replica_keeps_the_plan_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_replica_count", 1)
    assert await _allowed("t-1", rpm=60, n=100) == 60


async def test_never_below_one_request_per_pod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_replica_count", 50)
    assert await _allowed("t-50", rpm=10, n=5) == 1


async def test_degraded_decisions_are_counted_for_alerting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.observability.metrics import RATE_LIMIT_DEGRADED_TOTAL

    monkeypatch.setattr(get_settings(), "rate_limit_replica_count", 2)
    before_allowed = RATE_LIMIT_DEGRADED_TOTAL.labels(decision="allowed")._value.get()
    before_denied = RATE_LIMIT_DEGRADED_TOTAL.labels(decision="denied")._value.get()
    await _allowed("t-m", rpm=4, n=5)  # budget 2: 2 allowed, 3 denied
    assert RATE_LIMIT_DEGRADED_TOTAL.labels(decision="allowed")._value.get() - before_allowed == 2
    assert RATE_LIMIT_DEGRADED_TOTAL.labels(decision="denied")._value.get() - before_denied == 3


async def test_fallback_table_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(middleware, "_FALLBACK_MAX_TENANTS", 100)
    for i in range(500):
        await _check_rate_limit_with_fallback(f"t-{i}", None, rpm_limit=60)
    assert len(_fallback_counters) <= 100
