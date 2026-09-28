"""e2e_full: goal_events is month-partitioned, sequence-safe and retained by partition drop.

Migration c5d6e7f8a9b0 turned goal_events into ``PARTITION BY RANGE (created_at)``.
Pinned here, on real Postgres and (with ``E2E_LEAST_PRIVILEGE=1``) the
production roles:

  * the table really is partitioned, and every partition carries forced RLS;
  * concurrent appends for one goal get distinct, gap-free sequence numbers —
    they come from ``goals.event_seq`` now, because the unique constraint of a
    partitioned table must include ``created_at`` and can no longer catch a
    duplicate the old ``MAX(sequence) + 1`` race produced;
  * a tenant cannot append to or read another tenant's goal events;
  * retention drops whole expired monthly partitions (and trims old rows that
    fell into DEFAULT) instead of one table-wide DELETE, keeping recent events.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


def _least_privilege() -> bool:
    return os.getenv("E2E_LEAST_PRIVILEGE", "").lower() in ("1", "true", "yes")


@pytest_asyncio.fixture(loop_scope="session")
async def roles(
    _backends: tuple[str, str], _migrated_backends: tuple[str, str]
) -> AsyncIterator[SimpleNamespace]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    owner_url, _ = _backends
    app_url, _ = _migrated_backends
    maint_url = (os.environ.get("MAINTENANCE_DATABASE_URL") if _least_privilege() else None) or (
        app_url
    )
    engines = [create_async_engine(u) for u in (owner_url, app_url, maint_url)]
    owner, app, maint = (async_sessionmaker(e, expire_on_commit=False) for e in engines)
    try:
        yield SimpleNamespace(owner=owner, app=app, maint=maint)
    finally:
        for e in engines:
            await e.dispose()


async def _seed_goal(owner: Any, tenant_id: str) -> str:
    from sqlalchemy import text

    goal_id = uuid.uuid4().hex
    async with owner() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email) VALUES (:t, 'ge', :e) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"t": tenant_id, "e": f"{tenant_id}@ge.test"},
        )
        await s.execute(
            text(
                "INSERT INTO goals (id, tenant_id, goal_text, status) "
                "VALUES (:g, :t, 'partitioned events', 'executing')"
            ),
            {"g": goal_id, "t": tenant_id},
        )
    return goal_id


def _ctx(tenant_id: str) -> Any:
    from app.tenancy.context import PlanTier, TenantContext

    return TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")


async def test_goal_events_is_partitioned_with_forced_rls_on_every_partition(
    roles: SimpleNamespace,
) -> None:
    from sqlalchemy import text

    async with roles.owner() as s:
        kind = (
            await s.execute(text("SELECT relkind::text FROM pg_class WHERE relname = 'goal_events'"))
        ).scalar()
        parts = (
            await s.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                    "(SELECT COUNT(*) FROM pg_policy p WHERE p.polrelid = c.oid) "
                    "FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                    "WHERE i.inhparent = 'goal_events'::regclass"
                )
            )
        ).fetchall()
    assert kind == "p"
    names = {p[0] for p in parts}
    assert "goal_events_default" in names
    now = datetime.now(UTC)
    assert f"goal_events_{now.year}_{now.month:02d}" in names
    unprotected = [p[0] for p in parts if not (p[1] and p[2] and p[3] > 0)]
    assert unprotected == [], f"partitions without forced RLS + policy: {unprotected}"


async def test_concurrent_appends_get_distinct_gap_free_sequences(
    roles: SimpleNamespace,
) -> None:
    from sqlalchemy import text

    from app.services.event_store import EventStore

    tenant_id = uuid.uuid4().hex
    goal_id = await _seed_goal(roles.owner, tenant_id)
    store = EventStore(roles.app)
    ctx = _ctx(tenant_id)

    await asyncio.gather(
        *(store.append_event(goal_id, {"type": "step", "n": i}, tenant_ctx=ctx) for i in range(40))
    )

    async with roles.owner() as s:
        seqs = [
            r[0]
            for r in (
                await s.execute(
                    text("SELECT sequence FROM goal_events WHERE goal_id = :g ORDER BY sequence"),
                    {"g": goal_id},
                )
            ).fetchall()
        ]
    assert seqs == list(range(1, 41))
    replay = await store.list_events(goal_id, tenant_ctx=ctx)
    assert sorted(e["n"] for e in replay) == list(range(40))


async def test_a_tenant_cannot_append_to_or_read_another_tenants_goal(
    roles: SimpleNamespace,
) -> None:
    from sqlalchemy import text

    from app.services.event_store import EventStore

    owner_tenant, intruder = uuid.uuid4().hex, uuid.uuid4().hex
    goal_id = await _seed_goal(roles.owner, owner_tenant)
    await _seed_goal(roles.owner, intruder)
    store = EventStore(roles.app)

    await store.append_event(goal_id, {"type": "mine"}, tenant_ctx=_ctx(owner_tenant))
    await store.append_event(goal_id, {"type": "forged"}, tenant_ctx=_ctx(intruder))

    async with roles.owner() as s:
        types = [
            r[0]
            for r in (
                await s.execute(
                    text("SELECT event_type FROM goal_events WHERE goal_id = :g"), {"g": goal_id}
                )
            ).fetchall()
        ]
    assert types == ["mine"]
    assert await store.list_events(goal_id, tenant_ctx=_ctx(intruder)) == []


async def test_retention_drops_expired_month_partitions_and_keeps_recent_events(
    roles: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import text

    import app.db.session as dbs
    from app.scaling.tasks import _delete_expired_records

    tenant_id = uuid.uuid4().hex
    goal_id = await _seed_goal(roles.owner, tenant_id)
    old_month = (datetime.now(UTC) - timedelta(days=400)).replace(day=1)
    nxt = (old_month + timedelta(days=32)).replace(day=1)
    old_part = f"goal_events_{old_month.year}_{old_month.month:02d}"
    async with roles.owner() as s, s.begin():
        await s.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {old_part} PARTITION OF goal_events "
                f"FOR VALUES FROM ('{old_month:%Y-%m-%d}') TO ('{nxt:%Y-%m-%d}')"
            )
        )
        await s.execute(text(f"SELECT app_apply_parent_rls(CAST('{old_part}' AS regclass))"))
        for seq, created in (
            (1, old_month + timedelta(days=3)),  # expired month partition
            (2, datetime.now(UTC) - timedelta(days=5)),  # recent
        ):
            await s.execute(
                text(
                    "INSERT INTO goal_events (id, tenant_id, goal_id, sequence, event_type, "
                    "payload, created_at) VALUES (:id, :t, :g, :seq, 'e', '{}', :c)"
                ),
                {"id": uuid.uuid4().hex, "t": tenant_id, "g": goal_id, "seq": seq, "c": created},
            )

    monkeypatch.setattr(dbs, "get_system_session_factory", lambda: roles.maint)
    result = await _delete_expired_records(90)

    assert "error" not in result, result
    assert old_part in result["deleted"]["goal_events_partitions_dropped"], result
    async with roles.owner() as s:
        exists = (
            await s.execute(text("SELECT to_regclass(:p)"), {"p": old_part})
        ).scalar()
        left = [
            r[0]
            for r in (
                await s.execute(
                    text("SELECT sequence FROM goal_events WHERE goal_id = :g"), {"g": goal_id}
                )
            ).fetchall()
        ]
    assert exists is None
    assert left == [2]
