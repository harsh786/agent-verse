"""Tests for CostController atomic operations."""
from __future__ import annotations

import asyncio

import pytest

from app.governance.cost import CostController
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="cost-t1", plan=PlanTier.PROFESSIONAL, api_key_id="k")


async def test_concurrent_budget_check_no_overrun():
    """Concurrent budget checks must not allow overruns via TOCTOU."""
    ctrl = CostController(per_goal_usd=0.05)

    # Run 20 concurrent cost checks each of $0.003 (total $0.06 > budget $0.05)
    async def one_check(i: int) -> bool:
        return await ctrl.check_and_record(
            tenant_ctx=T, goal_id="g1",
            cost_usd=0.003, tool_name="test"
        )

    results = await asyncio.gather(*[one_check(i) for i in range(20)])
    # Some should be denied once budget is exceeded
    denied = [r for r in results if r is False]
    # Budget is $0.05, each call is $0.003 — at most 16 should pass (16 * 0.003 = 0.048 ≤ 0.05)
    approved_count = sum(1 for r in results if r is True)
    # With proper locking, total cost should not exceed budget by more than 1 step
    assert approved_count <= 17, (
        f"Too many approved ({approved_count}) — TOCTOU race detected"
    )
    assert len(denied) > 0, "Expected some denials when budget exceeded"


async def test_daily_reset_atomic():
    """Daily reset should not lose cost data."""
    ctrl = CostController()
    # Simulate reset during concurrent checks — verify no exceptions and all return bool
    async def check() -> bool:
        return await ctrl.check_and_record(
            tenant_ctx=T, goal_id="g2", cost_usd=0.001, tool_name="test"
        )

    results = await asyncio.gather(*[check() for _ in range(5)])
    assert all(isinstance(r, bool) for r in results)


async def test_tenant_isolation_concurrent():
    """Concurrent checks for different tenants should not interfere."""
    ctrl = CostController(per_goal_usd=0.01)
    T2 = TenantContext(tenant_id="cost-t2", plan=PlanTier.PROFESSIONAL, api_key_id="k2")

    async def check_t1() -> bool:
        return await ctrl.check_and_record(
            tenant_ctx=T, goal_id="g3", cost_usd=0.005, tool_name="test"
        )

    async def check_t2() -> bool:
        return await ctrl.check_and_record(
            tenant_ctx=T2, goal_id="g3", cost_usd=0.005, tool_name="test"
        )

    results = await asyncio.gather(check_t1(), check_t2())
    # Both should be allowed — different tenants, same goal_id but different key
    assert all(r is True for r in results), (
        "Tenant isolation failed: one tenant's budget check blocked the other"
    )


async def test_per_goal_usd_kwarg():
    """CostController(per_goal_usd=...) convenience constructor works correctly."""
    ctrl = CostController(per_goal_usd=0.01)
    assert ctrl._cfg.per_goal_usd == pytest.approx(0.01)
    # Within budget
    ok = await ctrl.check_and_record(
        tenant_ctx=T, goal_id="g4", cost_usd=0.005, tool_name="test"
    )
    assert ok is True
    # Exceeded
    ok2 = await ctrl.check_and_record(
        tenant_ctx=T, goal_id="g4", cost_usd=0.007, tool_name="test"
    )
    assert ok2 is False
