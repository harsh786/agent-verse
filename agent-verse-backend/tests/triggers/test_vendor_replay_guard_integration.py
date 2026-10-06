"""DEF-NEW-3 on real Postgres: the vendor webhook replay guard table.

Migration e5f7a9b1c3d5 creates ``vendor_webhook_replay_guard`` (FORCE RLS). The
request path records / checks a body-signed delivery under the tenant's RLS
context as a least-privilege (NOBYPASSRLS) role, so one tenant never sees
another's rows; the retention purge deletes only rows that can no longer verify
(trigger deleted, or a secret rotation completed after the row was recorded).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.triggers.webhooks import replay

pytestmark = pytest.mark.integration

_ROLE = "test_app_replay_guard"
_ROLE_PW = "replay-guard-test-pw"


async def _owner(pg_url: str) -> Any:
    engine = create_async_engine(pg_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _app_role(pg_url: str, owner: Any) -> Any:
    async with owner() as s, s.begin():
        exists = (
            await s.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": _ROLE})
        ).first()
        if not exists:
            await s.execute(
                text(
                    f"CREATE ROLE {_ROLE} LOGIN PASSWORD '{_ROLE_PW}' NOSUPERUSER NOBYPASSRLS"
                )
            )
        await s.execute(text(f"GRANT USAGE ON SCHEMA public TO {_ROLE}"))
        await s.execute(
            text(f"GRANT SELECT, INSERT, DELETE ON vendor_webhook_replay_guard TO {_ROLE}")
        )
    url = make_url(pg_url).set(username=_ROLE, password=_ROLE_PW)
    engine = create_async_engine(url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_guard_is_tenant_isolated_under_a_least_privilege_role(pg_url: str) -> None:
    owner_engine, owner = await _owner(pg_url)
    app_engine, app_db = await _app_role(pg_url, owner)
    t1, t2 = f"t1-{uuid.uuid4().hex[:8]}", f"t2-{uuid.uuid4().hex[:8]}"
    key = "signed-body:" + "a" * 40
    try:
        assert not await replay.already_delivered(app_db, t1, "trg-1", key)
        await replay.record_delivered(app_db, t1, "trg-1", key)
        await replay.record_delivered(app_db, t1, "trg-1", key)  # idempotent
        assert await replay.already_delivered(app_db, t1, "trg-1", key)
        # Another tenant (same trigger id and key) sees nothing.
        assert not await replay.already_delivered(app_db, t2, "trg-1", key)
        # Another trigger of the same tenant is a separate delivery.
        assert not await replay.already_delivered(app_db, t1, "trg-2", key)
        async with owner() as s:
            count = (
                await s.execute(
                    text("SELECT COUNT(*) FROM vendor_webhook_replay_guard WHERE tenant_id = :t"),
                    {"t": t1},
                )
            ).scalar_one()
        assert count == 1
        # Without the tenant GUC the app role cannot write a row at all.
        async with app_db() as s:
            with pytest.raises(Exception, match="row-level security"):
                async with s.begin():
                    await s.execute(
                        text(
                            "INSERT INTO vendor_webhook_replay_guard "
                            "(tenant_id, trigger_id, signed_key) VALUES (:t, 'x', 'y')"
                        ),
                        {"t": t1},
                    )
    finally:
        await app_engine.dispose()
        await owner_engine.dispose()


async def test_purge_deletes_only_rows_that_can_no_longer_verify(pg_url: str) -> None:
    from app.scaling.tasks import _TENANT_HOLD_EXEMPT

    owner_engine, owner = await _owner(pg_url)
    tenant = uuid.uuid4().hex
    live, rotated, gone = (f"s-{n}-{uuid.uuid4().hex[:8]}" for n in ("live", "rot", "gone"))
    try:
        async with owner() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :id, :e, 'free', true)"
                ),
                {"id": tenant, "e": f"{tenant}@example.test"},
            )
            for sid, grace in ((live, None), (rotated, 3600)):
                await s.execute(
                    text(
                        "INSERT INTO schedules (id, tenant_id, goal_id_template, trigger_type, "
                        " config, paused, webhook_secret_grace_until) "
                        "VALUES (:id, :t, 'x', 'github', '{}'::jsonb, false, "
                        " CASE WHEN CAST(:g AS integer) IS NULL THEN NULL "
                        "      ELSE NOW() - make_interval(secs => CAST(:g AS integer)) END)"
                    ),
                    {"id": sid, "t": tenant, "g": grace},
                )
            rows = [
                (gone, "k-gone", 60),  # trigger deleted → dead
                (live, "k-live", 400 * 86400),  # secret never rotated → kept
                (rotated, "k-before", 7200),  # before the rotation → dead
                (rotated, "k-after", 60),  # signed with the new secret → kept
            ]
            for trigger, key, age in rows:
                await s.execute(
                    text(
                        "INSERT INTO vendor_webhook_replay_guard "
                        "(tenant_id, trigger_id, signed_key, first_seen_at) "
                        "VALUES (:t, :tr, :k, NOW() - make_interval(secs => :age))"
                    ),
                    {"t": tenant, "tr": trigger, "k": key, "age": age},
                )
        async with owner() as s, s.begin():
            await s.execute(text(replay.purge_sql(_TENANT_HOLD_EXEMPT)), {"lim": 1000})
        async with owner() as s:
            left = {
                r[0]
                for r in (
                    await s.execute(
                        text(
                            "SELECT signed_key FROM vendor_webhook_replay_guard "
                            "WHERE tenant_id = :t"
                        ),
                        {"t": tenant},
                    )
                ).all()
            }
        assert left == {"k-live", "k-after"}
    finally:
        await owner_engine.dispose()
