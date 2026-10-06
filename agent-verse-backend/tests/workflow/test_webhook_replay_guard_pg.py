"""WF-REPLAY-1 on real Postgres: the workflow webhook replay guard table.

Migration a7c9e1f3b5d7 creates ``workflow_webhook_replay_guard`` (FORCE RLS). The
request path checks / records a signed delivery under the tenant's RLS context
as a least-privilege (NOBYPASSRLS) role; a new secret drops the old secret's
rows; the retention purge deletes rows of workflows that are gone and expired
``signed-ts:`` rows, never the live ones (an archived workflow keeps its rows).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.workflow import webhook_replay as wr

pytestmark = pytest.mark.integration

_ROLE = "test_app_wf_replay_guard"
_ROLE_PW = "wf-replay-guard-test-pw"


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
                text(f"CREATE ROLE {_ROLE} LOGIN PASSWORD '{_ROLE_PW}' NOSUPERUSER NOBYPASSRLS")
            )
        await s.execute(text(f"GRANT USAGE ON SCHEMA public TO {_ROLE}"))
        await s.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON workflow_webhook_replay_guard TO {_ROLE}")
        )
    url = make_url(pg_url).set(username=_ROLE, password=_ROLE_PW)
    engine = create_async_engine(url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _keys(owner: Any, tenant: str) -> set[tuple[str, str]]:
    async with owner() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT workflow_id, signed_key FROM workflow_webhook_replay_guard "
                    "WHERE tenant_id = :t"
                ),
                {"t": tenant},
            )
        ).all()
    return {(r[0], r[1]) for r in rows}


async def test_guard_is_tenant_isolated_and_follows_the_secret(pg_url: str) -> None:
    owner_engine, owner = await _owner(pg_url)
    app_engine, app_db = await _app_role(pg_url, owner)
    t1, t2 = f"t1-{uuid.uuid4().hex[:8]}", f"t2-{uuid.uuid4().hex[:8]}"
    key = "signed-body:" + "a" * 40
    old, new = wr.secret_ref("old-secret"), wr.secret_ref("new-secret")
    try:
        assert not await wr.already_accepted(app_db, t1, "wf-1", key, old)
        await wr.record_accepted(app_db, t1, "wf-1", key, old)
        await wr.record_accepted(app_db, t1, "wf-1", key, old)  # idempotent
        assert await wr.already_accepted(app_db, t1, "wf-1", key, old)
        # Another tenant (same workflow id and key) sees nothing.
        assert not await wr.already_accepted(app_db, t2, "wf-1", key, old)
        # Another workflow of the same tenant is a separate delivery.
        assert not await wr.already_accepted(app_db, t1, "wf-2", key, old)
        # Signed by another secret: not the same delivery.
        assert not await wr.already_accepted(app_db, t1, "wf-1", key, new)

        other = "signed-body:" + "b" * 40
        await wr.record_accepted(app_db, t1, "wf-1", other, old)
        await wr.record_accepted(app_db, t1, "wf-2", other, old)
        # The first delivery under a new secret drops that workflow's old-secret rows.
        fresh = "signed-body:" + "c" * 40
        await wr.record_accepted(app_db, t1, "wf-1", fresh, new)
        assert await _keys(owner, t1) == {("wf-1", fresh), ("wf-2", other)}
        assert await wr.already_accepted(app_db, t1, "wf-1", fresh, new)

        # Without the tenant GUC the app role cannot write a row at all.
        async with app_db() as s:
            with pytest.raises(Exception, match="row-level security"):
                async with s.begin():
                    await s.execute(
                        text(
                            "INSERT INTO workflow_webhook_replay_guard "
                            "(tenant_id, workflow_id, signed_key, secret_ref) "
                            "VALUES (:t, 'x', 'y', 'z')"
                        ),
                        {"t": t1},
                    )
    finally:
        await app_engine.dispose()
        await owner_engine.dispose()


async def test_purge_deletes_only_dead_rows(pg_url: str) -> None:
    from app.scaling.tasks import _TENANT_HOLD_EXEMPT, _WORKFLOW_REPLAY_GUARD_PURGE

    owner_engine, owner = await _owner(pg_url)
    tenant, other_tenant = uuid.uuid4().hex, uuid.uuid4().hex
    live, archived, gone = (f"wf-{n}-{uuid.uuid4().hex[:8]}" for n in ("live", "arch", "gone"))
    ref = wr.secret_ref("s")
    ttl = wr.TIMESTAMPED_GUARD_TTL_SECONDS
    try:
        async with owner() as s, s.begin():
            for wid, status in ((live, "published"), (archived, "archived")):
                await s.execute(
                    text(
                        "INSERT INTO workflows (id, tenant_id, name, status) "
                        "VALUES (:id, :t, 'w', :st)"
                    ),
                    {"id": wid, "t": tenant, "st": status},
                )
            rows = [
                (tenant, live, "signed-body:live", 400 * 86400),  # live workflow → kept
                (tenant, archived, "signed-body:arch", 60),  # archived → kept
                (tenant, gone, "signed-body:gone", 60),  # workflow gone → dead
                (tenant, live, "signed-ts:old", ttl + 60),  # window long past → dead
                (tenant, live, "signed-ts:new", 30),  # still inside the window → kept
                # The live workflow id under another tenant has no workflow row.
                (other_tenant, live, "signed-body:x", 60),
            ]
            for t, wid, key, age in rows:
                await s.execute(
                    text(
                        "INSERT INTO workflow_webhook_replay_guard "
                        "(tenant_id, workflow_id, signed_key, secret_ref, first_seen_at) "
                        "VALUES (:t, :w, :k, :r, NOW() - make_interval(secs => :age))"
                    ),
                    {"t": t, "w": wid, "k": key, "r": ref, "age": age},
                )
        assert wr.purge_sql(_TENANT_HOLD_EXEMPT) == _WORKFLOW_REPLAY_GUARD_PURGE
        async with owner() as s, s.begin():
            await s.execute(text(_WORKFLOW_REPLAY_GUARD_PURGE), {"lim": 1000})
        assert {k for _w, k in await _keys(owner, tenant)} == {
            "signed-body:live",
            "signed-body:arch",
            "signed-ts:new",
        }
        assert await _keys(owner, other_tenant) == set()
    finally:
        await owner_engine.dispose()
