"""Isolated PostgreSQL round-trip coverage for RAG migration 0091."""

from __future__ import annotations

import asyncio
import hashlib
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


class _RepositoryJobSchema(TypedDict):
    columns: set[str]
    chunk_job_columns: dict[int, int]
    triggers: set[str]


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


async def _repository_job_schema(database_url: str) -> _RepositoryJobSchema:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        columns = set(
            (
                await connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'knowledge_documents'"
                    )
                )
            ).scalars()
        )
        chunk_job_columns = {
            dimension: (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = :table AND column_name = 'ingestion_job_id'"
                    ),
                    {"table": f"knowledge_chunks_{dimension}"},
                )
            ).scalar_one()
            for dimension in DIMENSIONS
        }
        triggers = set(
            (
                await connection.execute(
                    text(
                        "SELECT trigger_name FROM information_schema.triggers "
                        "WHERE event_object_table = 'knowledge_documents'"
                    )
                )
            ).scalars()
        )
    await engine.dispose()
    return {"columns": columns, "chunk_job_columns": chunk_job_columns, "triggers": triggers}


def test_0092_upgrade_downgrade_upgrade_round_trip(isolated_postgres: str) -> None:
    _alembic(isolated_postgres, "upgrade", "0091_rag_ingestion_structures")
    before = asyncio.run(_repository_job_schema(isolated_postgres))
    _alembic(isolated_postgres, "upgrade", "0092_repository_ingestion_leases")
    upgraded = asyncio.run(_repository_job_schema(isolated_postgres))
    _alembic(isolated_postgres, "downgrade", "0091_rag_ingestion_structures")
    downgraded = asyncio.run(_repository_job_schema(isolated_postgres))
    _alembic(isolated_postgres, "upgrade", "0092_repository_ingestion_leases")
    reupgraded = asyncio.run(_repository_job_schema(isolated_postgres))

    lease_columns = {"job_source_hash", "lease_owner", "lease_expires_at", "heartbeat_at"}
    assert lease_columns.isdisjoint(before["columns"])
    assert upgraded["columns"] >= lease_columns
    assert lease_columns.isdisjoint(downgraded["columns"])
    assert reupgraded["columns"] >= lease_columns
    assert all(value == 1 for value in upgraded["chunk_job_columns"].values())
    assert all(value == 0 for value in downgraded["chunk_job_columns"].values())
    assert "trg_repository_ingestion_job_transition" in upgraded["triggers"]
    assert "trg_repository_ingestion_job_transition" in reupgraded["triggers"]


async def _seed_preexisting_repository_jobs(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES ('migration-tenant', 'Migration', 'migration@example.test', 'free', true) "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
        await connection.execute(
            text("""
                INSERT INTO knowledge_collections (id, tenant_id, name)
                VALUES ('migration-collection', 'migration-tenant', 'Migration Collection')
                ON CONFLICT (id) DO NOTHING
            """)
        )
        for job_id, status in (("migration-queued", "queued"), ("migration-running", "running")):
            await connection.execute(
                text("""
                    INSERT INTO knowledge_documents
                        (id, tenant_id, collection_id, title, source_url, source_type,
                         content_hash, status, domain_metadata)
                    VALUES
                        (:id, 'migration-tenant', 'migration-collection', :id,
                         'https://github.com/example/repository', 'repository',
                         'hash', :status, '{"record_type":"ingestion_job"}'::jsonb)
                    ON CONFLICT (id) DO NOTHING
                """),
                {"id": job_id, "status": status},
            )
    await engine.dispose()


async def _read_preexisting_repository_jobs(database_url: str) -> list[tuple[str, str, str]]:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text("""
                    SELECT id, status, job_source_hash FROM knowledge_documents
                    WHERE id IN ('migration-queued', 'migration-running') ORDER BY id
                """)
            )
        ).all()
    await engine.dispose()
    return [(str(row[0]), str(row[1]), str(row[2])) for row in rows]


def test_0092_backfills_sha256_and_interrupts_preexisting_running_job(
    isolated_postgres: str,
) -> None:
    _alembic(isolated_postgres, "downgrade", "0091_rag_ingestion_structures")
    asyncio.run(_seed_preexisting_repository_jobs(isolated_postgres))
    _alembic(isolated_postgres, "upgrade", "0092_repository_ingestion_leases")

    rows = asyncio.run(_read_preexisting_repository_jobs(isolated_postgres))
    expected_hash = hashlib.sha256(
        b"https://github.com/example/repository"
    ).hexdigest()
    assert rows == [
        ("migration-queued", "queued", expected_hash),
        ("migration-running", "failed", expected_hash),
    ]


def test_0092_documents_downgrade_association_warning_and_restore_path() -> None:
    migration = (
        BACKEND_ROOT / "app/db/migrations/versions/0092_repository_ingestion_leases.py"
    ).read_text()

    assert "WARNING: associations survive only in strategy_metadata" in migration
    assert "strategy_metadata->>'ingestion_job_id'" in migration
    assert "chunk.metadata->>'repo_url' = job.source_url" in migration
    assert "job.source_type = 'repository'" in migration
    assert "indisvalid" in migration
