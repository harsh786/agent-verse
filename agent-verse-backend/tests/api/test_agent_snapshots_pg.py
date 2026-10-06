"""a10-F236-01/04 on real Postgres: agent snapshot versions are unique and allocated
in the INSERT.

* concurrent snapshots of one agent, as the least-privilege (NOBYPASSRLS) app
  role, get distinct versions 1..N (they used to be ``len(existing) + 1`` from a
  separate read, so racing snapshots shared a number);
* migration e2b6d4f8a1c3 renumbers pre-existing duplicates (earliest keeps its
  number, the JSON ``version`` follows) and adds the unique constraint.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/api/test_agent_snapshots_pg.py -q -m integration
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.agents import _load_snapshots_from_db, _save_snapshot_to_db
from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


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
            for i in range(6)
        ]
        versions = await asyncio.gather(*(_save_snapshot_to_db(s, db, tenant) for s in snaps))
        assert sorted(versions) == [1, 2, 3, 4, 5, 6]
        loaded = await _load_snapshots_from_db(tenant, other, db)
        assert [s["version"] for s in loaded] == [1, 2, 3, 4, 5, 6]
        # The JSON copy carries the allocated number too.
        assert {s["snapshot_id"]: s["version"] for s in loaded} == dict(
            zip([s["snapshot_id"] for s in snaps], versions, strict=True)
        )
        # Another tenant sees none of them (RLS + tenant predicate).
        assert await _load_snapshots_from_db("someone-else", other, db) == []
    finally:
        await app_eng.dispose()
