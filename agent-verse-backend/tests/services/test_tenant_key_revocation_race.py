"""a08-F194-06: a revoke racing an in-flight resolve cannot re-cache the key.

Revoke deleted ``api_key:{hash}`` after the DB write. A resolve that had read
the key row just before that commit then wrote the context back with a plain
``SETEX`` (300 s), so the revoked key kept authenticating on every replica for
up to five minutes. Revoke (and tenant deactivation) now set a tombstone before
deleting the entry, and a resolve re-checks the tombstones after its cache
write and deletes its own entry.
"""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import pytest

from app.services.tenant_service import TenantService, _hash_key


class _DbStub:
    """Stands in for the session factory; the DB helpers are patched per test."""

    def __call__(self) -> Any:
        raise AssertionError("no real DB access in this test")


def _svc(redis: Any) -> TenantService:
    svc = TenantService(db_session_factory=_DbStub())
    svc.set_redis(redis)
    return svc


def _record(tenant_id: str = "t-1", key_id: str = "k-1") -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "plan": "free",
        "api_key_id": key_id,
        "roles": ["operator"],
        "scopes": [],
        "expires_at": None,
    }


@pytest.fixture
async def redis() -> Any:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


async def test_revoke_committing_during_a_resolve_leaves_no_cached_context(redis: Any) -> None:
    raw = "av_free_" + "r" * 40
    key_hash = _hash_key(raw)
    svc = _svc(redis)
    revoked = {"done": False}

    async def _db_revoke(key_id: str, tenant_id: str) -> str | None:
        revoked["done"] = True
        return key_hash

    async def _db_resolve(h: str) -> dict[str, Any] | None:
        if revoked["done"]:
            return None
        rec = _record()
        # The row was read as active; the revoke commits before this resolve
        # writes its cache entry.
        await svc.revoke_api_key("t-1", "k-1")
        return rec

    svc._db_revoke_api_key = _db_revoke  # type: ignore[method-assign]
    svc._db_resolve_by_hash = _db_resolve  # type: ignore[method-assign]

    first = await svc.resolve_api_key(raw)
    assert first is not None  # the in-flight request itself was already admitted
    assert await redis.get(f"api_key:{key_hash}") is None
    assert await redis.get(f"api_key_revoked:{key_hash}") == "1"
    # The next request is refused (DB says revoked; nothing stale in the cache).
    assert await svc.resolve_api_key(raw) is None


async def test_tombstone_outlives_the_cache_ttl(redis: Any) -> None:
    raw = "av_free_" + "s" * 40
    key_hash = _hash_key(raw)
    svc = _svc(redis)

    async def _db_revoke(key_id: str, tenant_id: str) -> str | None:
        return key_hash

    svc._db_revoke_api_key = _db_revoke  # type: ignore[method-assign]
    await svc.revoke_api_key("t-1", "k-1")
    ttl = await redis.ttl(f"api_key_revoked:{key_hash}")
    assert 300 < ttl <= 600


async def test_deactivation_during_a_resolve_leaves_no_cached_context(redis: Any) -> None:
    raw = "av_free_" + "d" * 40
    key_hash = _hash_key(raw)
    svc = _svc(redis)
    svc._db = None  # deactivate_tenant's in-memory path; resolve is patched below

    async def _deactivate_then_record() -> dict[str, Any]:
        svc._tenants["t-9"] = {"tenant_id": "t-9", "plan": "free", "is_active": True}
        await svc.deactivate_tenant("t-9")
        return _record(tenant_id="t-9", key_id="k-9")

    # Drive _cache_resolved_key directly with the context the resolve read.
    from app.tenancy.context import PlanTier, TenantContext

    rec = await _deactivate_then_record()
    ctx = TenantContext(
        tenant_id=rec["tenant_id"], plan=PlanTier.FREE, api_key_id=rec["api_key_id"]
    )
    await svc._cache_resolved_key(f"api_key:{key_hash}", ctx, None)
    assert await redis.get("tenant_deactivated:t-9") == "1"
    assert await redis.get(f"api_key:{key_hash}") is None


async def test_unrevoked_key_is_still_cached(redis: Any) -> None:
    raw = "av_free_" + "c" * 40
    key_hash = _hash_key(raw)
    svc = _svc(redis)

    async def _db_resolve(h: str) -> dict[str, Any] | None:
        return _record()

    svc._db_resolve_by_hash = _db_resolve  # type: ignore[method-assign]
    assert await svc.resolve_api_key(raw) is not None
    assert await redis.get(f"api_key:{key_hash}") is not None


async def test_revoke_fails_closed_when_the_tombstone_cannot_be_written() -> None:
    from app.services.tenant_service import KeyStoreUnavailableError

    class _NoWrites:
        async def setex(self, *a: object) -> None:
            raise ConnectionError("redis down")

        async def delete(self, *a: object) -> int:
            raise ConnectionError("redis down")

    svc = _svc(_NoWrites())

    async def _db_revoke(key_id: str, tenant_id: str) -> str | None:
        return "h"

    svc._db_revoke_api_key = _db_revoke  # type: ignore[method-assign]
    with pytest.raises(KeyStoreUnavailableError):
        await svc.revoke_api_key("t-1", "k-1")


@pytest.mark.integration
async def test_revocation_race_on_real_redis(redis_url: str) -> None:
    """Same race against a real Redis server (testcontainer)."""
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await test_revoke_committing_during_a_resolve_leaves_no_cached_context(client)
    finally:
        await client.aclose()
