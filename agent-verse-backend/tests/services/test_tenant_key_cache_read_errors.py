"""a08-F194-03: API-key cache read failures are visible, malformed entries dropped.

The shared ``api_key:{hash}`` read sat in ``except Exception: pass``: a Redis
outage (every request silently paying a DB round trip) and a corrupt entry
(skipped on every request until its TTL ran out) were both invisible. The DB
stays authoritative — resolution still succeeds — but a Redis failure is now
logged (throttled to one line per window) and a malformed entry is deleted.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.services import tenant_service as ts
from app.services.tenant_service import TenantService, _hash_key


class _BrokenRedis:
    def __init__(self) -> None:
        self.gets = 0

    async def get(self, key: str) -> Any:
        self.gets += 1
        raise ConnectionError("redis down")

    async def setex(self, *args: object) -> None:
        raise ConnectionError("redis down")

    async def delete(self, *keys: str) -> int:
        raise ConnectionError("redis down")


class _DictRedis:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.deleted: list[str] = []

    async def get(self, key: str) -> Any:
        return self.data.get(key)

    async def setex(self, key: str, ttl: int, value: Any) -> None:
        self.data[key] = value

    async def delete(self, *keys: str) -> int:
        for k in keys:
            self.deleted.append(k)
            self.data.pop(k, None)
        return len(keys)


@pytest.fixture(autouse=True)
def _reset_throttle() -> None:
    ts._cache_read_warn_state.update({"last": 0.0, "suppressed": 0})


async def _svc_and_key() -> tuple[TenantService, str]:
    svc = TenantService()
    created = await svc.create_tenant("Cache Corp", "cache@example.com")
    return svc, str(created["api_key"])


async def test_redis_read_error_is_logged_once_per_window_and_db_still_answers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    svc, raw = await _svc_and_key()
    redis = _BrokenRedis()
    svc.set_redis(redis)
    with caplog.at_level(logging.WARNING, logger="app.services.tenant_service"):
        for _ in range(5):
            assert await svc.resolve_api_key(raw) is not None
    assert redis.gets == 5
    lines = [r for r in caplog.records if "api_key_cache_read_failed" in r.getMessage()]
    assert len(lines) == 1
    assert ts._cache_read_warn_state["suppressed"] == 4


async def test_malformed_cache_entry_is_dropped_and_resolution_falls_through(
    caplog: pytest.LogCaptureFixture,
) -> None:
    svc, raw = await _svc_and_key()
    redis = _DictRedis()
    cache_key = f"api_key:{_hash_key(raw)}"
    redis.data[cache_key] = "{not json"
    svc.set_redis(redis)
    with caplog.at_level(logging.WARNING, logger="app.services.tenant_service"):
        ctx = await svc.resolve_api_key(raw)
    assert ctx is not None
    assert cache_key in redis.deleted
    assert any("api_key_cache_entry_malformed" in r.getMessage() for r in caplog.records)
    # Re-cached from the authoritative lookup with a valid entry.
    assert redis.data[cache_key].startswith("{")


async def test_entry_with_an_unknown_plan_is_dropped() -> None:
    svc, raw = await _svc_and_key()
    redis = _DictRedis()
    cache_key = f"api_key:{_hash_key(raw)}"
    redis.data[cache_key] = (
        '{"tenant_id": "t", "plan": "platinum", "api_key_id": "k",'
        ' "roles": [], "scopes": [], "expires_at": null}'
    )
    svc.set_redis(redis)
    ctx = await svc.resolve_api_key(raw)
    assert ctx is not None
    assert ctx.plan.value == "free"  # from the real record, not the corrupt entry
    assert cache_key in redis.deleted
