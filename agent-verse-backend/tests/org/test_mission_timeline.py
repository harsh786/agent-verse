"""Integration test for Task 5 (Situation Room UX): mission timeline endpoint.

Pins that ``OrgService.get_mission_timeline`` derives an ordered phase ribbon
(Gantt data) for a mission from ``OrgMission.created_at/started_at/completed_at``
plus its lifecycle events in ``org_events``.

Ground truth verified in ``app/org/service.py`` before writing this test:
- ``mission.created`` (``create_mission``) and the ``mission.{planned,started,
  completed,failed,...}`` events emitted by ``update_mission_status`` (status
  ``active`` maps to event action ``started``) set
  ``entity_type="mission"``, ``entity_id=str(mission.id)`` -- found via
  ``list_events(entity_id=mission_id)``.
- ``team.formed`` (``form_team_and_dispatch``) sets ``entity_type="team"``,
  ``entity_id=<team_id>`` INSTEAD -- the mission id only appears in
  ``payload["mission_id"]``. ``get_mission_timeline`` must (and does) filter
  for it via a JSONB ``payload->>'mission_id'`` match, not ``entity_id``.

Exercises the real Postgres schema/migrations (not a fake session), mirroring
``tests/org/test_agent_audit.py``.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/org/test_mission_timeline.py -v -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
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
GRANT_TABLES = ("organizations", "org_events", "org_missions", "org_teams")


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


async def _cleanup(admin_factory, org_id: str) -> None:
    async with admin_factory() as s, s.begin():
        await s.execute(
            text("DELETE FROM org_events WHERE org_id = CAST(:id AS uuid)"),
            {"id": org_id},
        )
        await s.execute(
            text("DELETE FROM org_missions WHERE org_id = CAST(:id AS uuid)"),
            {"id": org_id},
        )
        await s.execute(
            text("DELETE FROM org_teams WHERE org_id = CAST(:id AS uuid)"),
            {"id": org_id},
        )
        await s.execute(
            text("DELETE FROM organizations WHERE id = CAST(:id AS uuid)"),
            {"id": org_id},
        )


@pytest.mark.asyncio
async def test_get_mission_timeline_orders_phases_with_durations(factories: tuple) -> None:
    """Seed a mission through its real lifecycle (created -> planned ->
    team.formed -> active/started -> completed) and assert the derived
    ribbon is ordered, each phase's duration is computed from the next
    phase's ``at``, and ``total_ms`` reflects started_at -> completed_at."""
    admin_factory, app_factory = factories
    seeded = await _seed_org(admin_factory, app_factory)
    org_id = seeded["org_id"]
    tenant_id = seeded["tenant_id"]
    try:
        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                svc = OrgService(session=session, tenant_id=tenant_id)
                mission = await svc.create_mission(
                    org_id=org_id,
                    title="Launch the widget",
                    objective="Ship it",
                )
                mission_id = str(mission.id)
                await svc.update_mission_status(mission_id, "planned")
                team = await svc.create_team(org_id=org_id, name="Widget Squad")
                # Mirror the real emit in form_team_and_dispatch exactly: entity_type
                # "team" / entity_id=team_id, mission id ONLY in payload.
                await svc._emit_event(
                    uuid.UUID(org_id),
                    "team.formed",
                    title=f"Team formed for '{mission.title}'",
                    entity_type="team",
                    entity_id=str(team.id),
                    payload={"team_id": str(team.id), "mission_id": mission_id},
                    source="orchestrator",
                )
                await svc.update_mission_status(mission_id, "active")
                await svc.update_mission_status(mission_id, "completed")

        # All the writes above happened inside one Postgres transaction, and
        # `now()` is constant for the lifetime of a transaction -- so every
        # `created_at` would otherwise collapse to the same instant. Backdate
        # each row to distinct, well-separated timestamps (via a fresh admin
        # transaction, bypassing OrgService/_emit_event) so duration_ms/at
        # ordering assertions are meaningful, not accidentally-zero.
        base = datetime(2026, 1, 1, tzinfo=UTC)
        t_created = base
        t_planned = base + timedelta(minutes=1)
        t_started = base + timedelta(minutes=2)
        t_completed = base + timedelta(minutes=4)
        async with admin_factory() as s, s.begin():
            await s.execute(
                text(
                    "UPDATE org_missions SET created_at = :t_created, "
                    "started_at = :t_started, completed_at = :t_completed "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {
                    "t_created": t_created,
                    "t_started": t_started,
                    "t_completed": t_completed,
                    "id": mission_id,
                },
            )
            for event_type, ts in (
                ("mission.created", t_created),
                ("mission.planned", t_planned),
                ("team.formed", t_planned),
                ("mission.started", t_started),
                ("mission.completed", t_completed),
            ):
                await s.execute(
                    text(
                        "UPDATE org_events SET created_at = :ts "
                        "WHERE org_id = CAST(:org_id AS uuid) AND event_type = :et"
                    ),
                    {"ts": ts, "org_id": org_id, "et": event_type},
                )

        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                svc = OrgService(session=session, tenant_id=tenant_id)
                timeline = await svc.get_mission_timeline(org_id, mission_id)

        assert timeline is not None
        assert timeline["mission_id"] == mission_id
        assert timeline["status"] == "completed"

        names = [p["name"] for p in timeline["phases"]]
        assert names == ["created", "planning", "executing", "done"]

        assert timeline["phases"][0]["at"] == t_created.isoformat()
        assert timeline["phases"][1]["at"] == t_planned.isoformat()
        assert timeline["phases"][2]["at"] == t_started.isoformat()
        assert timeline["phases"][3]["at"] == t_completed.isoformat()

        # Ordered strictly ascending by 'at' now that timestamps are distinct.
        ats = [p["at"] for p in timeline["phases"]]
        assert ats == sorted(ats)
        assert len(set(ats)) == len(ats)

        # Every non-terminal phase's until/duration_ms is the next phase's 'at'.
        assert timeline["phases"][0]["until"] == t_planned.isoformat()
        assert timeline["phases"][0]["duration_ms"] == 60_000
        assert timeline["phases"][1]["until"] == t_started.isoformat()
        assert timeline["phases"][1]["duration_ms"] == 60_000
        assert timeline["phases"][2]["until"] == t_completed.isoformat()
        assert timeline["phases"][2]["duration_ms"] == 120_000

        # The terminal 'done' phase is a point in time -- no further phase follows it.
        assert timeline["phases"][-1]["name"] == "done"
        assert timeline["phases"][-1]["until"] is None
        assert timeline["phases"][-1]["duration_ms"] is None

        # total_ms = completed_at - started_at.
        assert timeline["total_ms"] == 120_000
    finally:
        await _cleanup(admin_factory, org_id)


@pytest.mark.asyncio
async def test_get_mission_timeline_cross_org_404(factories: tuple) -> None:
    """A mission belonging to org O2 must not be returned via org O's id --
    ``get_mission`` is tenant-scoped only, so the service must additionally
    verify ``str(mission.org_id) == org_id`` itself."""
    admin_factory, app_factory = factories
    seeded_o1 = await _seed_org(admin_factory, app_factory)
    seeded_o2 = await _seed_org(admin_factory, app_factory)
    org_id_1 = seeded_o1["org_id"]
    org_id_2 = seeded_o2["org_id"]
    # Same tenant for both orgs, so a tenant-only scope on get_mission would
    # otherwise let this leak across the org boundary.
    tenant_id = seeded_o1["tenant_id"]
    try:
        async with admin_factory() as s, s.begin():
            await s.execute(
                text("UPDATE organizations SET tenant_id = CAST(:tid AS uuid) WHERE id = CAST(:id AS uuid)"),
                {"tid": tenant_id, "id": org_id_2},
            )

        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                svc = OrgService(session=session, tenant_id=tenant_id)
                mission_o2 = await svc.create_mission(org_id=org_id_2, title="Org 2's mission")
                mission_id = str(mission_o2.id)

        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                svc = OrgService(session=session, tenant_id=tenant_id)
                timeline = await svc.get_mission_timeline(org_id_1, mission_id)

        assert timeline is None
    finally:
        await _cleanup(admin_factory, org_id_1)
        await _cleanup(admin_factory, org_id_2)


@pytest.mark.asyncio
async def test_get_mission_timeline_no_events_falls_back_to_columns(factories: tuple) -> None:
    """A mission with no lifecycle events beyond creation must not crash --
    it still returns a minimal ribbon derived purely from
    created_at/started_at/completed_at."""
    admin_factory, app_factory = factories
    seeded = await _seed_org(admin_factory, app_factory)
    org_id = seeded["org_id"]
    tenant_id = seeded["tenant_id"]
    try:
        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                svc = OrgService(session=session, tenant_id=tenant_id)
                mission = await svc.create_mission(org_id=org_id, title="Quiet mission")
                mission_id = str(mission.id)

        # Simulate started_at/completed_at being set with NO corresponding
        # lifecycle events (e.g. a legacy row) via a raw admin update that
        # bypasses OrgService/_emit_event entirely.
        async with admin_factory() as s, s.begin():
            await s.execute(
                text(
                    "UPDATE org_missions SET started_at = now(), "
                    "completed_at = now() + interval '5 minutes' "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"id": mission_id},
            )

        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                svc = OrgService(session=session, tenant_id=tenant_id)
                timeline = await svc.get_mission_timeline(org_id, mission_id)

        assert timeline is not None
        names = [p["name"] for p in timeline["phases"]]
        # "planning" has no evidence at all here (no team.formed/task.decomposed/
        # planned-status event) so it must be omitted, not fabricated.
        assert names == ["created", "executing", "done"]
        assert timeline["total_ms"] is not None
        assert timeline["total_ms"] > 0
    finally:
        await _cleanup(admin_factory, org_id)
