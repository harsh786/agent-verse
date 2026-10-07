"""DROP-ORPHAN-TABLES: e5f1a9c3d7b2 drops ab_test_results / learning_experiments /
learning_experiment_outcomes, refuses while they hold rows, and downgrade restores them.

Their code was deleted (a05-F089-01, a05-F092-02). The migration must never lose data
silently: with a row in any of them ``upgrade`` fails with a clear message and changes
nothing, unless ``AGENTVERSE_ALLOW_ORPHAN_TABLE_DROP=1`` is set. The rows are counted
past FORCE RLS, so a non-superuser table owner cannot pass the guard on a table whose
rows its RLS policies hide from it.

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
REVISION = "e5f1a9c3d7b2"
PREVIOUS = "a7c9e1f3b5d7"
TABLES = ("ab_test_results", "learning_experiments", "learning_experiment_outcomes")
ALLOW_ENV = "AGENTVERSE_ALLOW_ORPHAN_TABLE_DROP"
TENANT = "tenant-orphan-drop"


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
    for key in ("MIGRATION_DATABASE_URL", "APP_DB_USER", "APP_DB_PASSWORD"):
        env.pop(key, None)
    env.pop(ALLOW_ENV, None)
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
                rels = {
                    r[0]: (r[1], r[2])
                    for r in (
                        await conn.execute(
                            text(
                                "SELECT relname, relrowsecurity, relforcerowsecurity "
                                "FROM pg_class WHERE relkind = 'r' AND relname = ANY(:t)"
                            ),
                            {"t": list(TABLES)},
                        )
                    ).all()
                }
                policies = {
                    (r[0], r[1])
                    for r in (
                        await conn.execute(
                            text(
                                "SELECT tablename, policyname FROM pg_policies "
                                "WHERE tablename = ANY(:t)"
                            ),
                            {"t": list(TABLES)},
                        )
                    ).all()
                }
                indexes = {
                    r[0]
                    for r in (
                        await conn.execute(
                            text("SELECT indexname FROM pg_indexes WHERE tablename = ANY(:t)"),
                            {"t": list(TABLES)},
                        )
                    ).all()
                }
                fks = {
                    r[0]
                    for r in (
                        await conn.execute(
                            text(
                                "SELECT conname FROM pg_constraint "
                                "WHERE contype IN ('f', 'u') "
                                "AND conrelid::regclass::text = ANY(:t)"
                            ),
                            {"t": list(TABLES)},
                        )
                    ).all()
                }
                widths = {
                    (r[0], r[1]): r[2]
                    for r in (
                        await conn.execute(
                            text(
                                "SELECT table_name, column_name, character_maximum_length "
                                "FROM information_schema.columns WHERE table_name = ANY(:t) "
                                "AND data_type = 'character varying'"
                            ),
                            {"t": list(TABLES)},
                        )
                    ).all()
                }
                version = (
                    await conn.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
            return {
                "rels": rels,
                "policies": policies,
                "indexes": indexes,
                "constraints": fks,
                "widths": widths,
                "version": version,
            }
        finally:
            await engine.dispose()

    return asyncio.run(_read())


def test_head_drops_tables_and_downgrade_restores_them(db_url: str) -> None:
    _ok(_alembic(db_url, "upgrade", REVISION))
    head = _state(db_url)
    assert head["rels"] == {}, "orphaned tables must not exist at head"

    _ok(_alembic(db_url, "downgrade", PREVIOUS))
    restored = _state(db_url)
    assert restored["version"] == PREVIOUS
    assert restored["rels"] == dict.fromkeys(TABLES, (True, True))  # ENABLE + FORCE
    assert restored["policies"] == {(table, f"{table}_tenant_isolation") for table in TABLES}
    assert {
        "ab_test_results_pkey",
        "ix_ab_test_results_experiment_type",
        "ix_ab_test_results_goal_id",
        "ix_ab_test_results_tenant_id",
        "learning_experiments_pkey",
        "idx_learning_experiments_tenant_status",
        "learning_experiment_outcomes_pkey",
        "uq_experiment_assignment_outcome",
    } <= restored["indexes"]
    assert {
        "ab_test_results_tenant_id_fkey",
        "learning_experiments_tenant_id_fkey",
        "learning_experiment_outcomes_tenant_id_fkey",
        "learning_experiment_outcomes_experiment_id_fkey",
        "uq_experiment_assignment_outcome",
    } <= restored["constraints"]
    # Widths as at the previous head (after e7b1c4d9a2f6 widened the id columns).
    for column in ("id", "goal_id", "tenant_id"):
        assert restored["widths"][("ab_test_results", column)] == 64
    for column in ("id", "tenant_id", "agent_id"):
        assert restored["widths"][("learning_experiments", column)] == 64

    # Empty tables: the drop goes through without the override.
    _ok(_alembic(db_url, "upgrade", REVISION))
    assert _state(db_url)["rels"] == {}


def test_non_empty_table_blocks_drop_unless_explicitly_allowed(db_url: str) -> None:
    _ok(_alembic(db_url, "upgrade", REVISION))
    _ok(_alembic(db_url, "downgrade", PREVIOUS))

    # A NOSUPERUSER / NOBYPASSRLS owner of the tables runs the migration: FORCE RLS
    # hides every tenant row from it unless the guard lifts FORCE before counting.
    url = make_url(db_url)
    _run(
        db_url,
        "INSERT INTO tenants (id, name, email) "
        "VALUES ('" + TENANT + "', 'orphan drop', 'orphan-drop@example.test') "
        "ON CONFLICT DO NOTHING",
        "INSERT INTO ab_test_results (id, goal_id, tenant_id, experiment_type, arm_id, score) "
        "VALUES ('ab-1', 'g-1', '" + TENANT + "', 'rag_strategy', 'control', 0.9)",
        "CREATE ROLE orphan_migrator LOGIN PASSWORD 'pw' NOSUPERUSER NOBYPASSRLS",
        "GRANT USAGE ON SCHEMA public TO orphan_migrator",
        "GRANT SELECT, UPDATE ON alembic_version TO orphan_migrator",
        "GRANT SELECT, REFERENCES ON tenants TO orphan_migrator",
        *(f"ALTER TABLE {table} OWNER TO orphan_migrator" for table in TABLES),
    )
    migrator_url = url.set(username="orphan_migrator", password="pw").render_as_string(
        hide_password=False
    )

    refused = _alembic(migrator_url, "upgrade", REVISION)
    assert refused.returncode != 0
    assert "Refusing to drop orphaned tables that still hold data" in refused.stderr
    assert "ab_test_results (1 rows)" in refused.stderr
    assert ALLOW_ENV in refused.stderr
    blocked = _state(db_url)
    assert blocked["version"] == PREVIOUS, "a refused drop must not advance the revision"
    assert blocked["rels"] == dict.fromkeys(TABLES, (True, True)), (
        "a refused drop must leave every table (and its FORCE RLS) in place"
    )

    _ok(_alembic(migrator_url, "upgrade", REVISION, allow=True))
    dropped = _state(db_url)
    assert dropped["rels"] == {}
    assert dropped["version"] != PREVIOUS
