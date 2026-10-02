"""RPA-04: the session registry is heartbeated and a dead owner is reclaimed.

The registry record was written once with a 1 h TTL and never refreshed (a
session used for more than an hour lost it, so another replica opened a second
blank browser under the same id), and a crashed owner kept other replicas
answering 409 for an hour.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.rpa.session_manager import (
    BrowserSession,
    BrowserSessionManager,
    _REGISTRY_TTL_S,
    _REPLICA_ALIVE_TTL_S,
)
from tests.rpa._lease_redis import LeaseRedis


def _manager(redis: Any) -> BrowserSessionManager:
    m = BrowserSessionManager(redis=redis)

    async def _create(sid: str, tenant: str, **_k: Any) -> BrowserSession:
        s = BrowserSession(session_id=sid, tenant_id=tenant)
        s._browser = MagicMock()
        s._browser.close = AsyncMock()
        s._page = MagicMock()
        return s

    m._create_session = _create  # type: ignore[method-assign]
    return m


async def test_registry_ttl_is_short_and_refreshed_on_use() -> None:
    redis = LeaseRedis()
    m = _manager(redis)
    await m.get_or_create("s1", "t1")
    key = "rpa_session:t1:s1"
    assert redis.ttl_left(key) <= _REGISTRY_TTL_S + 1
    # Nearly expired, then used again -> the record lives on.
    value, _ = redis.strings[key]
    redis.strings[key] = (value, redis.strings[key][1] - (_REGISTRY_TTL_S - 2))
    await m.get_or_create("s1", "t1")
    assert redis.ttl_left(key) > _REGISTRY_TTL_S - 5


async def test_heartbeat_refreshes_registry_liveness_and_lease() -> None:
    redis = LeaseRedis()
    m = _manager(redis)
    await m.get_or_create("s1", "t1")
    alive_key = f"rpa_replica:{m.replica_id}:alive"
    assert await redis.get(alive_key) is not None
    redis.strings[alive_key] = ("1", redis.strings[alive_key][1] - (_REPLICA_ALIVE_TTL_S - 1))
    redis.zsets["rpa:leases:t1"]["s1"] -= 100_000  # lease close to expiry
    before = redis.zsets["rpa:leases:t1"]["s1"]
    await m.heartbeat()
    assert redis.ttl_left(alive_key) > _REPLICA_ALIVE_TTL_S - 5
    assert redis.zsets["rpa:leases:t1"]["s1"] > before


async def test_a_dead_owner_is_ignored_and_the_session_reclaimed() -> None:
    redis = LeaseRedis()
    a, b = _manager(redis), _manager(redis)
    await a.get_or_create("s1", "t1")
    assert await b.live_elsewhere("s1", "t1") == a.replica_id
    # Replica A crashes: its liveness key expires (nothing refreshes it).
    await redis.delete(f"rpa_replica:{a.replica_id}:alive")
    assert await b.live_elsewhere("s1", "t1") is None
    session = await b.get_or_create("s1", "t1")
    assert session.is_alive
    record = json.loads(await redis.get("rpa_session:t1:s1"))
    assert record["replica_id"] == b.replica_id


async def test_replica_id_names_the_host() -> None:
    import socket

    m = BrowserSessionManager()
    assert m.replica_id.startswith(socket.gethostname()[:40])
