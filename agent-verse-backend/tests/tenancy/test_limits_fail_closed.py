"""Regression tests: plan limits fail closed on Redis trouble.

1. The concurrent-goal check ``pass``-ed on any Redis error: an outage (or a
   Redis without Lua) disabled the per-plan concurrency cap.
2. The tenant rate limiter's fallback used the same (down) Redis, so an outage
   surfaced as a 500 on every authenticated request.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.limits import (
    ConcurrencyLimitUnavailableError,
    PlanLimitExceededError,
    check_and_increment_concurrent_goals,
)
from app.tenancy.middleware import TenantMiddleware

FREE = TenantContext(tenant_id="t-free", plan=PlanTier.FREE, api_key_id="k")


@pytest.mark.asyncio
async def test_redis_without_lua_still_enforces_the_cap() -> None:
    redis = fakeredis.FakeAsyncRedis()  # no lupa installed → EVAL unsupported
    await check_and_increment_concurrent_goals(FREE, redis)
    await check_and_increment_concurrent_goals(FREE, redis)
    with pytest.raises(PlanLimitExceededError):
        await check_and_increment_concurrent_goals(FREE, redis)
    assert int(await redis.get("concurrent_goals:t-free")) == 2  # over-limit INCR undone


@pytest.mark.asyncio
async def test_unreachable_redis_refuses_instead_of_allowing() -> None:
    redis = MagicMock()
    for op in ("eval", "incr", "expire", "decr"):
        setattr(redis, op, AsyncMock(side_effect=ConnectionError("down")))
    with pytest.raises(ConcurrencyLimitUnavailableError) as exc:
        await check_and_increment_concurrent_goals(FREE, redis)
    assert exc.value.http_status == 503


@pytest.mark.asyncio
async def test_no_redis_wired_is_unchanged() -> None:
    await check_and_increment_concurrent_goals(FREE, None)


class _DownRedis:
    def __getattr__(self, name: str) -> Any:
        async def _fail(*a: Any, **k: Any) -> Any:
            raise ConnectionError("redis down")

        return _fail


def test_rate_limit_redis_outage_is_not_a_500() -> None:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return FREE if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve, rate_limiter=_DownRedis())

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/ping", headers={"X-API-Key": "k"}).status_code == 200
