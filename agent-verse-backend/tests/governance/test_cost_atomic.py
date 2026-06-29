"""Test atomic cost check-and-increment prevents budget overrun under concurrency."""
from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_cost_controller_atomic_prevents_overrun() -> None:
    """Concurrent requests must not both succeed when combined cost exceeds budget."""
    from app.governance.cost import RedisCostController
    from app.main import _FakeRedis

    fake_redis = _FakeRedis()
    ctrl = RedisCostController(redis=fake_redis)

    # Budget of $1.00, each request costs $0.60 — only one should succeed
    budget = 1.00
    cost_per_req = 0.60
    tenant_id = "tenant-atomic-test"

    results = await asyncio.gather(
        ctrl.try_record_and_check(tenant_id, "g1", cost_per_req, budget),
        ctrl.try_record_and_check(tenant_id, "g2", cost_per_req, budget),
    )

    # Combined $1.20 > $1.00 budget — at most one should succeed
    successes = sum(1 for r in results if r)
    assert successes <= 1, f"Both requests succeeded: {results} — budget overrun!"


@pytest.mark.asyncio
async def test_cost_controller_allows_within_budget() -> None:
    """Sequential requests within budget all succeed."""
    from app.governance.cost import RedisCostController
    from app.main import _FakeRedis

    fake_redis = _FakeRedis()
    ctrl = RedisCostController(redis=fake_redis)

    result1 = await ctrl.try_record_and_check("t1", "g1", 0.50, 1.00)
    assert result1 is True  # Within budget ($0.50)

    result2 = await ctrl.try_record_and_check("t1", "g2", 0.40, 1.00)
    assert result2 is True  # Still within budget ($0.90 total)

    result3 = await ctrl.try_record_and_check("t1", "g3", 0.20, 1.00)
    # $0.90 + $0.20 = $1.10 > $1.00 — should be denied
    assert result3 is False, "Request exceeding budget should be denied"


@pytest.mark.asyncio
async def test_cost_controller_exact_budget_boundary() -> None:
    """Cost equal to budget limit is allowed; one cent over is denied."""
    from app.governance.cost import RedisCostController
    from app.main import _FakeRedis

    fake_redis = _FakeRedis()
    ctrl = RedisCostController(redis=fake_redis)

    # Exactly at budget — should succeed
    result1 = await ctrl.try_record_and_check("t2", "g1", 1.00, 1.00)
    assert result1 is True  # $1.00 == $1.00 budget, not over

    # Any further cost — should be denied
    result2 = await ctrl.try_record_and_check("t2", "g2", 0.01, 1.00)
    assert result2 is False  # $1.00 + $0.01 > $1.00


@pytest.mark.asyncio
async def test_cost_controller_no_redis_always_allows() -> None:
    """With no Redis client, try_record_and_check always returns True (fail-open)."""
    from app.governance.cost import RedisCostController

    ctrl = RedisCostController(redis=None)

    result = await ctrl.try_record_and_check("t3", "g1", 9999.99, 0.01)
    assert result is True  # No Redis → always allows


@pytest.mark.asyncio
async def test_cost_controller_separate_tenants_independent() -> None:
    """Budget counters for separate tenants are independent."""
    from app.governance.cost import RedisCostController
    from app.main import _FakeRedis

    fake_redis = _FakeRedis()
    ctrl = RedisCostController(redis=fake_redis)

    # Both tenants have $1.00 budget
    r1 = await ctrl.try_record_and_check("tenant-a", "g1", 0.90, 1.00)
    r2 = await ctrl.try_record_and_check("tenant-b", "g1", 0.90, 1.00)

    # Each tenant is independent — both should succeed
    assert r1 is True
    assert r2 is True

    # Now tenant-a is over budget
    r3 = await ctrl.try_record_and_check("tenant-a", "g2", 0.20, 1.00)
    assert r3 is False  # $0.90 + $0.20 = $1.10 > $1.00

    # But tenant-b has room
    r4 = await ctrl.try_record_and_check("tenant-b", "g2", 0.05, 1.00)
    assert r4 is True  # $0.90 + $0.05 = $0.95 <= $1.00
