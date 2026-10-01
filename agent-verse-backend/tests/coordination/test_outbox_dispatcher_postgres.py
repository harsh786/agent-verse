"""COORD-OUTBOX integration: outbox rows go pending -> published (or retry/dead-letter).

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/coordination/test_outbox_dispatcher_postgres.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import fakeredis
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.coordination.outbox_dispatcher import CoordinationOutboxDispatcher
from app.coordination.store import CoordinationStore
from app.coordination.streams import CoordinationStreams
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TENANTS = ("tenant-outbox-a", "tenant-outbox-b")


@pytest.fixture(scope="module")
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


@pytest_asyncio.fixture
async def factories(postgres_url: str) -> AsyncIterator[tuple[Any, Any]]:
    """(NOBYPASSRLS application factory, superuser 'maintenance' factory)."""
    password = secrets.token_urlsafe(24)
    role = f"test_app_outbox_{secrets.token_hex(4)}"
    admin_engine = create_async_engine(postgres_url)
    async with admin_engine.begin() as conn:
        quoted = (
            await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})
        ).scalar_one()
        await conn.execute(
            text(
                f"CREATE ROLE {role} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {role}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        await conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
        )
        for tenant in TENANTS:
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :id, :email, 'free', true) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": tenant, "email": f"{tenant}@example.test"},
            )
        await conn.execute(text("DELETE FROM coordination_dead_letters"))
        await conn.execute(text("DELETE FROM coordination_outbox"))
    app_url = (
        make_url(postgres_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=4, max_overflow=0)
    yield (
        async_sessionmaker(app_engine, expire_on_commit=False),
        async_sessionmaker(admin_engine, expire_on_commit=False),
    )
    await app_engine.dispose()
    await admin_engine.dispose()


async def _transition(factory: Any, tenant: str) -> str:
    ctx = TenantContext(tenant_id=tenant, plan=PlanTier.FREE, api_key_id="k")
    store = CoordinationStore(factory)
    session = await store.create_session(
        ctx, civilization_id="civ", goal_id="goal", policy_snapshot={}, budget_snapshot={}
    )
    await store.transition_session(
        ctx,
        session_id=session.session_id,
        expected_version=1,
        target_state="active",
        idempotency_key="start",
    )
    return session.session_id


async def _states(admin: Any) -> dict[str, tuple[str, int]]:
    async with admin() as db:
        rows = await db.execute(
            text("SELECT tenant_id, state, attempt_count FROM coordination_outbox")
        )
        return {str(r[0]): (str(r[1]), int(r[2])) for r in rows.all()}


@pytest.mark.asyncio
async def test_outbox_rows_go_pending_to_published_for_every_tenant(factories: Any) -> None:
    app_factory, admin = factories
    sessions = {tenant: await _transition(app_factory, tenant) for tenant in TENANTS}
    assert {state for state, _ in (await _states(admin)).values()} == {"pending"}
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    dispatcher = CoordinationOutboxDispatcher(
        session_factory=app_factory,
        system_session_factory=admin,
        publisher=lambda: CoordinationStreams(redis),
    )
    result = await dispatcher.dispatch_once()
    assert result["delivered"] == 2 and result["tenants"] == 2
    assert {state for state, _ in (await _states(admin)).values()} == {"published"}
    for tenant, session_id in sessions.items():
        entries = await redis.xrange(CoordinationStreams.stream_name(tenant, session_id))
        envelope = CoordinationStreams.decode(entries[0][1])
        assert envelope["event_type"] == "session.state_changed"
        assert envelope["tenant_id"] == tenant
    # Nothing due any more: a second tick delivers nothing (no duplicate publish).
    assert (await dispatcher.dispatch_once())["delivered"] == 0


class _BrokenStreams:
    async def publish(self, tenant_id: str, session_id: str, envelope: dict[str, Any]) -> str:
        raise ConnectionError("stream down")


@pytest.mark.asyncio
async def test_failed_publish_backs_off_then_dead_letters(factories: Any) -> None:
    app_factory, admin = factories
    await _transition(app_factory, TENANTS[0])
    dispatcher = CoordinationOutboxDispatcher(
        session_factory=app_factory,
        system_session_factory=admin,
        publisher=lambda: _BrokenStreams(),
        max_attempts=2,
    )
    assert (await dispatcher.dispatch_once())["delivered"] == 0
    state, attempts = (await _states(admin))[TENANTS[0]]
    assert (state, attempts) == ("pending", 1)
    # Backoff: not due yet, so the next tick does not touch it.
    await dispatcher.dispatch_once()
    assert (await _states(admin))[TENANTS[0]] == ("pending", 1)
    async with admin() as db, db.begin():
        await db.execute(
            text("UPDATE coordination_outbox SET available_at = now() - interval '1 day'")
        )
    await dispatcher.dispatch_once()
    assert (await _states(admin))[TENANTS[0]][0] == "dead_letter"
    async with admin() as db:
        dead = (
            await db.execute(text("SELECT count(*) FROM coordination_dead_letters"))
        ).scalar_one()
    assert dead == 1


@pytest.mark.asyncio
async def test_no_transport_claims_nothing(factories: Any) -> None:
    app_factory, admin = factories
    await _transition(app_factory, TENANTS[1])
    dispatcher = CoordinationOutboxDispatcher(
        session_factory=app_factory, system_session_factory=admin, publisher=lambda: None
    )
    assert (await dispatcher.dispatch_once())["status"] == "skipped"
    assert (await _states(admin))[TENANTS[1]] == ("pending", 0)
