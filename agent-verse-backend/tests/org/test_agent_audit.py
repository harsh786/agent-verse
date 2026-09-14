"""Integration test for Task 4 (Situation Room UX): per-agent audit trail.

Pins that ``OrgService.get_agent_audit`` assembles a unified, newest-first
trail for one agent by unioning ``org_events`` (entity_id), ``org_decisions``
(actor_agent_id), and ``org_tasks`` (owner_agent_id / assigned_agent_ids) --
all scoped by tenant_id + org_id -- and that the ``entity_id`` filter added
to ``list_events`` returns only matching rows. Also pins org isolation: an
identical agent-id string in a second org must never leak into the first
org's audit. Exercises the real Postgres schema/migrations (not a fake
session), mirroring ``tests/org/test_collaboration_persistence.py``.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/org/test_agent_audit.py -q -m integration
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
GRANT_TABLES = ("organizations", "org_events", "org_decisions", "org_tasks")


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


async def _seed_agent_activity(
    app_factory, tenant_id: str, org_id: str, agent_id: str
) -> None:
    """Seed one collaboration event, one decision, and one task for agent_id."""
    async with app_factory() as session, session.begin():
        async with sqlalchemy_rls_context(session, tenant_id):
            svc = OrgService(session=session, tenant_id=tenant_id)
            await svc.record_collaboration_event(
                org_id,
                from_agent=agent_id,
                kind="update",
                message=f"{agent_id} status update",
                payload={
                    "from_agent": agent_id,
                    "message": f"{agent_id} status update",
                    "cost_usd": 0.01,
                    "latency_ms": 123,
                    "mission_id": "mission-xyz",
                },
            )
            await svc.record_decision(
                org_id=org_id,
                entity_type="agent",
                entity_id=agent_id,
                decision_type="tool_call_approved",
                description=f"{agent_id} decision description",
                why="because reasons",
                cost_estimate_usd=0.5,
                actor_agent_id=agent_id,
            )
            await svc.create_task(
                org_id=org_id,
                title=f"{agent_id} task",
                owner_agent_id=agent_id,
                cost_estimate_usd=1.5,
            )


async def _cleanup(admin_factory, org_id: str) -> None:
    async with admin_factory() as s, s.begin():
        await s.execute(
            text("DELETE FROM org_tasks WHERE org_id = CAST(:id AS uuid)"),
            {"id": org_id},
        )
        await s.execute(
            text("DELETE FROM org_decisions WHERE org_id = CAST(:id AS uuid)"),
            {"id": org_id},
        )
        await s.execute(
            text("DELETE FROM org_events WHERE org_id = CAST(:id AS uuid)"),
            {"id": org_id},
        )
        await s.execute(
            text("DELETE FROM organizations WHERE id = CAST(:id AS uuid)"),
            {"id": org_id},
        )


@pytest.mark.asyncio
async def test_get_agent_audit_unions_and_excludes_other_agent(factories: tuple) -> None:
    """get_agent_audit(A) returns only A's 3 items, unified + newest-first,
    with the right kind/cost_usd/mission_id mapping, and excludes B's."""
    admin_factory, app_factory = factories
    seeded = await _seed_org(admin_factory, app_factory)
    org_id = seeded["org_id"]
    tenant_id = seeded["tenant_id"]
    try:
        await _seed_agent_activity(app_factory, tenant_id, org_id, "agent-a")
        await _seed_agent_activity(app_factory, tenant_id, org_id, "agent-b")

        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                svc = OrgService(session=session, tenant_id=tenant_id)
                audit = await svc.get_agent_audit(org_id, "agent-a")

        assert len(audit) == 3
        kinds = {e["kind"] for e in audit}
        assert kinds == {"message", "decision", "task"}

        # newest-first: 'at' timestamps must be non-increasing
        ats = [e["at"] for e in audit]
        assert ats == sorted(ats, reverse=True)

        message_entry = next(e for e in audit if e["kind"] == "message")
        assert message_entry["cost_usd"] == 0.01
        assert message_entry["duration_ms"] == 123
        assert message_entry["mission_id"] == "mission-xyz"
        assert message_entry["ref"]["table"] == "org_events"

        decision_entry = next(e for e in audit if e["kind"] == "decision")
        assert decision_entry["cost_usd"] == 0.5
        assert decision_entry["title"] == "tool_call_approved"
        assert decision_entry["ref"]["table"] == "org_decisions"

        task_entry = next(e for e in audit if e["kind"] == "task")
        assert task_entry["cost_usd"] == 1.5
        assert task_entry["title"] == "agent-a task"
        assert task_entry["ref"]["table"] == "org_tasks"

        # None of agent-b's activity leaks in.
        for e in audit:
            assert "agent-b" not in (e["title"] or "")
    finally:
        await _cleanup(admin_factory, org_id)


