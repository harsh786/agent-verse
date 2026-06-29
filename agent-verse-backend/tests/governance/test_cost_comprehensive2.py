"""Comprehensive tests for app/governance/cost.py — targeting 90%+ coverage."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.governance.cost import BudgetConfig, CostController, RedisCostController
from app.tenancy.context import TenantContext, PlanTier


def _ctx(tenant_id: str = "t1") -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="k1")


# ── BudgetConfig ──────────────────────────────────────────────────────────────

class TestBudgetConfig:
    def test_defaults(self) -> None:
        cfg = BudgetConfig()
        assert cfg.per_goal_usd == 10.0
        assert cfg.per_tenant_daily_usd == 500.0

    def test_custom_values(self) -> None:
        cfg = BudgetConfig(per_goal_usd=5.0, per_tenant_daily_usd=100.0)
        assert cfg.per_goal_usd == 5.0
        assert cfg.per_tenant_daily_usd == 100.0

    def test_frozen(self) -> None:
        cfg = BudgetConfig()
        with pytest.raises((AttributeError, TypeError)):
            cfg.per_goal_usd = 1.0  # type: ignore[misc]


# ── CostController ────────────────────────────────────────────────────────────

class TestCostController:
    async def test_within_budget_returns_true(self) -> None:
        ctrl = CostController(per_goal_usd=10.0, per_tenant_daily_usd=500.0)
        ctx = _ctx()
        ok = await ctrl.check_and_record(goal_id="g1", cost_usd=1.0, tenant_ctx=ctx)
        assert ok is True

    async def test_exceeds_goal_budget_returns_false(self) -> None:
        ctrl = CostController(per_goal_usd=5.0, per_tenant_daily_usd=500.0)
        ctx = _ctx()
        await ctrl.check_and_record(goal_id="g1", cost_usd=4.0, tenant_ctx=ctx)
        ok = await ctrl.check_and_record(goal_id="g1", cost_usd=2.0, tenant_ctx=ctx)
        assert ok is False

    async def test_exceeds_daily_budget_returns_false(self) -> None:
        ctrl = CostController(per_goal_usd=1000.0, per_tenant_daily_usd=5.0)
        ctx = _ctx()
        await ctrl.check_and_record(goal_id="g1", cost_usd=4.0, tenant_ctx=ctx)
        ok = await ctrl.check_and_record(goal_id="g1", cost_usd=2.0, tenant_ctx=ctx)
        assert ok is False

    async def test_goal_total_accumulates(self) -> None:
        ctrl = CostController()
        ctx = _ctx()
        await ctrl.check_and_record(goal_id="g1", cost_usd=1.5, tenant_ctx=ctx)
        await ctrl.check_and_record(goal_id="g1", cost_usd=2.5, tenant_ctx=ctx)
        assert ctrl.goal_total("g1", tenant_ctx=ctx) == pytest.approx(4.0)

    async def test_daily_total_accumulates(self) -> None:
        ctrl = CostController()
        ctx = _ctx()
        await ctrl.check_and_record(goal_id="g1", cost_usd=1.0, tenant_ctx=ctx)
        await ctrl.check_and_record(goal_id="g2", cost_usd=2.0, tenant_ctx=ctx)
        assert ctrl.daily_total(tenant_ctx=ctx) == pytest.approx(3.0)

    async def test_goal_total_isolated_by_tenant(self) -> None:
        ctrl = CostController()
        ctx1 = _ctx("t1")
        ctx2 = _ctx("t2")
        await ctrl.check_and_record(goal_id="g1", cost_usd=5.0, tenant_ctx=ctx1)
        await ctrl.check_and_record(goal_id="g1", cost_usd=2.0, tenant_ctx=ctx2)
        assert ctrl.goal_total("g1", tenant_ctx=ctx1) == pytest.approx(5.0)
        assert ctrl.goal_total("g1", tenant_ctx=ctx2) == pytest.approx(2.0)

    async def test_goal_total_zero_for_unknown_goal(self) -> None:
        ctrl = CostController()
        assert ctrl.goal_total("nonexistent", tenant_ctx=_ctx()) == 0.0

    async def test_daily_total_zero_for_new_tenant(self) -> None:
        ctrl = CostController()
        assert ctrl.daily_total(tenant_ctx=_ctx("brand_new")) == 0.0

    async def test_get_tenant_cost_today(self) -> None:
        ctrl = CostController()
        ctx = _ctx()
        await ctrl.check_and_record(goal_id="g1", cost_usd=3.0, tenant_ctx=ctx)
        assert ctrl.get_tenant_cost_today(ctx) == pytest.approx(3.0)

    async def test_reset_if_new_day_clears_daily(self) -> None:
        ctrl = CostController()
        ctx = _ctx()
        await ctrl.check_and_record(goal_id="g1", cost_usd=50.0, tenant_ctx=ctx)
        # Manually set date to yesterday to trigger reset
        ctrl._last_reset_date["t1"] = "2000-01-01"
        ctrl._reset_if_new_day("t1")
        assert ctrl.daily_total(tenant_ctx=ctx) == 0.0

    async def test_exact_budget_is_allowed(self) -> None:
        ctrl = CostController(per_goal_usd=10.0, per_tenant_daily_usd=500.0)
        ctx = _ctx()
        ok = await ctrl.check_and_record(goal_id="g1", cost_usd=10.0, tenant_ctx=ctx)
        assert ok is True

    async def test_record_cost_metric_called(self) -> None:
        ctrl = CostController()
        ctx = _ctx()
        with patch("app.governance.cost.record_cost_usd") as mock_metric:
            await ctrl.check_and_record(goal_id="g1", cost_usd=1.0, tenant_ctx=ctx)
        mock_metric.assert_called_once_with(scope="tool", amount=1.0)

    async def test_record_metric_not_called_when_blocked(self) -> None:
        ctrl = CostController(per_goal_usd=0.5)
        ctx = _ctx()
        with patch("app.governance.cost.record_cost_usd") as mock_metric:
            await ctrl.check_and_record(goal_id="g1", cost_usd=1.0, tenant_ctx=ctx)
        mock_metric.assert_not_called()

    async def test_config_object_overrides_keyword_args(self) -> None:
        cfg = BudgetConfig(per_goal_usd=2.0, per_tenant_daily_usd=50.0)
        ctrl = CostController(config=cfg)
        ctx = _ctx()
        ok = await ctrl.check_and_record(goal_id="g1", cost_usd=3.0, tenant_ctx=ctx)
        assert ok is False  # 3.0 > 2.0


# ── RedisCostController ───────────────────────────────────────────────────────

class TestRedisCostController:
    def _make_redis(self, goal_val: float = 0.0, daily_val: float = 0.0) -> AsyncMock:
        mock = AsyncMock()
        mock.incrbyfloat = AsyncMock(side_effect=[goal_val, daily_val])
        mock.expire = AsyncMock()
        mock.get = AsyncMock(return_value=str(daily_val))
        return mock

    async def test_check_and_record_within_budget(self) -> None:
        mock_redis = self._make_redis(1.0, 1.0)
        ctrl = RedisCostController(
            mock_redis,
            {"t1": BudgetConfig(per_goal_usd=10.0, per_tenant_daily_usd=100.0)},
        )
        ctx = _ctx("t1")
        ok = await ctrl.check_and_record(tenant_ctx=ctx, goal_id="g1", cost_usd=1.0)
        assert ok is True

    async def test_check_and_record_exceeds_goal_budget(self) -> None:
        mock_redis = self._make_redis(goal_val=11.0, daily_val=11.0)
        ctrl = RedisCostController(
            mock_redis,
            {"t1": BudgetConfig(per_goal_usd=10.0)},
        )
        ctx = _ctx("t1")
        ok = await ctrl.check_and_record(tenant_ctx=ctx, goal_id="g1", cost_usd=1.0)
        assert ok is False

    async def test_check_and_record_exceeds_daily_budget(self) -> None:
        mock_redis = self._make_redis(goal_val=1.0, daily_val=600.0)
        ctrl = RedisCostController(
            mock_redis,
            {"t1": BudgetConfig(per_goal_usd=1000.0, per_tenant_daily_usd=500.0)},
        )
        ctx = _ctx("t1")
        ok = await ctrl.check_and_record(tenant_ctx=ctx, goal_id="g1", cost_usd=1.0)
        assert ok is False

    async def test_redis_error_fails_open_in_dev(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.incrbyfloat.side_effect = ConnectionError("Redis down")
        ctrl = RedisCostController(mock_redis)
        ctx = _ctx()
        with patch.dict("os.environ", {"ENVIRONMENT": "development"}):
            ok = await ctrl.check_and_record(tenant_ctx=ctx, goal_id="g1", cost_usd=1.0)
        assert ok is True  # fail-open in dev

    async def test_redis_error_fails_closed_in_prod(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.incrbyfloat.side_effect = ConnectionError("Redis down")
        ctrl = RedisCostController(mock_redis)
        ctx = _ctx()
        with patch.dict("os.environ", {"ENVIRONMENT": "production"}):
            ok = await ctrl.check_and_record(tenant_ctx=ctx, goal_id="g1", cost_usd=1.0)
        assert ok is False  # fail-closed in prod

    async def test_get_tenant_cost_today_returns_float(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="42.5")
        ctrl = RedisCostController(mock_redis)
        ctx = _ctx()
        cost = await ctrl.get_tenant_cost_today(ctx)
        assert cost == pytest.approx(42.5)

    async def test_get_tenant_cost_today_no_key_returns_zero(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        ctrl = RedisCostController(mock_redis)
        ctx = _ctx()
        assert await ctrl.get_tenant_cost_today(ctx) == 0.0

    async def test_get_tenant_cost_today_error_returns_zero(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=Exception("error"))
        ctrl = RedisCostController(mock_redis)
        ctx = _ctx()
        assert await ctrl.get_tenant_cost_today(ctx) == 0.0

    async def test_get_budget_status_returns_dict(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="10.0")
        ctrl = RedisCostController(
            mock_redis,
            {"t1": BudgetConfig(per_goal_usd=50.0, per_tenant_daily_usd=100.0)},
        )
        status = await ctrl.get_budget_status("t1", "g1")
        assert status["daily_spent"] == pytest.approx(10.0)
        assert status["daily_limit"] == 100.0
        assert status["daily_remaining"] == pytest.approx(90.0)
        assert "goal_spent" in status

    async def test_get_budget_status_no_goal_id(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        ctrl = RedisCostController(mock_redis)
        status = await ctrl.get_budget_status("t1")
        assert status["goal_spent"] == 0.0

    async def test_configure_tenant_budget(self) -> None:
        ctrl = RedisCostController(AsyncMock())
        cfg = BudgetConfig(per_goal_usd=25.0)
        ctrl.configure_tenant_budget("t1", cfg)
        assert ctrl._tenant_configs["t1"] == cfg

    def test_daily_key_includes_date(self) -> None:
        from datetime import UTC, datetime
        ctrl = RedisCostController(AsyncMock())
        key = ctrl._daily_key("t1")
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        assert "t1" in key
        assert today in key

    def test_goal_key_format(self) -> None:
        ctrl = RedisCostController(AsyncMock())
        key = ctrl._goal_key("goal123", "tenant456")
        assert "goal123" in key
        assert "tenant456" in key

    async def test_get_ttl_to_midnight_positive(self) -> None:
        ctrl = RedisCostController(AsyncMock())
        ttl = await ctrl._get_ttl_to_midnight()
        assert ttl > 0
        assert ttl <= 86400

    async def test_check_and_record_async_alias(self) -> None:
        """check_and_record is an alias for check_and_record_async."""
        mock_redis = self._make_redis(1.0, 1.0)
        ctrl = RedisCostController(
            mock_redis,
            {"t1": BudgetConfig(per_goal_usd=10.0, per_tenant_daily_usd=100.0)},
        )
        ctx = _ctx("t1")
        ok = await ctrl.check_and_record(tenant_ctx=ctx, goal_id="g1", cost_usd=1.0)
        assert ok is True
