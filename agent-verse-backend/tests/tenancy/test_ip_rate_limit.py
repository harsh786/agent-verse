"""Regression tests: per-IP limiter for signup and the SSO token endpoints.

1. The auth limiter raised its 429 INSIDE ``try: ... except Exception`` — the
   429 was swallowed and nothing was ever limited.
2. It was a no-op without Redis; signup failed open on Redis errors.
3. ``ZADD {str(now_ms): now_ms}`` collapsed requests in the same millisecond.
4. Both keyed on ``request.client.host`` (the proxy, behind a load balancer).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from fastapi import HTTPException

from app.tenancy.ip_rate_limit import enforce_ip_rate_limit


def _req(peer: str = "198.51.100.9", xff: str | None = None) -> Any:
    headers = {"X-Forwarded-For": xff} if xff else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


async def _hits(req: Any, n: int, **kw: Any) -> list[int]:
    out = []
    for _ in range(n):
        try:
            await enforce_ip_rate_limit(req, bucket="t", limit=10, window_s=60, **kw)
            out.append(200)
        except HTTPException as exc:
            out.append(exc.status_code)
    return out


@pytest.mark.asyncio
async def test_redis_limit_counts_same_millisecond_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.tenancy.ip_rate_limit as mod

    monkeypatch.setattr(mod.time, "time", lambda: 1_700_000_000.0)  # frozen clock
    statuses = await _hits(_req(), 11, redis=fakeredis.FakeAsyncRedis())
    assert statuses == [200] * 10 + [429]


@pytest.mark.asyncio
async def test_without_redis_the_local_window_limits() -> None:
    assert (await _hits(_req(), 11))[-1] == 429


@pytest.mark.asyncio
async def test_redis_error_falls_back_to_local_window() -> None:
    broken = MagicMock()
    broken.pipeline.return_value.execute = AsyncMock(side_effect=ConnectionError("down"))
    assert (await _hits(_req(), 11, redis=broken))[-1] == 429


@pytest.mark.asyncio
async def test_clients_behind_a_trusted_proxy_get_separate_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    a = await _hits(_req(peer="10.0.0.2", xff="203.0.113.1"), 10)
    b = await _hits(_req(peer="10.0.0.2", xff="203.0.113.2"), 1)
    assert a == [200] * 10
    assert b == [200]  # a different real client is not throttled by the first
