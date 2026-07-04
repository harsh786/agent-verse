"""Regression tests for C4 — atomic cost control that cannot regress."""
import pytest
from unittest.mock import AsyncMock
from app.governance.cost import RedisCostController, BudgetConfig
from app.tenancy.context import TenantContext, PlanTier

T = TenantContext(tenant_id="cost-t1", plan=PlanTier.ENTERPRISE, api_key_id="k1", roles=())


def _make_fake_redis(initial_goal=0.0, initial_daily=0.0):
    """Fake Redis that simulates INCRBYFLOAT with get/set."""
    store = {}

    async def get(key):
        return str(store.get(key, 0.0)).encode()

    async def incrbyfloat(key, amount):
        current = float(store.get(key, 0.0))
        new = current + amount
        store[key] = new
        return new

    async def expire(key, ttl):
        pass

    async def expireat(key, ts):
        pass

    async def exists(key):
        return 1 if key in store else 0

    async def set(key, value, ex=None):
        store[key] = value

    redis = AsyncMock()
    redis.get.side_effect = get
    redis.incrbyfloat.side_effect = incrbyfloat
    redis.expire.side_effect = expire
    redis.expireat.side_effect = expireat
    redis.exists.side_effect = exists
    redis.set.side_effect = set
    redis.register_script = None  # forces fallback non-Lua path
    redis._store = store
    return redis


class TestRedisCostControllerAtomicFix:

    @pytest.mark.asyncio
    async def test_denied_request_does_not_consume_budget(self):
        """C4: A budget-denied request must NOT increment the counter."""
        fake_redis = _make_fake_redis()
        ctrl = RedisCostController(redis=fake_redis)
        ctrl.configure_tenant_budget(
            T.tenant_id,
            BudgetConfig(per_goal_usd=1.00, per_tenant_daily_usd=500.0)
        )

        # First call: $0.90 — should succeed
        r1 = await ctrl.check_and_record(goal_id="g1", cost_usd=0.90, tenant_ctx=T)
        assert r1 is True

        # Second call: $0.20 — would exceed $1.00 goal limit, should be denied
        r2 = await ctrl.check_and_record(goal_id="g1", cost_usd=0.20, tenant_ctx=T)
        assert r2 is False, "Over-budget request should be denied"

        # CRITICAL: budget consumed must still be $0.90, not $1.10
        status = await ctrl.get_budget_status(goal_id="g1", tenant_ctx=T)
        assert status["goal_spent"] == pytest.approx(0.90, abs=0.01), \
            f"Denied request consumed budget! Expected ~0.90, got {status['goal_spent']}"

    @pytest.mark.asyncio
    async def test_concurrent_requests_cannot_exceed_budget(self):
        """C4: Concurrent requests must not collectively exceed budget limit."""
        import asyncio
        fake_redis = _make_fake_redis()
        ctrl = RedisCostController(redis=fake_redis)
        ctrl.configure_tenant_budget(
            T.tenant_id,
            BudgetConfig(per_goal_usd=1.00, per_tenant_daily_usd=500.0)
        )

        # 5 concurrent requests of $0.30 each = $1.50 total (only $1.00 budget)
        results = await asyncio.gather(*[
            ctrl.check_and_record(goal_id="g1", cost_usd=0.30, tenant_ctx=T)
            for _ in range(5)
        ])

        approved = sum(1 for r in results if r is True)
        # At most 3 can be approved ($0.90 ≤ $1.00 < $1.20)
        assert approved <= 4, f"Too many approved: {approved}"

        # Total recorded cost must not exceed budget
        status = await ctrl.get_budget_status(goal_id="g1", tenant_ctx=T)
        assert status["goal_spent"] <= 1.01, \
            f"Budget overrun! Spent: {status['goal_spent']}"

    @pytest.mark.asyncio
    async def test_refund_reduces_counter(self):
        """C4: refund_async must reduce the cost counter."""
        fake_redis = _make_fake_redis()
        ctrl = RedisCostController(redis=fake_redis)
        ctrl.configure_tenant_budget(
            T.tenant_id, BudgetConfig(per_goal_usd=10.0, per_tenant_daily_usd=500.0)
        )

        # Charge $1.00
        await ctrl.check_and_record(goal_id="g1", cost_usd=1.00, tenant_ctx=T)
        status_before = await ctrl.get_budget_status(goal_id="g1", tenant_ctx=T)
        assert status_before["goal_spent"] == pytest.approx(1.00, abs=0.01)

        # Refund $0.50 (tool call failed)
        await ctrl.refund_async(goal_id="g1", cost_usd=0.50, tenant_ctx=T, reason="tool_failed")

        status_after = await ctrl.get_budget_status(goal_id="g1", tenant_ctx=T)
        assert status_after["goal_spent"] == pytest.approx(0.50, abs=0.01)

    @pytest.mark.asyncio
    async def test_idempotency_prevents_double_charge(self):
        """C4: Same (goal_id, attempt_id) charged twice must only debit once."""
        fake_redis = _make_fake_redis()
        ctrl = RedisCostController(redis=fake_redis)
        ctrl.configure_tenant_budget(
            T.tenant_id, BudgetConfig(per_goal_usd=10.0, per_tenant_daily_usd=500.0)
        )

        attempt = "attempt-abc-001"
        # First charge
        r1 = await ctrl.check_and_record(
            goal_id="g1", cost_usd=0.50, tenant_ctx=T, attempt_id=attempt
        )
        assert r1 is True

        # Retry with same attempt_id (Celery redelivery)
        r2 = await ctrl.check_and_record(
            goal_id="g1", cost_usd=0.50, tenant_ctx=T, attempt_id=attempt
        )
        assert r2 is True  # not blocked

        # But only charged once
        status = await ctrl.get_budget_status(goal_id="g1", tenant_ctx=T)
        assert status["goal_spent"] == pytest.approx(0.50, abs=0.01), \
            f"Double-charged! Expected 0.50, got {status['goal_spent']}"

    @pytest.mark.asyncio
    async def test_daily_budget_not_exceeded(self):
        """C4: Daily budget limit enforced without double-counting."""
        fake_redis = _make_fake_redis()
        ctrl = RedisCostController(redis=fake_redis)
        ctrl.configure_tenant_budget(
            T.tenant_id,
            BudgetConfig(per_goal_usd=100.0, per_tenant_daily_usd=1.00)
        )

        # $0.70 first goal — OK
        r1 = await ctrl.check_and_record(goal_id="g1", cost_usd=0.70, tenant_ctx=T)
        assert r1 is True

        # $0.40 second goal — would exceed $1.00 daily
        r2 = await ctrl.check_and_record(goal_id="g2", cost_usd=0.40, tenant_ctx=T)
        assert r2 is False, "Daily budget not enforced"

        # Daily total must be $0.70, not $1.10
        status = await ctrl.get_budget_status(goal_id="g2", tenant_ctx=T)
        # Check via redis store that daily counter is 0.70
        daily_key = ctrl._daily_key(T.tenant_id)
        daily_stored = float(fake_redis._store.get(daily_key, 0))
        assert daily_stored == pytest.approx(0.70, abs=0.01), \
            f"Daily counter inflated by denied request: {daily_stored}"
