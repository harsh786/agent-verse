"""WF-17: the goal-start org-stop check is O(1), not a keyspace SCAN.

``_stopped_org_ids_sync`` ran ``scan_iter(emergency_stop:{tenant}:*)`` on every
``run_goal``: SCAN walks the whole keyspace in 100-key steps. Org stops now live
in a per-tenant SET maintained by the org stop endpoint; flags written before the
index existed are indexed once.
"""

from __future__ import annotations

from typing import Any

import fakeredis
import pytest

from app.governance.emergency_stop import (
    ORG_STOP_REASON,
    TENANT_STOP_REASON,
    activate_org_stop,
    clear_org_stop,
    emergency_stop_reason_sync,
    org_stop_key,
    org_stop_index_key,
)

TID = "11111111-1111-1111-1111-111111111111"


class _Counting:
    """Wraps a sync Redis and records every command name."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.inner, name)
        if not callable(attr):
            return attr

        def _wrapped(*a: Any, **k: Any) -> Any:
            self.calls.append(name)
            return attr(*a, **k)

        return _wrapped


@pytest.fixture
def server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.mark.asyncio
async def test_start_check_is_constant_and_never_scans(server: fakeredis.FakeServer) -> None:
    aio = fakeredis.FakeAsyncRedis(server=server)
    sync = fakeredis.FakeRedis(server=server)
    for i in range(500):  # unrelated keys: a SCAN would walk all of them
        sync.set(f"noise:{i}", "x")
    await activate_org_stop(aio, TID, "org-a", activated_by="op")
    sync.get(org_stop_key(TID, "org-a"))  # flag exists
    emergency_stop_reason_sync(sync, TID, resolve_org_id=lambda: "org-b")  # warm (backfill)

    counted = _Counting(sync)
    assert emergency_stop_reason_sync(counted, TID, resolve_org_id=lambda: "org-a") == (
        ORG_STOP_REASON
    )
    assert "scan_iter" not in counted.calls and "scan" not in counted.calls
    assert len(counted.calls) <= 4, counted.calls


@pytest.mark.asyncio
async def test_org_stop_set_and_clear_round_trip_through_the_index(
    server: fakeredis.FakeServer,
) -> None:
    aio = fakeredis.FakeAsyncRedis(server=server)
    sync = fakeredis.FakeRedis(server=server)
    await activate_org_stop(aio, TID, "org-a", activated_by="op")
    assert sync.smembers(org_stop_index_key(TID)) == {b"org-a"}
    assert emergency_stop_reason_sync(sync, TID, resolve_org_id=lambda: "org-a") == (
        ORG_STOP_REASON
    )
    await clear_org_stop(aio, TID, "org-a")
    assert sync.smembers(org_stop_index_key(TID)) == set()
    assert emergency_stop_reason_sync(sync, TID, resolve_org_id=lambda: "org-a") is None


def test_flags_written_before_the_index_are_indexed_once(
    server: fakeredis.FakeServer,
) -> None:
    sync = fakeredis.FakeRedis(server=server)
    sync.set(org_stop_key(TID, "legacy-org"), "1")
    counted = _Counting(sync)
    assert emergency_stop_reason_sync(counted, TID, resolve_org_id=lambda: "legacy-org") == (
        ORG_STOP_REASON
    )
    assert "scan_iter" in counted.calls
    again = _Counting(sync)
    emergency_stop_reason_sync(again, TID, resolve_org_id=lambda: "legacy-org")
    assert "scan_iter" not in again.calls


def test_stale_index_entry_without_flag_does_not_block(server: fakeredis.FakeServer) -> None:
    sync = fakeredis.FakeRedis(server=server)
    emergency_stop_reason_sync(sync, TID)  # index ready
    sync.sadd(org_stop_index_key(TID), "org-gone")
    assert emergency_stop_reason_sync(sync, TID, resolve_org_id=lambda: "org-gone") is None


def test_tenant_stop_still_short_circuits(server: fakeredis.FakeServer) -> None:
    sync = fakeredis.FakeRedis(server=server)
    sync.set(f"emergency_stop:{TID}", "1")
    assert emergency_stop_reason_sync(sync, TID) == TENANT_STOP_REASON


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_redis_org_stop_round_trip(redis_url: str) -> None:
    import redis as redis_lib
    import redis.asyncio as aioredis

    aio = aioredis.Redis.from_url(redis_url)
    sync = redis_lib.Redis.from_url(redis_url)
    try:
        await activate_org_stop(aio, TID, "org-r", activated_by="op")
        assert emergency_stop_reason_sync(sync, TID, resolve_org_id=lambda: "org-r") == (
            ORG_STOP_REASON
        )
        await clear_org_stop(aio, TID, "org-r")
        assert emergency_stop_reason_sync(sync, TID, resolve_org_id=lambda: "org-r") is None
    finally:
        await aio.aclose()
        sync.close()
