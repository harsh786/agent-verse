"""Isolated PostgreSQL round-trip coverage for RAG migration 0091."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
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


@pytest.fixture
def owner_migration_postgres() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


def test_0091_upgrade_downgrade_upgrade_round_trip(isolated_postgres: str) -> None:
    _alembic(isolated_postgres, "upgrade", "0090")
    before = asyncio.run(_schema_state(isolated_postgres))

    _alembic(isolated_postgres, "upgrade", "0091")
    upgraded = asyncio.run(_schema_state(isolated_postgres))

    _alembic(isolated_postgres, "downgrade", "0090")
    downgraded = asyncio.run(_schema_state(isolated_postgres))

    _alembic(isolated_postgres, "upgrade", "0091")
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
    _alembic(isolated_postgres, "upgrade", "0091")
    before = asyncio.run(_repository_job_schema(isolated_postgres))
    _alembic(isolated_postgres, "upgrade", "0092")
    upgraded = asyncio.run(_repository_job_schema(isolated_postgres))
    _alembic(isolated_postgres, "downgrade", "0091")
    downgraded = asyncio.run(_repository_job_schema(isolated_postgres))
    _alembic(isolated_postgres, "upgrade", "0092")
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
    _alembic(isolated_postgres, "downgrade", "0091")
    asyncio.run(_seed_preexisting_repository_jobs(isolated_postgres))
    _alembic(isolated_postgres, "upgrade", "0092")

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


_OWNER_ROLE = "repository_migration_owner"
_OWNER_PASSWORD = "repository-migration-password"
_AFFECTED_TABLES = ("knowledge_documents", *(f"knowledge_chunks_{d}" for d in DIMENSIONS))


def _owner_url(admin_url: str) -> str:
    return make_url(admin_url).set(
        username=_OWNER_ROLE,
        password=_OWNER_PASSWORD,
    ).render_as_string(hide_password=False)


def _run_alembic(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["alembic", *arguments],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        check=False,
        capture_output=True,
        text=True,
    )


async def _seed_owner_migration_case(database_url: str, *, invalid_job: bool = False) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        database_name = (
            await connection.execute(text("SELECT quote_ident(current_database())"))
        ).scalar_one()
        await connection.execute(
            text(
                f"CREATE ROLE {_OWNER_ROLE} LOGIN PASSWORD '{_OWNER_PASSWORD}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await connection.execute(text(f"GRANT CREATE ON DATABASE {database_name} TO {_OWNER_ROLE}"))

        tenant_rows = (
            ("owner-tenant-a", "owner-a@example.test", "owner-collection-a"),
            ("owner-tenant-b", "owner-b@example.test", "owner-collection-b"),
        )
        for tenant_id, email, collection_id in tenant_rows:
            await connection.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :id, :email, 'free', true)"
                ),
                {"id": tenant_id, "email": email},
            )
            await connection.execute(
                text(
                    "INSERT INTO knowledge_collections (id, tenant_id, name) "
                    "VALUES (:id, :tenant_id, :id)"
                ),
                {"id": collection_id, "tenant_id": tenant_id},
            )

        jobs = (
            (
                "owner-job-a",
                "owner-tenant-a",
                "owner-collection-a",
                None if invalid_job else "https://github.com/example/owner-a",
                "queued",
            ),
            (
                "owner-job-b",
                "owner-tenant-b",
                "owner-collection-b",
                "https://github.com/example/owner-b",
                "running",
            ),
        )
        for job_id, tenant_id, collection_id, source_url, status in jobs:
            await connection.execute(
                text("""
                    INSERT INTO knowledge_documents
                        (id, tenant_id, collection_id, title, source_url, source_type,
                         content_hash, status, domain_metadata)
                    VALUES
                        (:id, :tenant_id, :collection_id, :id, :source_url, 'repository',
                         'legacy-hash', :status, '{"record_type":"ingestion_job"}'::jsonb)
                """),
                {
                    "id": job_id,
                    "tenant_id": tenant_id,
                    "collection_id": collection_id,
                    "source_url": source_url,
                    "status": status,
                },
            )

        if not invalid_job:
            chunks = (
                (
                    768,
                    "owner-chunk-a",
                    "owner-job-a",
                    "owner-tenant-a",
                    "owner-collection-a",
                    "https://github.com/example/owner-a",
                ),
                (
                    1024,
                    "owner-chunk-b",
                    "owner-job-b",
                    "owner-tenant-b",
                    "owner-collection-b",
                    "https://github.com/example/owner-b",
                ),
            )
            for dimension, chunk_id, job_id, tenant_id, collection_id, source_url in chunks:
                await connection.execute(
                    text(f"""
                        INSERT INTO knowledge_chunks_{dimension}
                            (id, tenant_id, collection_id, document_id, chunk_index,
                             content, content_hash, embedding, metadata, strategy_metadata)
                        VALUES
                            (:id, :tenant_id, :collection_id, :job_id, 0,
                             'legacy chunk', 'legacy-hash',
                             CAST(array_fill(0::real, ARRAY[{dimension}]) AS vector),
                             CAST(:metadata AS jsonb), CAST(:strategy AS jsonb))
                    """),
                    {
                        "id": chunk_id,
                        "tenant_id": tenant_id,
                        "collection_id": collection_id,
                        "job_id": job_id,
                        "metadata": json.dumps({"repo_url": source_url}),
                        "strategy": json.dumps({"ingestion_job_id": job_id}),
                    },
                )

        await connection.execute(text(f"ALTER SCHEMA public OWNER TO {_OWNER_ROLE}"))
        await connection.execute(text(f"ALTER TABLE alembic_version OWNER TO {_OWNER_ROLE}"))
        for table in _AFFECTED_TABLES:
            await connection.execute(text(f"ALTER TABLE {table} OWNER TO {_OWNER_ROLE}"))
        await connection.execute(
            text(f"GRANT SELECT ON knowledge_collections TO {_OWNER_ROLE}")
        )
    await engine.dispose()


async def _owner_migration_state(
    admin_url: str,
    owner_url: str,
) -> dict[str, object]:
    admin_engine = create_async_engine(admin_url)
    async with admin_engine.connect() as connection:
        jobs = (
            await connection.execute(
                text(
                    "SELECT id, status, job_source_hash FROM knowledge_documents "
                    "WHERE id IN ('owner-job-a', 'owner-job-b') ORDER BY id"
                )
            )
        ).all()
        associations = (
            await connection.execute(
                text(
                    "SELECT id, ingestion_job_id FROM knowledge_chunks_768 "
                    "WHERE id = 'owner-chunk-a' UNION ALL "
                    "SELECT id, ingestion_job_id FROM knowledge_chunks_1024 "
                    "WHERE id = 'owner-chunk-b' ORDER BY id"
                )
            )
        ).all()
        flags = {
            str(row[0]): (bool(row[1]), bool(row[2]))
            for row in (
                await connection.execute(
                    text(
                        "SELECT relname, relrowsecurity, relforcerowsecurity "
                        "FROM pg_class WHERE relname = ANY(:tables)"
                    ),
                    {"tables": list(_AFFECTED_TABLES)},
                )
            ).all()
        }
        owners = dict(
            (
                await connection.execute(
                    text(
                        "SELECT relname, pg_get_userbyid(relowner) "
                        "FROM pg_class WHERE relname = ANY(:tables)"
                    ),
                    {"tables": list(_AFFECTED_TABLES)},
                )
            ).all()
        )
        schema_owner = (
            await connection.execute(
                text(
                    "SELECT pg_get_userbyid(nspowner) FROM pg_namespace "
                    "WHERE nspname = 'public'"
                )
            )
        ).scalar_one()
        constraints = set(
            (
                await connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conname = 'ck_repository_ingestion_job_state' "
                        "OR conname LIKE 'fk_knowledge_chunks_%_ingestion_job_scope'"
                    )
                )
            ).scalars()
        )
        role_flags = (
            await connection.execute(
                text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname = :role"
                ),
                {"role": _OWNER_ROLE},
            )
        ).one()
    await admin_engine.dispose()

    owner_engine = create_async_engine(owner_url)
    visible: dict[str, tuple[int, int]] = {}
    async with owner_engine.begin() as connection:
        for tenant_id in ("owner-tenant-a", "owner-tenant-b"):
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            documents = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM knowledge_documents "
                        "WHERE id IN ('owner-job-a', 'owner-job-b')"
                    )
                )
            ).scalar_one()
            chunks = (
                await connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM knowledge_chunks_768 "
                        "WHERE id IN ('owner-chunk-a', 'owner-chunk-b')) + "
                        "(SELECT count(*) FROM knowledge_chunks_1024 "
                        "WHERE id IN ('owner-chunk-a', 'owner-chunk-b'))"
                    )
                )
            ).scalar_one()
            visible[tenant_id] = (int(documents), int(chunks))
    await owner_engine.dispose()

    return {
        "jobs": [(str(row[0]), str(row[1]), str(row[2])) for row in jobs],
        "associations": [(str(row[0]), str(row[1])) for row in associations],
        "flags": flags,
        "owners": {str(table): str(owner) for table, owner in owners.items()},
        "schema_owner": str(schema_owner),
        "constraints": constraints,
        "role_flags": tuple(role_flags),
        "visible": visible,
    }


def test_0092_owner_migrator_backfills_all_tenants_and_restores_force_rls(
    owner_migration_postgres: str,
) -> None:
    _alembic(owner_migration_postgres, "upgrade", "0091")
    asyncio.run(_seed_owner_migration_case(owner_migration_postgres))
    owner_url = _owner_url(owner_migration_postgres)

    result = _run_alembic(owner_url, "upgrade", "0092")

    assert result.returncode == 0, result.stderr
    state = asyncio.run(_owner_migration_state(owner_migration_postgres, owner_url))
    assert state["jobs"] == [
        (
            "owner-job-a",
            "queued",
            hashlib.sha256(b"https://github.com/example/owner-a").hexdigest(),
        ),
        (
            "owner-job-b",
            "failed",
            hashlib.sha256(b"https://github.com/example/owner-b").hexdigest(),
        ),
    ]
    assert state["associations"] == [
        ("owner-chunk-a", "owner-job-a"),
        ("owner-chunk-b", "owner-job-b"),
    ]
    assert state["flags"] == dict.fromkeys(_AFFECTED_TABLES, (True, True))
    assert state["owners"] == dict.fromkeys(_AFFECTED_TABLES, _OWNER_ROLE)
    assert state["schema_owner"] == _OWNER_ROLE
    assert state["constraints"] == {
        "ck_repository_ingestion_job_state",
        *(f"fk_knowledge_chunks_{d}_ingestion_job_scope" for d in DIMENSIONS),
    }
    assert state["role_flags"] == (False, False)
    assert state["visible"] == {
        "owner-tenant-a": (1, 1),
        "owner-tenant-b": (1, 1),
    }


async def _failed_owner_migration_state(database_url: str) -> tuple[str, bool, bool, int]:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        version = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
        flags = (
            await connection.execute(
                text(
                    "SELECT bool_and(relrowsecurity), bool_and(relforcerowsecurity) "
                    "FROM pg_class WHERE relname = ANY(:tables)"
                ),
                {"tables": list(_AFFECTED_TABLES)},
            )
        ).one()
        lease_columns = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'knowledge_documents' "
                    "AND column_name IN "
                    "('job_source_hash', 'lease_owner', 'lease_expires_at', 'heartbeat_at')"
                )
            )
        ).scalar_one()
    await engine.dispose()
    return str(version), bool(flags[0]), bool(flags[1]), int(lease_columns)


def test_0092_owner_migrator_failure_rolls_back_force_rls(
    owner_migration_postgres: str,
) -> None:
    _alembic(owner_migration_postgres, "upgrade", "0091")
    asyncio.run(
        _seed_owner_migration_case(owner_migration_postgres, invalid_job=True)
    )

    result = _run_alembic(
        _owner_url(owner_migration_postgres),
        "upgrade",
        "0092",
    )

    assert result.returncode != 0
    assert asyncio.run(_failed_owner_migration_state(owner_migration_postgres)) == (
        "0091",
        True,
        True,
        0,
    )
