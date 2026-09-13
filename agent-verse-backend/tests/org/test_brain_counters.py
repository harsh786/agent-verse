import pytest


@pytest.mark.asyncio
async def test_snapshot_and_record_launch():
    try:
        import fakeredis.aioredis as fr
    except ImportError:
        pytest.skip("fakeredis not available")
    r = fr.FakeRedis()
    from app.org.brain_counters import BrainCounters

    c = BrainCounters(r, org_id="org-1")
    spend, count, since = await c.snapshot()
    assert spend == 0.0 and count == 0 and since >= 10**8  # never launched → huge gap

    await c.record_launch(est_cost_usd=3.5)
    spend, count, since = await c.snapshot()
    assert spend == 3.5 and count == 1 and since < 5


@pytest.mark.asyncio
async def test_tick_lock_is_exclusive():
    try:
        import fakeredis.aioredis as fr
    except ImportError:
        pytest.skip("fakeredis not available")
    r = fr.FakeRedis()
    from app.org.brain_counters import BrainCounters

    c = BrainCounters(r, org_id="org-2")
    assert await c.acquire_tick_lock() is True
    assert await c.acquire_tick_lock() is False  # already held