@pytest.mark.asyncio
async def test_get_agent_audit_includes_task_via_assigned_agent_ids(factories: tuple) -> None:
    """A task where the agent is only in assigned_agent_ids (not owner)
    still shows up in that agent's audit."""
    admin_factory, app_factory = factories
    seeded = await _seed_org(admin_factory, app_factory)
    org_id = seeded["org_id"]
    tenant_id = seeded["tenant_id"]
    try:
        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                svc = OrgService(session=session, tenant_id=tenant_id)
                await svc.create_task(
                    org_id=org_id,
                    title="shared task",
                    owner_agent_id="owner-agent",
                    assigned_agent_ids=["helper-agent"],
                )

        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                svc = OrgService(session=session, tenant_id=tenant_id)
                audit = await svc.get_agent_audit(org_id, "helper-agent")

        assert len(audit) == 1
        assert audit[0]["kind"] == "task"
        assert audit[0]["title"] == "shared task"
    finally:
        await _cleanup(admin_factory, org_id)


@pytest.mark.asyncio
async def test_list_events_entity_id_filter(factories: tuple) -> None:
    """list_events(entity_id=...) returns only matching rows."""
    admin_factory, app_factory = factories
    seeded = await _seed_org(admin_factory, app_factory)
    org_id = seeded["org_id"]
    tenant_id = seeded["tenant_id"]
    try:
        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                svc = OrgService(session=session, tenant_id=tenant_id)
                for agent in ("agent-a", "agent-b"):
                    await svc.record_collaboration_event(
                        org_id,
                        from_agent=agent,
                        kind="update",
                        message=f"{agent} update",
                        payload={"from_agent": agent},
                    )

        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                svc = OrgService(session=session, tenant_id=tenant_id)
                rows = await svc.list_events(org_id, entity_id="agent-a")

        assert len(rows) == 1
        assert rows[0].entity_id == "agent-a"
    finally:
        await _cleanup(admin_factory, org_id)


@pytest.mark.asyncio
async def test_get_agent_audit_org_isolation(factories: tuple) -> None:
    """An identical agent-id string in a second org must not leak into the
    first org's audit -- every subquery scopes by org_id (and tenant_id)."""
    admin_factory, app_factory = factories
    seeded_1 = await _seed_org(admin_factory, app_factory)
    seeded_2 = await _seed_org(admin_factory, app_factory)
    org_id_1 = seeded_1["org_id"]
    org_id_2 = seeded_2["org_id"]
    tenant_id_1 = seeded_1["tenant_id"]
    tenant_id_2 = seeded_2["tenant_id"]
    shared_agent_id = "agent-shared"
    try:
        await _seed_agent_activity(app_factory, tenant_id_1, org_id_1, shared_agent_id)
        await _seed_agent_activity(app_factory, tenant_id_2, org_id_2, shared_agent_id)

        async with app_factory() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id_1):
                svc = OrgService(session=session, tenant_id=tenant_id_1)
                audit_1 = await svc.get_agent_audit(org_id_1, shared_agent_id)

        # Only org_1's 3 items -- if org_2's identical agent-id activity
        # leaked in, this would be 6 (each seed call creates fresh
        # uuid7-keyed rows, so a leak inflates the count, not just
        # duplicates content).
        assert len(audit_1) == 3
        assert {e["kind"] for e in audit_1} == {"message", "decision", "task"}
    finally:
        await _cleanup(admin_factory, org_id_1)
        await _cleanup(admin_factory, org_id_2)
