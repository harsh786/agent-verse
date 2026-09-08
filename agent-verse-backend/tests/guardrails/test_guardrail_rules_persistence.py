"""Behavioral persistence + RLS proof for guardrail rules (P1-4, Task 0.4).

Stands up a real Postgres via testcontainers, runs ``alembic upgrade head``
(which creates the 0113 ``guardrail_rules`` table + RLS policy), and connects as
a **non-superuser, non-owner** role that RLS genuinely applies to. It proves:

1. Rules added to one engine + flushed to the repo are still present when a
   *fresh* engine loads from the same repo (survive a restart).
2. RLS scopes rules per tenant: under tenant A's GUC only A's rows are visible,
   even for a bare ``SELECT`` with no WHERE clause.

Run with::

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/guardrails/test_guardrail_rules_persistence.py -q
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.rls import sqlalchemy_rls_context
from app.guardrails_v2.engine import GuardrailsEngine
from app.guardrails_v2.repository import PostgresGuardrailRuleRepository

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]

APP_ROLE = "gr_app_role"
APP_PASSWORD = "gr-app-role-password"
TENANT_A = "tenant-a-guardrails"
TENANT_B = "tenant-b-guardrails"


def _alembic(database_url: str, *arguments: str) -> None:
    subprocess.run(
        ["alembic", *arguments],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        check=True,
        capture_output=True,
        text=True,
    )


def _asyncpg_dsn(sqlalchemy_url: str) -> str:
    return (
        make_url(sqlalchemy_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )


def _app_role_url(admin_url: str) -> str:
    return (
        make_url(admin_url)
        .set(username=APP_ROLE, password=APP_PASSWORD)
        .render_as_string(hide_password=False)
    )


async def _provision(admin_url: str) -> None:
    conn = await asyncpg.connect(_asyncpg_dsn(admin_url))
    try:
        await conn.execute(
            f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
        )
        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
        await conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
        )
        await conn.execute(
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"
        )
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def app_url() -> Iterator[str]:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        _alembic(admin_url, "upgrade", "head")
        asyncio.run(_provision(admin_url))
        yield _app_role_url(admin_url)


def _sessionmaker(url: str) -> tuple[async_sessionmaker, object]:
    engine = create_async_engine(url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def test_rules_survive_engine_restart(app_url: str) -> None:
    sessions, engine = _sessionmaker(app_url)
    try:
        repo = PostgresGuardrailRuleRepository(sessions)

        # Engine #1: seed defaults for tenant A, flush to the repo.
        e1 = GuardrailsEngine()
        e1.bind_repository(repo, auto_persist=False)
        seeded = e1.ensure_default_rules(TENANT_A)
        assert seeded > 0
        flushed = await e1.flush()
        assert flushed == seeded

        # Engine #2: a fresh process — load from the same repo.
        e2 = GuardrailsEngine()
        e2.bind_repository(repo, auto_persist=False)
        loaded = await e2.load_from_repo(TENANT_A)
        assert loaded == seeded

        reloaded_ids = {r.rule_id for r in e2.get_rules(TENANT_A)}
        original_ids = {r.rule_id for r in e1.get_rules(TENANT_A)}
        assert reloaded_ids == original_ids
        # A blocking rule round-tripped intact.
        assert any(r.action.value == "block" for r in e2.get_rules(TENANT_A))
    finally:
        await engine.dispose()


async def test_rls_scopes_rules_per_tenant(app_url: str) -> None:
    sessions, engine = _sessionmaker(app_url)
    try:
        repo = PostgresGuardrailRuleRepository(sessions)

        e = GuardrailsEngine()
        e.bind_repository(repo, auto_persist=False)
        e.ensure_default_rules(TENANT_A)  # idempotent re-seed (already present)
        count_b = e.ensure_default_rules(TENANT_B)
        await e.flush()
        assert count_b > 0  # B is new in this test

        # repo.load scopes to the requested tenant.
        a_rules = await repo.load(TENANT_A)
        b_rules = await repo.load(TENANT_B)
        assert a_rules and all(r.tenant_id == TENANT_A for r in a_rules)
        assert b_rules and all(r.tenant_id == TENANT_B for r in b_rules)

        # RLS proof: a bare SELECT under tenant A's GUC sees ONLY A's rows.
        async with sessions() as db, db.begin(), sqlalchemy_rls_context(db, TENANT_A):
            visible = (
                await db.execute(text("SELECT tenant_id FROM guardrail_rules"))
            ).scalars().all()
        assert visible, "tenant A must see its own rows"
        assert set(visible) == {TENANT_A}, f"RLS leak: {set(visible)}"
    finally:
        await engine.dispose()
