import asyncio
import pytest
from app.reliability.bulkhead import BulkheadRegistry


def test_get_creates_semaphore_with_default_limit():
    reg = BulkheadRegistry(default_max_concurrent=10)
    sem = reg.get("tenant-1")
    assert sem._value == 10


def test_configure_tenant_overrides_default():
    reg = BulkheadRegistry(default_max_concurrent=10)
    reg.configure_tenant("tenant-1", 3)
    sem = reg.get("tenant-1")
    assert sem._value == 3


def test_different_tenants_have_independent_semaphores():
    reg = BulkheadRegistry(default_max_concurrent=5)
    sem1 = reg.get("t1")
    sem2 = reg.get("t2")
    assert sem1 is not sem2


@pytest.mark.asyncio
async def test_semaphore_limits_concurrent_access():
    reg = BulkheadRegistry(default_max_concurrent=2)
    sem = reg.get("tenant-1")
    results = []

    async def task(i):
        async with sem:
            results.append(i)
            await asyncio.sleep(0.01)

    await asyncio.gather(*[task(i) for i in range(5)])
    assert len(results) == 5  # all eventually ran
    assert reg.available_slots("tenant-1") == 2  # semaphore released
