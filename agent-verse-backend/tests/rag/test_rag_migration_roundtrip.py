"""Isolated PostgreSQL round-trip coverage for RAG migration 0091."""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DIMENSIONS = (768, 1024, 1536, 3072)
NEW_COLUMNS = {"window_id", "hierarchy_level", "is_proposition", "strategy_metadata"}


class _SchemaState(TypedDict):
    columns: dict[int, set[str]]
    indexes: dict[int, set[str]]
    policies: dict[str, tuple[bool, bool, int]]
    index_definitions: dict[str, str]


def _alembic(database_url: str, *arguments: str) -> None:
    subprocess.run(
        ["alembic", *arguments],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        check=True,
        capture_output=True,
        text=True,
    )


async def _schema_state(database_url: str) -> _SchemaState:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        columns: dict[int, set[str]] = {}
        indexes: dict[int, set[str]] = {}
        policies: dict[str, tuple[bool, bool, int]] = {}
        index_definitions: dict[str, str] = {}
        for dimension in DIMENSIONS:
            table = f"knowledge_chunks_{dimension}"
            columns[dimension] = set(
                (
                    await connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = :table"
                        ),
                        {"table": table},
                    )
                ).scalars()
            )
            indexes[dimension] = set(
                (
                    await connection.execute(
                        text("SELECT indexname FROM pg_indexes WHERE tablename = :table"),
                        {"table": table},
                    )
                ).scalars()
            )
            definition_rows = (
                await connection.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE tablename = :table"
                    ),
                    {"table": table},
                )
            ).all()
            index_definitions.update(
                {str(row[0]): str(row[1]) for row in definition_rows}
            )
        for table in ["knowledge_collections", *(f"knowledge_chunks_{d}" for d in DIMENSIONS)]:
            row = (
                await connection.execute(
                    text(
                        "SELECT c.relrowsecurity, c.relforcerowsecurity, "
                        "(SELECT count(*) FROM pg_policies p WHERE p.tablename = :table) "
                        "FROM pg_class c WHERE c.relname = :table"
                    ),
                    {"table": table},
                )
            ).one()
            policies[table] = (bool(row[0]), bool(row[1]), int(row[2]))
    await engine.dispose()
    return {
        "columns": columns,
        "indexes": indexes,
        "policies": policies,
        "index_definitions": index_definitions,
    }


@pytest.fixture(scope="module")
def isolated_postgres() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


def test_0091_upgrade_downgrade_upgrade_round_trip(isolated_postgres: str) -> None:
    _alembic(isolated_postgres, "upgrade", "0090_parent_child_retrieval")
    before = asyncio.run(_schema_state(isolated_postgres))

    _alembic(isolated_postgres, "upgrade", "0091_rag_ingestion_structures")
    upgraded = asyncio.run(_schema_state(isolated_postgres))

    _alembic(isolated_postgres, "downgrade", "0090_parent_child_retrieval")
    downgraded = asyncio.run(_schema_state(isolated_postgres))

    _alembic(isolated_postgres, "upgrade", "0091_rag_ingestion_structures")
    reupgraded = asyncio.run(_schema_state(isolated_postgres))

    for dimension in DIMENSIONS:
        assert NEW_COLUMNS.isdisjoint(before["columns"][dimension])
        assert upgraded["columns"][dimension] >= NEW_COLUMNS
        assert NEW_COLUMNS.isdisjoint(downgraded["columns"][dimension])
        assert reupgraded["columns"][dimension] >= NEW_COLUMNS
        expected_indexes = {
            f"idx_knowledge_chunks_{dimension}_metadata",
            f"idx_knowledge_chunks_{dimension}_fts",
            f"idx_knowledge_chunks_{dimension}_strategy_metadata",
        }
        assert expected_indexes <= upgraded["indexes"][dimension]
        assert expected_indexes.isdisjoint(downgraded["indexes"][dimension])
        assert expected_indexes <= reupgraded["indexes"][dimension]

    for state in (upgraded, downgraded, reupgraded):
        assert all(
            rls and forced and policy_count >= 1
            for rls, forced, policy_count in state["policies"].values()
        )

    halfvec_index = "idx_knowledge_chunks_3072_vector_halfvec"
    assert halfvec_index not in before["index_definitions"]
    assert "halfvec(3072)" in upgraded["index_definitions"][halfvec_index]
    assert "halfvec_cosine_ops" in upgraded["index_definitions"][halfvec_index]
    assert halfvec_index not in downgraded["index_definitions"]
    assert "halfvec(3072)" in reupgraded["index_definitions"][halfvec_index]


def test_0091_uses_retry_safe_concurrent_index_ddl() -> None:
    migration = BACKEND_ROOT / "app/db/migrations/versions/0091_rag_ingestion_structures.py"
    source = migration.read_text()

    assert "autocommit_block" in source
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in source
    assert "DROP INDEX CONCURRENTLY IF EXISTS" in source
    assert "CREATE INDEX IF NOT EXISTS" not in source
    assert "indisvalid" in source
