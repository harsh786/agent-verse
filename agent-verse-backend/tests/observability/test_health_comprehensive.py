"""Comprehensive tests for HealthRegistry and HealthCheck."""
from __future__ import annotations

import pytest

from app.observability.health import HealthCheck, HealthRegistry


# ── 1. HealthCheck dataclass ─────────────────────────────────────────────────

def test_health_check_fields():
    async def check():
        pass
    hc = HealthCheck(name="postgres", check=check)
    assert hc.name == "postgres"
    assert callable(hc.check)


def test_health_check_frozen():
    async def check():
        pass
    hc = HealthCheck(name="redis", check=check)
    with pytest.raises((AttributeError, TypeError)):
        hc.name = "other"  # type: ignore[misc]


# ── 2. HealthRegistry — empty ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_empty_registry():
    registry = HealthRegistry()
    healthy, report = await registry.run()
    assert healthy is True
    assert report == {}


@pytest.mark.asyncio
async def test_register_adds_check():
    registry = HealthRegistry()

    async def noop():
        pass

    registry.register(HealthCheck(name="db", check=noop))
    assert len(registry.checks) == 1


# ── 3. All healthy ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_healthy_checks():
    registry = HealthRegistry()

    async def healthy_check():
        pass  # No exception = healthy

    for name in ["postgres", "redis", "mcp"]:
        registry.register(HealthCheck(name=name, check=healthy_check))

    healthy, report = await registry.run()
    assert healthy is True
    assert len(report) == 3
    for status in report.values():
        assert status["status"] == "up"


# ── 4. Mix of healthy and unhealthy ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_failing_check_overall_unhealthy():
    registry = HealthRegistry()

    async def ok():
        pass

    async def fail():
        raise ConnectionError("Cannot connect")

    registry.register(HealthCheck(name="postgres", check=ok))
    registry.register(HealthCheck(name="redis", check=fail))

    healthy, report = await registry.run()
    assert healthy is False
    assert report["postgres"]["status"] == "up"
    assert report["redis"]["status"] == "down"
    assert "Cannot connect" in report["redis"]["error"]


@pytest.mark.asyncio
async def test_all_failing_checks():
    registry = HealthRegistry()

    for name in ["db", "cache"]:
        async def fail(n=name):
            raise RuntimeError(f"{n} down")
        registry.register(HealthCheck(name=name, check=fail))

    healthy, report = await registry.run()
    assert healthy is False
    assert report["db"]["status"] == "down"
    assert report["cache"]["status"] == "down"


# ── 5. Checks run concurrently ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_checks_run_concurrently():
    import asyncio
    import time

    events = []

    async def slow_check():
        await asyncio.sleep(0.05)
        events.append("slow_done")

    async def fast_check():
        events.append("fast_done")

    registry = HealthRegistry()
    registry.register(HealthCheck(name="slow", check=slow_check))
    registry.register(HealthCheck(name="fast", check=fast_check))

    t0 = time.monotonic()
    healthy, report = await registry.run()
    elapsed = time.monotonic() - t0

    # If run sequentially, it would take 0.1s. Concurrent takes ~0.05s.
    assert elapsed < 0.15, "Checks should run concurrently"
    assert healthy is True


# ── 6. Exception message in report ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_error_message_in_report():
    registry = HealthRegistry()

    async def failing():
        raise ValueError("specific error message")

    registry.register(HealthCheck(name="failing_svc", check=failing))

    healthy, report = await registry.run()
    assert "specific error message" in report["failing_svc"]["error"]


# ── 7. Multiple registrations ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_multiple_registrations_all_tracked():
    registry = HealthRegistry()

    async def ok():
        pass

    for i in range(10):
        registry.register(HealthCheck(name=f"svc_{i}", check=ok))

    healthy, report = await registry.run()
    assert healthy is True
    assert len(report) == 10


# ── 8. Error types all captured ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_various_exception_types_captured():
    registry = HealthRegistry()
    exceptions = [
        RuntimeError("runtime"),
        OSError("os error"),
        TimeoutError("timeout"),
        Exception("generic"),
    ]

    for i, exc in enumerate(exceptions):
        e = exc
        async def check(err=e):
            raise err
        registry.register(HealthCheck(name=f"svc_{i}", check=check))

    healthy, report = await registry.run()
    assert healthy is False
    assert len(report) == 4
    for entry in report.values():
        assert entry["status"] == "down"
        assert "error" in entry


# ── 9. Report structure ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_healthy_report_no_error_key():
    registry = HealthRegistry()

    async def ok():
        pass

    registry.register(HealthCheck(name="healthy_svc", check=ok))
    _, report = await registry.run()
    assert "error" not in report["healthy_svc"]
    assert report["healthy_svc"]["status"] == "up"


@pytest.mark.asyncio
async def test_unhealthy_report_has_error_key():
    registry = HealthRegistry()

    async def fail():
        raise Exception("broken")

    registry.register(HealthCheck(name="broken_svc", check=fail))
    _, report = await registry.run()
    assert "error" in report["broken_svc"]
