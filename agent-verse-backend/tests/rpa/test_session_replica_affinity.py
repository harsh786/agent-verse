"""RPA browser sessions are per-process: the manager never pretends otherwise.

A live Playwright page exists only in the process that opened it, but the
session registry is shared (Redis). The manager on another replica refuses to
open a brand-new blank browser under the same id (SessionOnAnotherReplicaError);
the executor and API relay such requests to the owner (tests/rpa/test_session_relay.py,
RPA-07) instead of the 409 they used to answer.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.rpa.session_manager import (
    BrowserSession,
    BrowserSessionManager,
    SessionOnAnotherReplicaError,
)
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-rep", plan=PlanTier.PROFESSIONAL, api_key_id="kid-r")
_KEY = "av_test_rpa_replica"


class _FakeRedis:
    """The shared registry both replicas see."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.data[key] = value

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)

    async def keys(self, pattern: str) -> list[str]:
        prefix = pattern.rstrip("*")
        return [k for k in self.data if k.startswith(prefix)]


def _live(manager: BrowserSessionManager, sid: str, tenant: str) -> BrowserSession:
    session = BrowserSession(session_id=sid, tenant_id=tenant)
    session._browser = MagicMock()
    session._page = MagicMock()
    session.ssrf_guarded = True
    return session


async def _open_on(manager: BrowserSessionManager, sid: str, tenant: str) -> None:
    manager._create_session = AsyncMock(  # type: ignore[method-assign]
        return_value=_live(manager, sid, tenant)
    )
    await manager.get_or_create(sid, tenant)


def _pair() -> tuple[BrowserSessionManager, BrowserSessionManager, _FakeRedis]:
    redis = _FakeRedis()
    return BrowserSessionManager(redis=redis), BrowserSessionManager(redis=redis), redis


async def test_registry_records_the_owning_replica() -> None:
    a, _b, redis = _pair()
    await _open_on(a, "s1", "t1")
    record = json.loads(redis.data["rpa_session:t1:s1"])
    assert record["replica_id"] == a.replica_id


async def test_other_replica_refuses_instead_of_opening_a_blank_browser() -> None:
    a, b, _redis = _pair()
    await _open_on(a, "s1", "t1")
    b._create_session = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(SessionOnAnotherReplicaError) as exc_info:
        await b.get_or_create("s1", "t1")

    b._create_session.assert_not_called()
    assert exc_info.value.owner_replica == a.replica_id
    assert await b.live_elsewhere("s1", "t1") == a.replica_id
    assert await a.live_elsewhere("s1", "t1") is None


async def test_owning_replica_and_unknown_sessions_are_unaffected() -> None:
    a, b, _redis = _pair()
    await _open_on(a, "s1", "t1")
    assert (await a.get_or_create("s1", "t1")).is_alive
    # A different tenant's id, or a fresh id, is not "elsewhere".
    assert await b.live_elsewhere("s1", "other-tenant") is None
    assert await b.live_elsewhere("fresh", "t1") is None


async def test_closed_session_is_released_for_every_replica() -> None:
    a, b, _redis = _pair()
    await _open_on(a, "s1", "t1")
    await a.close("s1", "t1")
    assert await b.live_elsewhere("s1", "t1") is None
