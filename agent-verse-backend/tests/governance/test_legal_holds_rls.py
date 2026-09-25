"""Integration test: a legal hold must actually block deletion.

`legal_holds` is FORCE ROW LEVEL SECURITY, and `LegalHoldManager` opened plain
sessions with no RLS context on all five of its DB paths. Under a real
least-privilege role that means:

  * `create_hold`'s INSERT is rejected, the error is swallowed by
    `except Exception: logger.error(...)`, and the method still returns a
    hold dict — so the hold looks created but was never persisted;
  * `is_under_hold`'s `SELECT 1 FROM legal_holds ...` matches zero rows, so it
    returns **False** — "not on hold". The delete gate therefore lets deletion
    proceed on data under legal hold, which is precisely what a legal hold
    exists to prevent. Every path out of that method fails open (the missing
    row, the exception handler, and the final `return False`).

An earlier session fixed this method to fall back to the DB rather than trust an
incomplete Redis cache. That fallback was itself blocked by RLS, so the fix did
not change the outcome.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/governance/test_legal_holds_rls.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.governance.legal_holds import LegalHoldManager

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TENANT_A = "tenant-hold-a"
TENANT_B = "tenant-hold-b"
RESOURCE = "goal-under-litigation"


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


@pytest_asyncio.fixture(scope="function")
async def factories(postgres_url: str) -> AsyncIterator[tuple]:
    password = secrets.token_urlsafe(24)
    role = f"test_app_hold_{secrets.token_hex(4)}"
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
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON legal_holds TO {role}")
        )
        await conn.execute(text("DELETE FROM legal_holds WHERE tenant_id = ANY(:t)"),
                           {"t": [TENANT_A, TENANT_B]})

    app_url = (
        make_url(postgres_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=4, max_overflow=0)
    yield (
        async_sessionmaker(admin_engine, expire_on_commit=False),
        async_sessionmaker(app_engine, expire_on_commit=False),
    )
    await app_engine.dispose()
    await admin_engine.dispose()


@pytest.mark.asyncio
async def test_a_created_hold_is_persisted_and_blocks(factories: tuple) -> None:
    admin_factory, app_factory = factories
    # redis=None forces the DB path, which is the one that must be correct.
    mgr = LegalHoldManager(redis=None, db_factory=app_factory)

    await mgr.create_hold(
        tenant_id=TENANT_A,
        name="Matter 42",
        resource_type="goal",
        resource_ids=[RESOURCE],
        legal_matter_id="M-42",
    )

    async with admin_factory() as s:
        rows = (
            await s.execute(
                text("SELECT id, status FROM legal_holds WHERE tenant_id = :t"),
                {"t": TENANT_A},
            )
        ).fetchall()
    assert len(rows) == 1, f"hold was never persisted: {rows}"
    assert rows[0][1] == "active"

    assert await mgr.is_under_hold(TENANT_A, RESOURCE) is True, (
        "resource under an active legal hold reported as NOT held — the delete "
        "gate would let deletion proceed"
    )


@pytest.mark.asyncio
async def test_a_hold_is_tenant_scoped(factories: tuple) -> None:
    _admin, app_factory = factories
    mgr = LegalHoldManager(redis=None, db_factory=app_factory)
    await mgr.create_hold(
        tenant_id=TENANT_A,
        name="Matter 43",
        resource_type="goal",
        resource_ids=[RESOURCE],
    )
    assert await mgr.is_under_hold(TENANT_A, RESOURCE) is True
    assert await mgr.is_under_hold(TENANT_B, RESOURCE) is False


@pytest.mark.asyncio
async def test_released_hold_no_longer_blocks_and_listing_works(
    factories: tuple,
) -> None:
    _admin, app_factory = factories
    mgr = LegalHoldManager(redis=None, db_factory=app_factory)
    hold = await mgr.create_hold(
        tenant_id=TENANT_A,
        name="Matter 44",
        resource_type="goal",
        resource_ids=[RESOURCE],
    )
    listed = await mgr.list_holds(TENANT_A)
    assert len(listed) == 1, f"list_holds saw nothing under RLS: {listed}"

    await mgr.release_hold(TENANT_A, str(hold["id"]))
    assert await mgr.is_under_hold(TENANT_A, RESOURCE) is False
    assert await mgr.list_holds(TENANT_A, status="active") == []
