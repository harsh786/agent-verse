"""NATIVE-02 against real Redis: the daily recipient quota (Lua check-and-consume)
is shared by two independent clients (two replicas)."""

from __future__ import annotations

import uuid

import pytest

from app.tenancy.context import PlanTier
from app.tools import email_quota

pytestmark = pytest.mark.integration


async def test_quota_is_shared_across_replicas(
    redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import redis.asyncio as aioredis

    from app.core.config import get_settings

    monkeypatch.setenv("EMAIL_DAILY_RECIPIENT_QUOTA", "5")
    get_settings.cache_clear()
    a, b = aioredis.from_url(redis_url), aioredis.from_url(redis_url)
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    try:
        r1 = await email_quota.consume(a, tenant, PlanTier.FREE, 3)
        assert (r1.used, r1.remaining) == (3, 2)
        r2 = await email_quota.consume(b, tenant, PlanTier.FREE, 2)
        assert r2.remaining == 0
        with pytest.raises(email_quota.EmailQuotaExceededError):
            await email_quota.consume(a, tenant, PlanTier.FREE, 1)
        # A refused request consumed nothing; another tenant is unaffected.
        assert int(await a.get(f"email_quota:{tenant}:{email_quota._day()}")) == 5
        assert (await email_quota.consume(b, f"{tenant}-x", PlanTier.FREE, 5)).remaining == 0
    finally:
        await a.aclose()
        await b.aclose()
        get_settings.cache_clear()
