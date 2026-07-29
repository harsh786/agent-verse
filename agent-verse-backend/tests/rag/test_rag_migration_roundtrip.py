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
    return {"columns": columns, "indexes": indexes, "policies": policies}


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
