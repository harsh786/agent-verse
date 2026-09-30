"""A hung dependency check must not hang /health (it is the liveness probe).

Seen on the local stack: Redis was OOM-killed under a running backend and one check
awaited a dead connection forever, so every GET /health timed out and the container
went unhealthy with no hint which dependency was at fault.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.observability.health import HealthCheck, HealthRegistry

pytestmark = pytest.mark.asyncio


async def test_hung_check_reports_down_within_the_timeout() -> None:
    async def hangs() -> None:
        await asyncio.sleep(3600)

    async def ok() -> None:
        return None

    reg = HealthRegistry(check_timeout_s=0.2)
    reg.register(HealthCheck("redis", hangs))
    reg.register(HealthCheck("postgres", ok))
    t0 = time.monotonic()
    healthy, report = await reg.run()
    assert time.monotonic() - t0 < 2
    assert healthy is False
    assert report["redis"] == {"status": "down", "error": "timed out"}
    assert report["postgres"] == {"status": "up"}


async def test_default_timeout_is_bounded() -> None:
    assert 0 < HealthRegistry().check_timeout_s <= 10
