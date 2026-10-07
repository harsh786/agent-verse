"""a10-F251-01/02: b3e7d1f9a5c2 drops routing_decisions / routing_outcomes, refuses
while they hold rows, and downgrade restores them.

``app/routing_runtime`` (their only reader/writer) was deleted on the owner's decision.
The migration must never lose data silently: with a row in either table ``upgrade``
fails with a clear message and changes nothing, unless
``AGENTVERSE_ALLOW_ORPHAN_TABLE_DROP=1`` is set. Rows are counted past FORCE RLS, so a
NOSUPERUSER / NOBYPASSRLS owner of the tables cannot pass the guard on rows its RLS
policies hide from it.

Uses its own container: it downgrades the schema, which must not disturb the shared
``pg_url`` database other tests expect at head.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = "a8d2f6c4e1b9"
TABLES = ("routing_decisions", "routing_outcomes")
ALLOW_ENV = "AGENTVERSE_ALLOW_ORPHAN_TABLE_DROP"
TENANT = "tenant-routing-drop"


@pytest.fixture(scope="module")
def db_url() -> Iterator[str]:
    try:
        from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

        container = PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg")
        container.start()
    except Exception as exc:  # pragma: no cover - Docker down
        pytest.skip(f"could not start a Postgres testcontainer: {exc}")
    try:
        yield container.get_connection_url()
    finally:
        container.stop()


def _alembic(url: str, *args: str, allow: bool = False) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": url, "ENVIRONMENT": "development"}
    for key in ("MIGRATION_DATABASE_URL", "APP_DB_USER", "APP_DB_PASSWORD", ALLOW_ENV):
        env.pop(key, None)
    if allow:
        env[ALLOW_ENV] = "1"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr[-3000:]


def _run(url: str, *statements: str) -> None:
    async def _go() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                for statement in statements:
                    await conn.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(_go())


def _state(url: str) -> dict[str, Any]:
    async def _read() -> dict[str, Any]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:

                async def _rows(sql: str) -> list[Any]:
                    return list((await conn.execute(text(sql), {"t": list(TABLES)})).all())

                rels = {
                    r[0]: (r[1], r[2])
                    for r in await _rows(
                        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relkind = 'r' AND relname = ANY(:t)"
                    )
                }
                policies = {
                    (r[0], r[1])
                    for r in await _rows(
                        "SELECT tablename, policyname FROM pg_policies WHERE tablename = ANY(:t)"
                    )
                }
                indexes = {
                    r[0]
                    for r in await _rows(
                        "SELECT indexname FROM pg_indexes WHERE tablename = ANY(:t)"
                    )
                }
                constraints = {
                    r[0]
                    for r in await _rows(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid::regclass::text = ANY(:t)"
                    )
                }
                widths = {
                    (r[0], r[1]): r[2]
                    for r in await _rows(
                        "SELECT table_name, column_name, character_maximum_length "
                        "FROM information_schema.columns WHERE table_name = ANY(:t) "
                        "AND data_type = 'character varying'"
                    )
                }
                version = (
                    await conn.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
            return {
                "rels": rels,
                "policies": policies,
                "indexes": indexes,
                "constraints": constraints,
                "widths": widths,
                "version": version,
            }
        finally:
            await engine.dispose()

    return asyncio.run(_read())


def test_head_drops_tables_and_downgrade_restores_them(db_url: str) -> None:
    _ok(_alembic(db_url, "upgrade", "head"))
    assert _state(db_url)["rels"] == {}, "routing tables must not exist at head"

    _ok(_alembic(db_url, "downgrade", PREVIOUS))
    restored = _state(db_url)
    assert restored["version"] == PREVIOUS
    assert restored["rels"] == dict.fromkeys(TABLES, (True, True))  # ENABLE + FORCE
    assert restored["policies"] == {(t, f"{t}_tenant_isolation") for t in TABLES}
    assert {
        "routing_decisions_pkey",
        "idx_routing_decisions_tenant_goal_created",
        "idx_routing_decisions_tenant_category_created",
        "uq_routing_decision_execution_category_id",
        "routing_outcomes_pkey",
        "idx_routing_outcomes_tenant_decision_recorded",
        "uq_routing_outcome_attempt_evaluator",
    } <= restored["indexes"]
    assert {
        "routing_decisions_tenant_id_fkey",
        "ck_routing_decision_category",
        "routing_outcomes_tenant_id_fkey",
        "routing_outcomes_decision_id_fkey",
        "ck_routing_outcome_attempt",
        "ck_routing_outcome_quality",
        "ck_routing_outcome_cost",
        "ck_routing_outcome_latency",
        "ck_routing_outcome_tokens",
    } <= restored["constraints"]
    for column in ("id", "tenant_id", "goal_id", "execution_id"):
        assert restored["widths"][("routing_decisions", column)] == 64
    for column in ("id", "tenant_id", "decision_id"):
        assert restored["widths"][("routing_outcomes", column)] == 64

    # Empty tables: the drop goes through without the override.
    _ok(_alembic(db_url, "upgrade", "head"))
    assert _state(db_url)["rels"] == {}


def test_non_empty_table_blocks_drop_unless_explicitly_allowed(db_url: str) -> None:
    _ok(_alembic(db_url, "upgrade", "head"))
    _ok(_alembic(db_url, "downgrade", PREVIOUS))

    # A NOSUPERUSER / NOBYPASSRLS owner of the tables runs the migration: FORCE RLS
    # hides every tenant row from it unless the guard lifts FORCE before counting.
    _run(
        db_url,
        f"INSERT INTO tenants (id, name, email) VALUES ('{TENANT}', 'routing drop', "
        "'routing-drop@example.test') ON CONFLICT DO NOTHING",
        "INSERT INTO routing_decisions (id, tenant_id, goal_id, execution_id, category, "
        f"profile_version, safe_rationale, payload) VALUES ('d-1', '{TENANT}', 'g-1', "
        "'e-1', 'model', 1, 'why', '{}')",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
        "'routing_migrator') THEN CREATE ROLE routing_migrator LOGIN PASSWORD 'pw' "
        "NOSUPERUSER NOBYPASSRLS; END IF; END $$",
        "GRANT USAGE ON SCHEMA public TO routing_migrator",
        "GRANT SELECT, UPDATE ON alembic_version TO routing_migrator",
        "GRANT SELECT, REFERENCES ON tenants TO routing_migrator",
        *(f"ALTER TABLE {table} OWNER TO routing_migrator" for table in TABLES),
    )
    migrator_url = (
        make_url(db_url)
        .set(username="routing_migrator", password="pw")
        .render_as_string(hide_password=False)
    )

    refused = _alembic(migrator_url, "upgrade", "head")
    assert refused.returncode != 0
    assert "Refusing to drop orphaned tables that still hold data" in refused.stderr
    assert "routing_decisions (1 rows)" in refused.stderr
    assert ALLOW_ENV in refused.stderr
    blocked = _state(db_url)
    assert blocked["version"] == PREVIOUS, "a refused drop must not advance the revision"
    assert blocked["rels"] == dict.fromkeys(TABLES, (True, True)), (
        "a refused drop must leave both tables (and their FORCE RLS) in place"
    )

    _ok(_alembic(migrator_url, "upgrade", "head", allow=True))
    dropped = _state(db_url)
    assert dropped["rels"] == {}
    assert dropped["version"] != PREVIOUS
