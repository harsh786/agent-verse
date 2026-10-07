"""a10-F236-01/04 on real Postgres: agent snapshot versions are unique and allocated
in the INSERT.

* 12 concurrent snapshots of one agent, as the least-privilege (NOBYPASSRLS) app
  role, get distinct versions 1..N (they used to be ``len(existing) + 1`` from a
  separate read, so racing snapshots shared a number);
* migration e2b6d4f8a1c3 renumbers pre-existing duplicates (earliest keeps its
  number, the JSON ``version`` follows) and adds the unique constraint;
* it lifts FORCE RLS only for the data fix: afterwards the table is ENABLE + FORCE
  again, both as a superuser and as a NOSUPERUSER / NOBYPASSRLS owner (which must
  still see - and renumber - every tenant's rows).

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/api/test_agent_snapshots_pg.py -q -m integration
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.agents import _load_snapshots_from_db, _save_snapshot_to_db
from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
_BEFORE = "c4e8a2f6b1d3"
_REVISION = "e2b6d4f8a1c3"


async def _rls_flags(engine: object) -> tuple[bool, bool]:
    async with engine.connect() as c:  # type: ignore[attr-defined]
        row = (
            await c.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'agent_snapshots' AND relkind = 'r'"
                )
            )
        ).one()
    return bool(row[0]), bool(row[1])


async def _insert_duplicates(engine: object, tenant: str, agent: str) -> None:
    async with engine.begin() as c:  # type: ignore[attr-defined]
        for sid, version, minute in (("s-a", 1, 0), ("s-b", 2, 1), ("s-c", 2, 2), ("s-d", 2, 3)):
            await c.execute(
                text(
                    "INSERT INTO agent_snapshots (id, tenant_id, agent_id, version, snapshot, "
                    "snapshotted_at) VALUES (:id, :t, :a, :v, CAST(:s AS jsonb), "
                    "now() + make_interval(mins => :m))"
                ),
                {"id": f"{sid}-{agent}", "t": tenant, "a": agent, "v": version, "m": minute,
                 "s": json.dumps({"snapshot_id": sid, "version": version})},
            )


@pytest.fixture(scope="module")
def pre_url() -> Iterator[str]:
    """A container migrated to just before e2b6d4f8a1c3."""
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url, "c4e8a2f6b1d3")
        yield url


async def test_migration_renumbers_duplicates_then_versions_are_allocated_atomically(
    pre_url: str,
) -> None:
    tenant, agent = "snap-tenant", "agent-1"
    admin = create_async_engine(pre_url)  # container superuser: bypasses RLS
    async with admin.begin() as c:
        for sid, version, minute in (("s-a", 1, 0), ("s-b", 2, 1), ("s-c", 2, 2), ("s-d", 2, 3)):
            await c.execute(
                text(
                    "INSERT INTO agent_snapshots (id, tenant_id, agent_id, version, snapshot, "
                    "snapshotted_at) VALUES (:id, :t, :a, :v, CAST(:s AS jsonb), "
                    "now() + make_interval(mins => :m))"
                ),
                {"id": sid, "t": tenant, "a": agent, "v": version, "m": minute,
                 "s": json.dumps({"snapshot_id": sid, "version": version})},
            )
    await admin.dispose()

    alembic_upgrade(pre_url, "head")

    admin = create_async_engine(pre_url)
    assert await _rls_flags(admin) == (True, True), "RLS must be ENABLE + FORCE again"
    async with admin.begin() as c:
        rows = (
            await c.execute(
                text(
                    "SELECT id, version, (snapshot->>'version')::int FROM agent_snapshots "
                    "WHERE agent_id = :a ORDER BY id"
                ),
                {"a": agent},
            )
        ).all()
        assert [tuple(r) for r in rows] == [
            ("s-a", 1, 1), ("s-b", 2, 2), ("s-c", 3, 3), ("s-d", 4, 4)
        ]
        with pytest.raises(IntegrityError):
            async with c.begin_nested():
                await c.execute(
                    text(
                        "INSERT INTO agent_snapshots (id, tenant_id, agent_id, version) "
                        "VALUES ('dup', :t, :a, 4)"
                    ),
                    {"t": tenant, "a": agent},
                )
    await admin.dispose()

    app_eng = await app_role_engine(pre_url, ["agent_snapshots"])
    db = sessionmaker_for(app_eng)
    try:
        other = f"agent-{uuid.uuid4().hex[:6]}"
        snaps = [
            {"snapshot_id": uuid.uuid4().hex, "agent_id": other, "name": f"v{i}", "version": 0}
            for i in range(12)  # > the conflict retries: allocation must serialize
        ]
        versions = await asyncio.gather(*(_save_snapshot_to_db(s, db, tenant) for s in snaps))
        assert sorted(versions) == list(range(1, 13))
        loaded = await _load_snapshots_from_db(tenant, other, db)
        assert [s["version"] for s in loaded] == list(range(1, 13))
        # The JSON copy carries the allocated number too.
        assert {s["snapshot_id"]: s["version"] for s in loaded} == dict(
            zip([s["snapshot_id"] for s in snaps], versions, strict=True)
        )
        # Another tenant sees none of them (RLS + tenant predicate).
        assert await _load_snapshots_from_db("someone-else", other, db) == []
    finally:
        await app_eng.dispose()


@pytest.fixture(scope="module")
def owner_url() -> Iterator[str]:
    """A separate container at the revision before e2b6d4f8a1c3."""
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url, _BEFORE)
        yield url


async def test_migration_as_nosuperuser_owner_renumbers_and_restores_force_rls(
    owner_url: str,
) -> None:
    admin = create_async_engine(owner_url)
    # Two tenants' duplicates: FORCE RLS would hide all of them from the owner.
    await _insert_duplicates(admin, "tenant-one", "agent-x")
    await _insert_duplicates(admin, "tenant-two", "agent-y")
    async with admin.begin() as c:
        await c.execute(
            text(
                "CREATE ROLE snapshot_migrator LOGIN PASSWORD 'pw' NOSUPERUSER NOBYPASSRLS"
            )
        )
        await c.execute(text("GRANT USAGE ON SCHEMA public TO snapshot_migrator"))
        await c.execute(text("GRANT SELECT, UPDATE ON alembic_version TO snapshot_migrator"))
        await c.execute(text("ALTER TABLE agent_snapshots OWNER TO snapshot_migrator"))
    assert await _rls_flags(admin) == (True, True)

    migrator_url = (
        make_url(owner_url)
        .set(username="snapshot_migrator", password="pw")
        .render_as_string(hide_password=False)
    )
    env = {**os.environ, "DATABASE_URL": migrator_url, "ENVIRONMENT": "development"}
    for key in ("MIGRATION_DATABASE_URL", "APP_DB_USER", "APP_DB_PASSWORD"):
        env.pop(key, None)

    def _upgrade() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", _REVISION],
            cwd=BACKEND_ROOT, env=env, capture_output=True, text=True, check=False,
        )

    # Failure path: without CREATE on the schema the ADD CONSTRAINT (an index)
    # fails AFTER the NO FORCE + renumbering. The transaction rolls back: FORCE
    # is back, nothing was renumbered and the revision did not advance.
    failed = _upgrade()
    assert failed.returncode != 0
    assert await _rls_flags(admin) == (True, True), "a failed run must not leave FORCE lifted"
    async with admin.connect() as c:
        versions = sorted(
            (await c.execute(text("SELECT version FROM agent_snapshots"))).scalars().all()
        )
        revision = (await c.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    assert versions == [1, 1, 2, 2, 2, 2, 2, 2] and revision == _BEFORE

    # A schema-owner-like role (CREATE on the schema, still NOSUPERUSER/NOBYPASSRLS).
    async with admin.begin() as c:
        await c.execute(text("GRANT CREATE ON SCHEMA public TO snapshot_migrator"))
    result = _upgrade()
    assert result.returncode == 0, result.stderr[-3000:]

    try:
        assert await _rls_flags(admin) == (True, True), (
            "the owner-run migration must leave agent_snapshots ENABLE + FORCE RLS"
        )
        async with admin.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT agent_id, version, (snapshot->>'version')::int "
                        "FROM agent_snapshots ORDER BY agent_id, id"
                    )
                )
            ).all()
            constraint = (
                await c.execute(
                    text(
                        "SELECT 1 FROM pg_constraint "
                        "WHERE conname = 'uq_agent_snapshots_tenant_agent_version'"
                    )
                )
            ).first()
        # Both tenants' duplicates were seen through FORCE RLS and renumbered.
        assert [tuple(r) for r in rows] == [
            ("agent-x", 1, 1), ("agent-x", 2, 2), ("agent-x", 3, 3), ("agent-x", 4, 4),
            ("agent-y", 1, 1), ("agent-y", 2, 2), ("agent-y", 3, 3), ("agent-y", 4, 4),
        ]
        assert constraint is not None
    finally:
        await admin.dispose()
