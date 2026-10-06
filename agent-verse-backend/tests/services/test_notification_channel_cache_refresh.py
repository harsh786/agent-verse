"""QA-13 (notifications): a channel deleted on one replica stops being notified
on every other replica.

Each process hydrated a tenant's channels ONCE (``ensure_tenant_loaded``),
``sync_from_db`` only ever added rows, and a delete cleared only the deleting
replica's cache (no pub/sub). Delivery reads that cache, so another replica kept
notifying a deleted channel until it restarted.

The per-tenant cache now has a refresh interval (the guardrails engine pattern):
it is re-read at most every ``channel_refresh_s`` and REPLACED by the DB rows,
and every delivery re-checks that freshness first.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.notification_service import NotificationChannel, NotificationService

TENANT = "t-qa13"


class _FakeDb:
    """One shared ``notification_channels`` table, as every replica sees it."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, str, str, dict[str, Any], bool]] = {}
        self.selects = 0
        self.fail = False

    def factory(self) -> Any:
        db = self

        class _Session:
            async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
                sql = str(stmt)
                params = params or {}
                if "set_config" in sql:
                    return MagicMock()
                if db.fail:
                    raise RuntimeError("db down")
                if sql.lstrip().startswith("SELECT channel_id"):
                    db.selects += 1
                    rows = [r for r in db.rows.values() if r[1] == params["tid"]]
                    return MagicMock(fetchall=MagicMock(return_value=rows))
                if "INSERT INTO notification_channels" in sql:
                    import json

                    db.rows[params["cid"]] = (
                        params["cid"], params["tid"], params["ctype"],
                        json.loads(params["cfg"]), params["enabled"],
                    )
                    return MagicMock()
                if "DELETE FROM notification_channels" in sql:
                    row = db.rows.get(params["cid"])
                    gone = row is not None and row[1] == params["tid"]
                    if gone:
                        del db.rows[params["cid"]]
                    return MagicMock(rowcount=1 if gone else 0)
                raise AssertionError(f"unexpected SQL: {sql}")

            @asynccontextmanager
            async def _begin(self) -> Any:
                yield None

            def begin(self) -> Any:
                return self._begin()

        @asynccontextmanager
        async def _factory() -> Any:
            yield _Session()

        return _factory


def _channel(cid: str = "c-1") -> NotificationChannel:
    return NotificationChannel(
        channel_id=cid, tenant_id=TENANT, channel_type="webhook",
        config={"url": "https://hooks.example.com/x"},
    )


def _replica(db: _FakeDb, refresh_s: float) -> NotificationService:
    svc = NotificationService(channel_refresh_s=refresh_s)
    svc.set_db(db.factory())
    return svc


async def _notify(svc: NotificationService) -> dict[str, Any]:
    return await svc.notify_approval_required(
        request_id="r1", goal_id="g1", action="deploy", risk_level="high", tenant_id=TENANT
    )


async def test_channel_deleted_on_a_disappears_from_b_and_is_not_delivered() -> None:
    db = _FakeDb()
    replica_a = _replica(db, refresh_s=0.0)
    replica_b = _replica(db, refresh_s=0.0)
    await replica_a.add_channel_async(_channel("c-1"))
    replica_b._send = AsyncMock()  # type: ignore[method-assign]

    assert (await _notify(replica_b))["sent"] == 1  # B hydrated and delivered

    assert await replica_a.remove_channel_async("c-1", TENANT) is True
    replica_b._send.reset_mock()

    result = await _notify(replica_b)

    assert result == {"sent": 0, "channels": []}
    replica_b._send.assert_not_awaited()
    assert replica_b.get_channels(TENANT) == []


