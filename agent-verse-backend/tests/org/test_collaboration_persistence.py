"""Integration test for Task 2 (Situation Room UX): collaboration messages
persisted to ``org_events``.

Pins that ``OrgService.record_collaboration_event`` writes a real,
RLS-scoped ``org_events`` row that ``OrgService.list_events`` can read back
with the right ``entity_id`` and the full enriched payload -- the history
the Situation Room Team Channel and the per-agent audit trail (Task 4) rely
on. Exercises the real Postgres schema/migrations (not a fake session), so
this catches column/type mismatches a unit test with a fake recorder cannot.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/org/test_collaboration_persistence.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.db.rls import sqlalchemy_rls_context
from app.org.service import OrgService

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "test_app"
GRANT_TABLES = ("organizations", "org_events")


@pytest.fixture(scope="function")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": admin_url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield admin_url


def _app_url(admin_url: str, password: str) -> str:
    return (
        make_url(admin_url)
        .set(username=APP_ROLE, password=password)
        .render_as_string(hide_password=False)
    )


@pytest_asyncio.fixture(scope="function")
async def factories(postgres_url: str) -> AsyncIterator[tuple]:
    """Return (admin_factory, app_factory) for integration tests."""
    password = secrets.token_urlsafe(24)
    admin_engine = create_async_engine(postgres_url)
    async with admin_engine.begin() as conn:
        quoted = (
            await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})
        ).scalar_one()
        await conn.execute(
            text(
                f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        for tbl in GRANT_TABLES:
            await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO {APP_ROLE}"))

    admin_factory = async_sessionmaker(
        create_async_engine(postgres_url, echo=False), expire_on_commit=False
    )
    app_url = _app_url(postgres_url, password)
    app_engine = create_async_engine(app_url, pool_size=4, max_overflow=0, echo=False)
    app_factory = async_sessionmaker(app_engine, expire_on_commit=False)
    yield (admin_factory, app_factory)
    await app_engine.dispose()
    await admin_engine.dispose()


async def _seed_org(admin_factory, app_factory) -> dict:
    """Insert an organizations row (admin bypasses RLS) and return ids."""
    ids = {
        "org_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
    }
    async with admin_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO organizations (id, tenant_id, name, slug) "
                "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), :name, :slug)"
            ),
            {
                "id": ids["org_id"],
                "tid": ids["tenant_id"],
                "name": "Test Org",
                "slug": f"test-org-{uuid.uuid4().hex[:8]}",
            },
        )
    return {**ids, "app_factory": app_factory, "admin_factory": admin_factory}


@pytest.mark.asyncio
async def test_record_collaboration_event_then_list_events_roundtrip(factories: tuple) -> None:
    """A persisted collaboration message shows up via ``list_events`` with
    the right ``entity_id`` (the speaking agent) and the full enriched
    payload -- what the Situation Room history / per-agent audit trail
    query for."""
    admin_factory, app_factory = factories
    seeded = await _seed_org(admin_factory, app_factory)
    payload = {
        "lead": "alice",
        "message": "Shipping the integration by end of day.",
        "from_agent": "alice",
        "to": "team",
        "kind": "update",
        "latency_ms": 42,
        "tokens": 17,
        "cost_usd": 0.0025,
        "mission_id": None,
    }
    try:
        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, seeded["tenant_id"]):
                svc = OrgService(session=session, tenant_id=seeded["tenant_id"])
                await svc.record_collaboration_event(
                    seeded["org_id"],
                    from_agent="alice",
                    kind="update",
                    message=payload["message"],
                    payload=payload,
                )

        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, seeded["tenant_id"]):
                svc = OrgService(session=session, tenant_id=seeded["tenant_id"])
                rows = await svc.list_events(
                    seeded["org_id"], event_type="org.collaboration.message"
                )

        assert len(rows) == 1
        row = rows[0]
        assert row.entity_type == "agent"
        assert row.entity_id == "alice"
        assert row.actor_id == "alice"
        assert row.source == "collaboration"
        assert row.severity == "info"
        assert row.title == "alice · update"
        assert row.payload == payload
    finally:
        async with admin_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM org_events WHERE org_id = CAST(:id AS uuid)"),
                {"id": seeded["org_id"]},
            )
            await s.execute(
                text("DELETE FROM organizations WHERE id = CAST(:id AS uuid)"),
                {"id": seeded["org_id"]},
            )


@pytest.mark.asyncio
async def test_record_collaboration_event_multiple_messages_all_queryable(
    factories: tuple,
) -> None:
    """Multiple collaboration messages across different agents all persist
    and are queryable by event_type, each with its own entity_id."""
    admin_factory, app_factory = factories
    seeded = await _seed_org(admin_factory, app_factory)
    try:
        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, seeded["tenant_id"]):
                svc = OrgService(session=session, tenant_id=seeded["tenant_id"])
                for lead in ("alice", "bob", "carol"):
                    await svc.record_collaboration_event(
                        seeded["org_id"],
                        from_agent=lead,
                        kind="update",
                        message=f"{lead} status update",
                        payload={"from_agent": lead, "message": f"{lead} status update"},
                    )

        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, seeded["tenant_id"]):
                svc = OrgService(session=session, tenant_id=seeded["tenant_id"])
                rows = await svc.list_events(
                    seeded["org_id"], event_type="org.collaboration.message"
                )

        assert {row.entity_id for row in rows} == {"alice", "bob", "carol"}
    finally:
        async with admin_factory() as s, s.begin():
            await s.execute(
                text("DELETE FROM org_events WHERE org_id = CAST(:id AS uuid)"),
                {"id": seeded["org_id"]},
            )
            await s.execute(
                text("DELETE FROM organizations WHERE id = CAST(:id AS uuid)"),
                {"id": seeded["org_id"]},
            )
