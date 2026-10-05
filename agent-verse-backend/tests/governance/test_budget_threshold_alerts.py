"""COST-03: configured budget alert thresholds fire — once, fleet-wide, on every path.

``alert_pct_thresholds`` was persisted and echoed but never evaluated; the only
alert was a log line at a hard-coded 79-81% band, and the production Lua path
evaluated none at all. Now each configured threshold that a charge crosses (for
the tenant's daily budget and for a per-agent daily cap) fires exactly once per
UTC day across replicas (Redis SET NX) and is delivered to the alert sink (the
tenant's notification channels by default).
"""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import pytest

from app.governance import cost as cost_mod
from app.governance.cost import BudgetConfig, CostController, RedisCostController
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t-alerts", plan=PlanTier.ENTERPRISE, api_key_id="k")
CFG = BudgetConfig(
    per_goal_usd=1000.0,
    per_tenant_daily_usd=100.0,
    per_agent_daily_usd={"agent-1": 10.0},
    alert_pct_thresholds=(50, 75, 90),
)


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def _sink(db: Any, alert: dict[str, Any]) -> None:
        sent.append(alert)

    monkeypatch.setattr(cost_mod, "_deliver_budget_alert", _sink)
    return sent


def _pcts(sent: list[dict[str, Any]], scope: str) -> list[int]:
    return [a["threshold_pct"] for a in sent if a["scope"] == scope]


class _NoLuaRedis(fakeredis.aioredis.FakeRedis):
    """fakeredis without Lua (no lupa here): the controller's non-Lua path."""

    register_script = None  # type: ignore[assignment]


def _fake_redis() -> Any:
    return _NoLuaRedis(decode_responses=True)


async def _redis_controller(redis: Any = None) -> RedisCostController:
    ctrl = RedisCostController(redis if redis is not None else _fake_redis())
    ctrl._tenant_configs[T.tenant_id] = CFG
    return ctrl


async def test_each_crossed_threshold_fires_once(alerts: list[dict[str, Any]]) -> None:
    ctrl = await _redis_controller()
    for _ in range(4):  # 40, 80 (crosses 50 and 75), ... per call of 20
        assert await ctrl.check_and_record_async(goal_id="g", cost_usd=20.0, tenant_ctx=T)
    # 20 -> 40 -> 60 -> 80: crossed 50 and 75 once each, never 90.
    assert _pcts(alerts, "tenant_daily") == [50, 75]
    assert await ctrl.check_and_record_async(goal_id="g", cost_usd=11.0, tenant_ctx=T)
    assert _pcts(alerts, "tenant_daily") == [50, 75, 90]


async def test_a_second_replica_does_not_repeat_an_alert(alerts: list[dict[str, Any]]) -> None:
    redis = _fake_redis()
    a, b = RedisCostController(redis), RedisCostController(redis)
    a._tenant_configs[T.tenant_id] = CFG
    b._tenant_configs[T.tenant_id] = CFG
    assert await a.check_and_record_async(goal_id="g", cost_usd=49.0, tenant_ctx=T)
    assert await b.check_and_record_async(goal_id="g2", cost_usd=2.0, tenant_ctx=T)
    assert await a.check_and_record_async(goal_id="g", cost_usd=2.0, tenant_ctx=T)
    assert _pcts(alerts, "tenant_daily") == [50]


async def test_agent_daily_thresholds_fire(alerts: list[dict[str, Any]]) -> None:
    ctrl = await _redis_controller()
    assert await ctrl.check_and_record_async(
        goal_id="g", cost_usd=8.0, tenant_ctx=T, agent_id="agent-1"
    )
    assert _pcts(alerts, "agent_daily") == [50, 75]
    assert alerts[-1]["agent_id"] == "agent-1"


async def test_in_memory_controller_uses_configured_thresholds(
    alerts: list[dict[str, Any]],
) -> None:
    ctrl = CostController(CFG)
    assert await ctrl.check_and_record(goal_id="g", cost_usd=60.0, tenant_ctx=T)
    assert _pcts(alerts, "tenant_daily") == [50]


async def test_alert_delivery_failure_never_blocks_the_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _down(db: Any, alert: dict[str, Any]) -> None:
        raise ConnectionError("webhook down")

    monkeypatch.setattr(cost_mod, "_deliver_budget_alert", _down)
    ctrl = await _redis_controller()
    assert await ctrl.check_and_record_async(goal_id="g", cost_usd=60.0, tenant_ctx=T)


@pytest.mark.integration
async def test_lua_path_fires_thresholds_on_real_redis(
    redis_url: str, alerts: list[dict[str, Any]]
) -> None:
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await client.flushdb()
        a, b = await _redis_controller(client), await _redis_controller(client)
        assert await a.check_and_record_async(goal_id="g", cost_usd=60.0, tenant_ctx=T)
        assert await b.check_and_record_async(goal_id="g2", cost_usd=20.0, tenant_ctx=T)
        assert await a.check_and_record_async(
            goal_id="g", cost_usd=8.0, tenant_ctx=T, agent_id="agent-1"
        )
        assert _pcts(alerts, "tenant_daily") == [50, 75]
        assert _pcts(alerts, "agent_daily") == [50, 75]
    finally:
        await client.aclose()