async def test_deletion_propagates_only_after_the_refresh_interval() -> None:
    db = _FakeDb()
    replica_a = _replica(db, refresh_s=0.0)
    replica_b = _replica(db, refresh_s=3600.0)
    await replica_a.add_channel_async(_channel("c-1"))
    await replica_b.ensure_tenant_loaded(TENANT)
    assert [c.channel_id for c in replica_b.get_channels(TENANT)] == ["c-1"]

    await replica_a.remove_channel_async("c-1", TENANT)
    selects = db.selects  # replica B's reads from here on
    await replica_b.ensure_tenant_loaded(TENANT)
    assert db.selects == selects  # within the window: no DB read
    assert [c.channel_id for c in replica_b.get_channels(TENANT)] == ["c-1"]

    replica_b._loaded_tenants[TENANT] -= 3601.0  # the window elapses
    await replica_b.ensure_tenant_loaded(TENANT)

    assert db.selects == selects + 1
    assert replica_b.get_channels(TENANT) == []


async def test_refresh_picks_up_channels_and_config_changes_from_other_replicas() -> None:
    db = _FakeDb()
    replica_a = _replica(db, refresh_s=0.0)
    replica_b = _replica(db, refresh_s=0.0)
    await replica_b.ensure_tenant_loaded(TENANT)
    assert replica_b.get_channels(TENANT) == []

    await replica_a.add_channel_async(_channel("c-2"))
    db.rows["c-2"] = (*db.rows["c-2"][:4], False)  # disabled elsewhere

    await replica_b.ensure_tenant_loaded(TENANT)
    assert replica_b.get_channels(TENANT) == []  # present but disabled
    assert [c.channel_id for c in replica_b._channels[TENANT]] == ["c-2"]


async def test_failed_refresh_keeps_last_known_channels() -> None:
    db = _FakeDb()
    replica = _replica(db, refresh_s=0.0)
    await replica.add_channel_async(_channel("c-1"))
    await replica.ensure_tenant_loaded(TENANT)

    db.fail = True
    await replica.ensure_tenant_loaded(TENANT)

    assert [c.channel_id for c in replica.get_channels(TENANT)] == ["c-1"]


async def test_first_load_failure_is_retried_next_call() -> None:
    db = _FakeDb()
    db.rows["c-1"] = ("c-1", TENANT, "webhook", {"url": "https://h.example.com"}, True)
    replica = _replica(db, refresh_s=3600.0)

    db.fail = True
    await replica.ensure_tenant_loaded(TENANT)
    assert TENANT not in replica._loaded_tenants

    db.fail = False
    await replica.ensure_tenant_loaded(TENANT)
    assert [c.channel_id for c in replica.get_channels(TENANT)] == ["c-1"]


async def test_never_caches_a_foreign_tenants_row_on_refresh() -> None:
    db = _FakeDb()
    db.rows["c-x"] = ("c-x", "someone-else", "webhook", {}, True)
    replica = _replica(db, refresh_s=0.0)

    # The fake returns only matching rows; force a foreign one through.
    original = db.factory

    def _leaky() -> Any:
        factory = original()

        @asynccontextmanager
        async def _wrapped() -> Any:
            async with factory() as session:
                real = session.execute

                async def _execute(stmt: Any, params: Any = None) -> Any:
                    if str(stmt).lstrip().startswith("SELECT channel_id"):
                        return MagicMock(fetchall=MagicMock(return_value=list(db.rows.values())))
                    return await real(stmt, params)

                session.execute = _execute
                yield session

        return _wrapped

    replica.set_db(_leaky())
    await replica.ensure_tenant_loaded(TENANT)
    assert replica.get_channels(TENANT) == []


@pytest.mark.parametrize("refresh_s", [0.0, 3600.0])
async def test_no_db_keeps_the_in_memory_cache(refresh_s: float) -> None:
    svc = NotificationService(channel_refresh_s=refresh_s)
    svc.add_channel(_channel("c-mem"))

    await svc.ensure_tenant_loaded(TENANT)

    assert [c.channel_id for c in svc.get_channels(TENANT)] == ["c-mem"]
