"""QA-19: an expired API key must stop authenticating at its ``expires_at``.

Regression: ``resolve_api_key`` cached the resolved context for a flat 300 s and
the cache-hit path returned it without looking at the key's expiry, so a key
kept working for up to five minutes after it expired. The cache TTL is now
bounded by the key's remaining lifetime, the entry carries ``expires_at``, and a
hit past it is rejected (and the entry dropped).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.services.tenant_service import TenantService, _hash_key


class _DictRedis:
    """Tiny async Redis stand-in that records the TTL of every ``setex``."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}
        self.deleted: list[str] = []

    async def get(self, key: str) -> Any:
        return self.data.get(key)

    async def setex(self, key: str, ttl: int, value: Any) -> None:
        self.data[key] = value
        self.ttls[key] = int(ttl)

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            self.deleted.append(k)
            if self.data.pop(k, None) is not None:
                n += 1
        return n


async def _svc_with_key(expires_at: datetime | None) -> tuple[TenantService, _DictRedis, str]:
    svc = TenantService()
    created = await svc.create_tenant("Expiry Corp", "expiry@example.com")
    key = await svc.create_api_key(
        created["tenant_id"], "short-lived", ["goals:read"], expires_at=expires_at
    )
    redis = _DictRedis()
    svc.set_redis(redis)
    return svc, redis, key["raw_key"]


@pytest.mark.asyncio
async def test_cache_ttl_is_bounded_by_key_expiry() -> None:
    svc, redis, raw = await _svc_with_key(datetime.now(UTC) + timedelta(seconds=40))
    assert await svc.resolve_api_key(raw) is not None
    ttl = redis.ttls[f"api_key:{_hash_key(raw)}"]
    assert 0 < ttl <= 40


@pytest.mark.asyncio
async def test_cache_ttl_stays_300_for_long_lived_and_non_expiring_keys() -> None:
    svc, redis, raw = await _svc_with_key(datetime.now(UTC) + timedelta(days=30))
    assert await svc.resolve_api_key(raw) is not None
    assert redis.ttls[f"api_key:{_hash_key(raw)}"] == 300

    svc2, redis2, raw2 = await _svc_with_key(None)
    assert await svc2.resolve_api_key(raw2) is not None
    assert redis2.ttls[f"api_key:{_hash_key(raw2)}"] == 300


@pytest.mark.asyncio
async def test_cache_entry_records_expires_at() -> None:
    expiry = datetime.now(UTC) + timedelta(seconds=120)
    svc, redis, raw = await _svc_with_key(expiry)
    assert await svc.resolve_api_key(raw) is not None
    entry = json.loads(redis.data[f"api_key:{_hash_key(raw)}"])
    assert datetime.fromisoformat(entry["expires_at"]) == expiry


@pytest.mark.asyncio
async def test_cached_hit_past_expiry_is_rejected() -> None:
    """A cache entry that outlives the key (TTL rounding, clock skew, an entry
    written before the fix) must not authenticate once ``expires_at`` passed."""
    svc, redis, raw = await _svc_with_key(datetime.now(UTC) + timedelta(seconds=120))
    ctx = await svc.resolve_api_key(raw)
    assert ctx is not None
    cache_key = f"api_key:{_hash_key(raw)}"
    entry = json.loads(redis.data[cache_key])
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    entry["expires_at"] = past
    redis.data[cache_key] = json.dumps(entry)
    # The authoritative record has expired as well.
    svc._keys[ctx.api_key_id]["expires_at"] = past

    assert await svc.resolve_api_key(raw) is None
    assert cache_key in redis.deleted


@pytest.mark.asyncio
async def test_already_expired_key_is_not_cached() -> None:
    svc, redis, raw = await _svc_with_key(datetime.now(UTC) - timedelta(seconds=5))
    assert await svc.resolve_api_key(raw) is None
    assert f"api_key:{_hash_key(raw)}" not in redis.data


@pytest.mark.asyncio
async def test_legacy_cache_entry_without_expiry_is_re_resolved() -> None:
    """Entries written before expiry was cached carry no ``expires_at`` field;
    trusting one could serve an expired key, so they are re-resolved."""
    svc, redis, raw = await _svc_with_key(datetime.now(UTC) - timedelta(seconds=5))
    ctx_tenant = next(iter(svc._tenants))
    redis.data[f"api_key:{_hash_key(raw)}"] = json.dumps(
        {
            "tenant_id": ctx_tenant,
            "plan": "free",
            "api_key_id": "legacy",
            "roles": ["operator"],
            "scopes": ["goals:read"],
        }
    )
    assert await svc.resolve_api_key(raw) is None


@pytest.mark.asyncio
async def test_revoke_drops_the_cached_entry() -> None:
    svc, redis, raw = await _svc_with_key(None)
    ctx = await svc.resolve_api_key(raw)
    assert ctx is not None
    await svc.revoke_api_key(ctx.tenant_id, ctx.api_key_id)
    assert f"api_key:{_hash_key(raw)}" not in redis.data
    assert await svc.resolve_api_key(raw) is None
