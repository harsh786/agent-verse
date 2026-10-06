"""Real-Postgres, least-privilege proof for the sweep's server-error fixes.

Runs the goal-template store and the coordination store against the migrated
schema through a NOBYPASSRLS, non-owner role — the production API posture —
so FORCE'd RLS is enforced for real (the testcontainer superuser bypasses it).

* ``POST /templates`` used to 500 with "Could not refresh instance": the store
  committed, then refreshed outside the tenant transaction, where RLS hid the
  row it had just written.
* ``POST /coordination/v1/sessions/{id}/start|complete`` from a tenant that does
  not own the session raised an unmapped ``KeyError`` (500); the store must
  raise ``KeyError`` (→ 404) and leave the owner's session untouched.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/api/test_server_errors_rls_integration.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "server_errors_app"
GRANT_TABLES = (
    "goal_templates",
    "goal_template_tombstones",  # read by built-in seeding (a10-F230-01)
    "coordination_sessions",
    "coordination_events",
    "coordination_outbox",
)


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    try:
        from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency guard
        pytest.skip(f"testcontainers unavailable: {exc}")
    try:
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
    except subprocess.CalledProcessError:
        raise
    except Exception as exc:  # pragma: no cover - Docker not available
        pytest.skip(f"could not start Postgres testcontainer (Docker down?): {exc}")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def app_factory(postgres_url: str) -> AsyncIterator[Any]:
    """A session factory bound to a NOBYPASSRLS, non-owner, DML-only role."""
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
    app_url = (
        make_url(postgres_url)
        .set(username=APP_ROLE, password=password)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=4, max_overflow=0)
    factory = async_sessionmaker(app_engine, expire_on_commit=False)
    factory.admin = async_sessionmaker(admin_engine, expire_on_commit=False)  # type: ignore[attr-defined]
    yield factory
    await app_engine.dispose()
    await admin_engine.dispose()


async def _seed_tenant(app_factory: Any) -> SimpleNamespace:
    """Insert a tenants row (FK target of coordination_sessions) as the owner."""
    tenant_id = uuid.uuid4().hex
    async with app_factory.admin() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES (:id, 'Server Errors', :email, 'free', true)"
            ),
            {"id": tenant_id, "email": f"{tenant_id}@example.test"},
        )
    return SimpleNamespace(tenant_id=tenant_id)


# ── goal templates ────────────────────────────────────────────────────────────


async def test_template_crud_works_under_nobypassrls_role(app_factory: Any) -> None:
    from app.api.templates import _TemplateStore

    store = _TemplateStore(seed_builtins=False)
    store.set_db(app_factory)
    tenant_a = uuid.uuid4().hex  # hex form, as the API's tenant ids are
    tenant_b = uuid.uuid4().hex

    created = await store.create(
        tenant_id=tenant_a,
        name="Deploy",
        description="d",
        goal_text="Deploy {{svc}}",
        domain="devops",
        parameters=[{"name": "svc"}],
    )
    tid = created["id"]
    assert created["tenant_id"] == tenant_a and created["version"] == 1

    got = await store.get(tenant_a, tid)
    assert got is not None and got["name"] == "Deploy"

    updated = await store.update(
        tenant_id=tenant_a,
        template_id=tid,
        name="Deploy v2",
        description="d2",
        goal_text="Deploy {{svc}} to {{env}}",
        domain="devops",
        parameters=[{"name": "svc"}, {"name": "env"}],
    )
    assert updated is not None and updated["version"] == 2

    await store.increment_use_count(tenant_a, tid)
    after = await store.get(tenant_a, tid)
    assert after is not None and after["use_count"] == 1 and after["name"] == "Deploy v2"

    # Tenant B sees and changes nothing.
    assert await store.get(tenant_b, tid) is None
    assert [t["id"] for t in await store.list(tenant_b)] == []
    assert await store.delete(tenant_b, tid) is False
    await store.increment_use_count(tenant_b, tid)
    still = await store.get(tenant_a, tid)
    assert still is not None and still["use_count"] == 1

    assert [t["id"] for t in await store.list(tenant_a, domain="devops")] == [tid]
    assert await store.delete(tenant_a, tid) is True
    assert await store.get(tenant_a, tid) is None


async def test_template_builtin_seed_works_under_nobypassrls_role(app_factory: Any) -> None:
    from app.api.templates import _TemplateStore

    store = _TemplateStore(seed_builtins=True)
    store.set_db(app_factory)
    tenant = uuid.uuid4().hex
    listed = await store.list(tenant)
    assert len(listed) >= 14
    # Served from rows the seed really wrote: a fresh store (no memory) sees them.
    fresh = _TemplateStore(seed_builtins=False)
    fresh.set_db(app_factory)
    assert len(await fresh.list(tenant)) == len(listed)


# ── coordination sessions ─────────────────────────────────────────────────────


async def test_coordination_transition_on_foreign_session_is_keyerror(app_factory: Any) -> None:
    from app.coordination.store import CoordinationStore

    store = CoordinationStore(app_factory)
    tenant_a = await _seed_tenant(app_factory)
    tenant_b = await _seed_tenant(app_factory)

    created = await store.create_session(
        tenant_a,  # type: ignore[arg-type]
        civilization_id="civ",
        goal_id="goal",
        policy_snapshot={},
        budget_snapshot={},
    )
    sid = created.session_id

    with pytest.raises(KeyError):
        await store.get_session(tenant_b, session_id=sid)  # type: ignore[arg-type]
    for target in ("active", "completed"):
        with pytest.raises(KeyError):
            await store.transition_session(
                tenant_b,  # type: ignore[arg-type]
                session_id=sid,
                expected_version=1,
                target_state=target,
                idempotency_key=f"probe-{target}",
            )

    # The owner's session is untouched and still transitions from version 1.
    record = await store.get_session(tenant_a, session_id=sid)  # type: ignore[arg-type]
    assert record.state == "pending" and record.version == 1
    accepted = await store.transition_session(
        tenant_a,  # type: ignore[arg-type]
        session_id=sid,
        expected_version=1,
        target_state="active",
        idempotency_key="start-1",
    )
    assert accepted.state == "active" and accepted.version == 2
