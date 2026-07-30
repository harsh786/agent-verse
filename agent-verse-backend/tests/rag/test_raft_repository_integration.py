"""Real PostgreSQL coverage for tenant-scoped RAFT persistence."""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.rag.raft import (
    RAFT_INFERENCE_CAPABILITY,
    ConfirmationRequiredError,
    FineTuneCost,
    FineTuneEvaluation,
    RAFTDatasetRecord,
    RAFTExample,
    RAFTJobRecord,
    _confirmation_binding_digest,
    _ConfirmationGrant,
    _job_confirmation_digest,
)
from app.rag.raft_repository import SQLRAFTRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
RAFT_TABLES = (
    "raft_datasets",
    "raft_fine_tune_jobs",
    "raft_confirmation_grants",
)
OWNER_ROLE = "raft_application"


@dataclass(frozen=True)
class _Database:
    admin_factory: async_sessionmaker[AsyncSession]
    owner_factory: async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class _TenantRows:
    tenant_a: str
    tenant_b: str
    collection_a: str
    collection_b: str


def _owner_url(admin_url: str, password: str) -> str:
    return make_url(admin_url).set(
        username=OWNER_ROLE,
        password=password,
    ).render_as_string(hide_password=False)


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


async def _prepare_owner(admin_url: str, password: str) -> None:
    engine = create_async_engine(admin_url)
    async with engine.begin() as connection:
        quoted_password = (
            await connection.execute(
                text("SELECT quote_literal(:password)"),
                {"password": password},
            )
        ).scalar_one()
        await connection.execute(
            text(
                f"CREATE ROLE {OWNER_ROLE} LOGIN PASSWORD {quoted_password} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await connection.execute(text(f"GRANT CONNECT ON DATABASE test TO {OWNER_ROLE}"))
        await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {OWNER_ROLE}"))
        for table in RAFT_TABLES:
            await connection.execute(text(f"ALTER TABLE {table} OWNER TO {OWNER_ROLE}"))
    await engine.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def database(postgres_url: str) -> AsyncIterator[_Database]:
    password = secrets.token_urlsafe(24)
    await _prepare_owner(postgres_url, password)
    admin_engine = create_async_engine(postgres_url)
    owner_engine = create_async_engine(
        _owner_url(postgres_url, password),
        pool_size=4,
        max_overflow=0,
    )
    yield _Database(
        admin_factory=async_sessionmaker(admin_engine, expire_on_commit=False),
        owner_factory=async_sessionmaker(owner_engine, expire_on_commit=False),
    )
    await owner_engine.dispose()
    await admin_engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def tenant_rows(database: _Database) -> AsyncIterator[_TenantRows]:
    suffix = uuid.uuid4().hex[:12]
    rows = _TenantRows(
        tenant_a=f"raft-a-{suffix}",
        tenant_b=f"raft-b-{suffix}",
        collection_a=f"col-a-{suffix}",
        collection_b=f"col-b-{suffix}",
    )
    async with database.admin_factory() as session, session.begin():
        for tenant_id, collection_id in (
            (rows.tenant_a, rows.collection_a),
            (rows.tenant_b, rows.collection_b),
        ):
            await session.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :id, :email, 'enterprise', true)"
                ),
                {"id": tenant_id, "email": f"{tenant_id}@example.test"},
            )
            await session.execute(
                text(
                    "INSERT INTO knowledge_collections (id, tenant_id, name) "
                    "VALUES (:id, :tenant_id, :id)"
                ),
                {"id": collection_id, "tenant_id": tenant_id},
            )
    yield rows
    async with database.admin_factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM tenants WHERE id IN (:tenant_a, :tenant_b)"),
            {"tenant_a": rows.tenant_a, "tenant_b": rows.tenant_b},
        )


def _dataset(tenant_id: str, collection_id: str, dataset_id: str) -> RAFTDatasetRecord:
    example = RAFTExample(
        example_id=f"example-{dataset_id}",
        document_id="document-1",
        question="What is the durable policy?",
        answer="The durable policy is persisted.",
        oracle_chunk_id="chunk-1",
        oracle_context="Durable policy evidence.",
        distractor_chunk_ids=("chunk-2",),
        distractor_contexts=("Unrelated evidence.",),
        split="train",
    )
    return RAFTDatasetRecord(
        dataset_id=dataset_id,
        tenant_id=tenant_id,
        collection_id=collection_id,
        content_fingerprint="a" * 64,
        examples=(example,),
        validation_errors=(),
        created_at=datetime(2026, 7, 30, 10, 0, tzinfo=UTC),
    )


def _grant(tenant_id: str, dataset_id: str, token_hash: str) -> _ConfirmationGrant:
    cost = FineTuneCost(currency="USD", estimated_amount=Decimal("1.25"))
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    return _ConfirmationGrant(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        provider_id="test-provider",
        base_model="base-model",
        cost=cost,
        token_hash=token_hash,
        binding_digest=_confirmation_binding_digest(
            token_hash=token_hash,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            provider_id="test-provider",
            base_model="base-model",
            cost=cost,
            expires_at=expires_at,
        ),
        expires_at=expires_at,
    )


def _pending_job(
    tenant_id: str,
    collection_id: str,
    dataset_id: str,
    *,
    job_id: str | None = None,
    base_model: str = "base-model",
) -> RAFTJobRecord:
    return RAFTJobRecord(
        job_id=job_id or uuid.uuid4().hex,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        collection_id=collection_id,
        provider_id="test-provider",
        base_model=base_model,
        capability=RAFT_INFERENCE_CAPABILITY,
        compatibility_key="f" * 64,
        status="pending",
        confirmation_digest="",
    )


async def test_dataset_job_evaluation_and_grant_round_trip_durably(
    database: _Database,
    tenant_rows: _TenantRows,
) -> None:
    repository = SQLRAFTRepository(database.owner_factory)
    dataset = _dataset(tenant_rows.tenant_a, tenant_rows.collection_a, uuid.uuid4().hex)
    await repository.save_dataset(tenant_rows.tenant_a, dataset)

    job = RAFTJobRecord(
        job_id=uuid.uuid4().hex,
        tenant_id=tenant_rows.tenant_a,
        dataset_id=dataset.dataset_id,
        collection_id=dataset.collection_id,
        provider_id="test-provider",
        base_model="base-model",
        capability=RAFT_INFERENCE_CAPABILITY,
        compatibility_key="b" * 64,
        status="completed",
        confirmation_digest="c" * 64,
        provider_job_id="provider-job-1",
        fine_tuned_model="fine-tuned-model-1",
        evaluation=FineTuneEvaluation(metrics={"accuracy": 0.91, "faithfulness": 0.87}),
        estimated_cost=FineTuneCost(currency="USD", estimated_amount=Decimal("1.25")),
        created_at=datetime(2026, 7, 30, 10, 1, tzinfo=UTC),
        updated_at=datetime(2026, 7, 30, 10, 2, tzinfo=UTC),
    )
    await repository.save_job(tenant_rows.tenant_a, job)

    grant = _grant(tenant_rows.tenant_a, dataset.dataset_id, secrets.token_hex(32))
    await repository.save_confirmation(tenant_rows.tenant_a, grant)

    reloaded = SQLRAFTRepository(database.owner_factory)
    assert (
        await reloaded.get_dataset(tenant_rows.tenant_a, dataset.dataset_id)
        == dataset
    )
    assert await reloaded.get_job(tenant_rows.tenant_a, job.job_id) == job
    assert (
        await reloaded.find_completed_job(
            tenant_rows.tenant_a,
            job.compatibility_key,
        )
        == job
    )
    assert await reloaded.list_completed_models(
        tenant_rows.tenant_a,
        dataset.collection_id,
        provider_ids=frozenset({"test-provider"}),
        capability=RAFT_INFERENCE_CAPABILITY,
        dataset_id=dataset.dataset_id,
        base_model="base-model",
    ) == [job]
    assert await reloaded.list_completed_models(
        tenant_rows.tenant_a,
        dataset.collection_id,
        provider_ids=frozenset({"unregistered-provider"}),
        capability=RAFT_INFERENCE_CAPABILITY,
    ) == []
    assert await reloaded.list_completed_models(
        tenant_rows.tenant_b,
        dataset.collection_id,
        provider_ids=frozenset({"test-provider"}),
        capability=RAFT_INFERENCE_CAPABILITY,
    ) == []
    assert (
        await reloaded.consume_confirmation(tenant_rows.tenant_a, grant.token_hash)
        == grant
    )
    assert (
        await reloaded.consume_confirmation(tenant_rows.tenant_a, grant.token_hash)
        is None
    )


async def test_owner_role_rls_blocks_cross_tenant_read_update_and_consume(
    database: _Database,
    tenant_rows: _TenantRows,
) -> None:
    repository = SQLRAFTRepository(database.owner_factory)
    dataset_b = _dataset(
        tenant_rows.tenant_b,
        tenant_rows.collection_b,
        uuid.uuid4().hex,
    )
    await repository.save_dataset(tenant_rows.tenant_b, dataset_b)
    grant_b = _grant(tenant_rows.tenant_b, dataset_b.dataset_id, secrets.token_hex(32))
    await repository.save_confirmation(tenant_rows.tenant_b, grant_b)

    assert (
        await repository.get_dataset(tenant_rows.tenant_a, dataset_b.dataset_id)
        is None
    )
    assert (
        await repository.consume_confirmation(
            tenant_rows.tenant_a,
            grant_b.token_hash,
        )
        is None
    )

    cross_tenant_update = replace(
        dataset_b,
        tenant_id=tenant_rows.tenant_a,
        collection_id=tenant_rows.collection_a,
        validation_errors=("cross-tenant mutation",),
    )
    with pytest.raises(IntegrityError):
        await repository.save_dataset(tenant_rows.tenant_a, cross_tenant_update)

    assert (
        await repository.get_dataset(tenant_rows.tenant_b, dataset_b.dataset_id)
        == dataset_b
    )
    assert (
        await repository.consume_confirmation(
            tenant_rows.tenant_b,
            grant_b.token_hash,
        )
        == grant_b
    )


async def test_database_rejects_cross_tenant_collection_reference_under_attacker_context(
    database: _Database,
    tenant_rows: _TenantRows,
) -> None:
    with pytest.raises(IntegrityError):
        async with database.owner_factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_rows.tenant_a},
            )
            await session.execute(
                text(
                    "INSERT INTO raft_datasets "
                    "(id, tenant_id, collection_id, content_fingerprint, examples, "
                    "validation_errors) VALUES "
                    "(:id, :tenant_id, :collection_id, :fingerprint, "
                    "CAST('[]' AS jsonb), CAST('[]' AS jsonb))"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "tenant_id": tenant_rows.tenant_a,
                    "collection_id": tenant_rows.collection_b,
                    "fingerprint": "a" * 64,
                },
            )


@pytest.mark.parametrize("child_table", ["job", "grant"])
async def test_database_rejects_cross_tenant_dataset_reference_under_attacker_context(
    database: _Database,
    tenant_rows: _TenantRows,
    child_table: str,
) -> None:
    repository = SQLRAFTRepository(database.owner_factory)
    foreign_dataset = _dataset(
        tenant_rows.tenant_b,
        tenant_rows.collection_b,
        uuid.uuid4().hex,
    )
    await repository.save_dataset(tenant_rows.tenant_b, foreign_dataset)

    with pytest.raises(IntegrityError):
        async with database.owner_factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_rows.tenant_a},
            )
            if child_table == "job":
                await session.execute(
                    text(
                        "INSERT INTO raft_fine_tune_jobs "
                        "(id, tenant_id, dataset_id, collection_id, provider_id, "
                        "base_model, capability, compatibility_key, status, "
                        "confirmation_digest) VALUES "
                        "(:id, :tenant_id, :dataset_id, :collection_id, "
                        "'test-provider', 'base-model', :capability, :compatibility_key, "
                        "'pending', :confirmation_digest)"
                    ),
                    {
                        "id": uuid.uuid4().hex,
                        "tenant_id": tenant_rows.tenant_a,
                        "dataset_id": foreign_dataset.dataset_id,
                        "collection_id": tenant_rows.collection_a,
                        "capability": RAFT_INFERENCE_CAPABILITY,
                        "compatibility_key": secrets.token_hex(32),
                        "confirmation_digest": secrets.token_hex(32),
                    },
                )
            else:
                await session.execute(
                    text(
                        "INSERT INTO raft_confirmation_grants "
                        "(token_hash, tenant_id, dataset_id, provider_id, base_model, "
                        "currency, estimated_amount, binding_digest, expires_at) VALUES "
                        "(:token_hash, :tenant_id, :dataset_id, 'test-provider', "
                        "'base-model', 'USD', 1.25, :binding_digest, "
                        "now() + interval '10 minutes')"
                    ),
                    {
                        "token_hash": secrets.token_hex(32),
                        "tenant_id": tenant_rows.tenant_a,
                        "dataset_id": foreign_dataset.dataset_id,
                        "binding_digest": secrets.token_hex(32),
                    },
                )


async def test_same_tenant_raft_relationships_round_trip_and_cascade(
    database: _Database,
    tenant_rows: _TenantRows,
) -> None:
    collection_id = f"cascade-{uuid.uuid4().hex[:12]}"
    dataset_id = uuid.uuid4().hex
    job_id = uuid.uuid4().hex
    token_hash = secrets.token_hex(32)
    async with database.admin_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO knowledge_collections (id, tenant_id, name) "
                "VALUES (:id, :tenant_id, :id)"
            ),
            {"id": collection_id, "tenant_id": tenant_rows.tenant_a},
        )

    async with database.owner_factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_rows.tenant_a},
        )
        await session.execute(
            text(
                "INSERT INTO raft_datasets "
                "(id, tenant_id, collection_id, content_fingerprint, examples, "
                "validation_errors) VALUES "
                "(:id, :tenant_id, :collection_id, :fingerprint, "
                "CAST('[]' AS jsonb), CAST('[]' AS jsonb))"
            ),
            {
                "id": dataset_id,
                "tenant_id": tenant_rows.tenant_a,
                "collection_id": collection_id,
                "fingerprint": "b" * 64,
            },
        )
        await session.execute(
            text(
                "INSERT INTO raft_fine_tune_jobs "
                "(id, tenant_id, dataset_id, collection_id, provider_id, base_model, "
                "capability, compatibility_key, status, confirmation_digest) VALUES "
                "(:id, :tenant_id, :dataset_id, :collection_id, 'test-provider', "
                "'base-model', :capability, :compatibility_key, 'pending', :digest)"
            ),
            {
                "id": job_id,
                "tenant_id": tenant_rows.tenant_a,
                "dataset_id": dataset_id,
                "collection_id": collection_id,
                "capability": RAFT_INFERENCE_CAPABILITY,
                "compatibility_key": secrets.token_hex(32),
                "digest": secrets.token_hex(32),
            },
        )
        await session.execute(
            text(
                "INSERT INTO raft_confirmation_grants "
                "(token_hash, tenant_id, dataset_id, provider_id, base_model, currency, "
                "estimated_amount, binding_digest, expires_at) VALUES "
                "(:token_hash, :tenant_id, :dataset_id, 'test-provider', 'base-model', "
                "'USD', 1.25, :binding_digest, now() + interval '10 minutes')"
            ),
            {
                "token_hash": token_hash,
                "tenant_id": tenant_rows.tenant_a,
                "dataset_id": dataset_id,
                "binding_digest": secrets.token_hex(32),
            },
        )

    async with database.admin_factory() as session:
        persisted = (
            await session.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM raft_datasets WHERE id = :dataset_id), "
                    "(SELECT count(*) FROM raft_fine_tune_jobs WHERE id = :job_id), "
                    "(SELECT count(*) FROM raft_confirmation_grants "
                    " WHERE token_hash = :token_hash)"
                ),
                {
                    "dataset_id": dataset_id,
                    "job_id": job_id,
                    "token_hash": token_hash,
                },
            )
        ).one()

    assert persisted == (1, 1, 1)

    async with database.admin_factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM knowledge_collections WHERE id = :collection_id"),
            {"collection_id": collection_id},
        )
        remaining = (
            await session.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM raft_datasets WHERE id = :dataset_id), "
                    "(SELECT count(*) FROM raft_fine_tune_jobs WHERE id = :job_id), "
                    "(SELECT count(*) FROM raft_confirmation_grants "
                    " WHERE token_hash = :token_hash)"
                ),
                {
                    "dataset_id": dataset_id,
                    "job_id": job_id,
                    "token_hash": token_hash,
                },
            )
        ).one()

    assert remaining == (0, 0, 0)


async def test_writes_reject_entity_tenant_mismatch_before_database_access(
    database: _Database,
    tenant_rows: _TenantRows,
) -> None:
    repository = SQLRAFTRepository(database.owner_factory)
    dataset_b = _dataset(
        tenant_rows.tenant_b,
        tenant_rows.collection_b,
        uuid.uuid4().hex,
    )
    grant_b = _grant(tenant_rows.tenant_b, dataset_b.dataset_id, secrets.token_hex(32))
    job_b = RAFTJobRecord(
        job_id=uuid.uuid4().hex,
        tenant_id=tenant_rows.tenant_b,
        dataset_id=dataset_b.dataset_id,
        collection_id=dataset_b.collection_id,
        provider_id="test-provider",
        base_model="base-model",
        capability=RAFT_INFERENCE_CAPABILITY,
        compatibility_key="d" * 64,
        status="pending",
        confirmation_digest="e" * 64,
    )

    with pytest.raises(ValueError, match="tenant does not match"):
        await repository.save_dataset(tenant_rows.tenant_a, dataset_b)
    with pytest.raises(ValueError, match="tenant does not match"):
        await repository.save_confirmation(tenant_rows.tenant_a, grant_b)
    with pytest.raises(ValueError, match="tenant does not match"):
        await repository.save_job(tenant_rows.tenant_a, job_b)

    assert await repository.get_dataset(tenant_rows.tenant_b, dataset_b.dataset_id) is None


async def test_confirmation_grant_concurrent_consumption_has_exactly_one_winner(
    database: _Database,
    tenant_rows: _TenantRows,
) -> None:
    repository = SQLRAFTRepository(database.owner_factory)
    dataset = _dataset(tenant_rows.tenant_a, tenant_rows.collection_a, uuid.uuid4().hex)
    await repository.save_dataset(tenant_rows.tenant_a, dataset)
    grant = _grant(tenant_rows.tenant_a, dataset.dataset_id, secrets.token_hex(32))
    await repository.save_confirmation(tenant_rows.tenant_a, grant)

    results = await asyncio.gather(
        repository.consume_confirmation(tenant_rows.tenant_a, grant.token_hash),
        repository.consume_confirmation(tenant_rows.tenant_a, grant.token_hash),
    )

    assert results.count(grant) == 1
    assert results.count(None) == 1


async def test_wrong_binding_retains_grant_for_atomic_job_creation(
    database: _Database,
    tenant_rows: _TenantRows,
) -> None:
    repository = SQLRAFTRepository(database.owner_factory)
    dataset = _dataset(tenant_rows.tenant_a, tenant_rows.collection_a, uuid.uuid4().hex)
    await repository.save_dataset(tenant_rows.tenant_a, dataset)
    grant = _grant(tenant_rows.tenant_a, dataset.dataset_id, secrets.token_hex(32))
    await repository.save_confirmation(tenant_rows.tenant_a, grant)

    wrong = await repository.create_job_from_confirmation(
        tenant_rows.tenant_a,
        grant.token_hash,
        _pending_job(
            tenant_rows.tenant_a,
            tenant_rows.collection_a,
            dataset.dataset_id,
            base_model="wrong-model",
        ),
    )
    created = await repository.create_job_from_confirmation(
        tenant_rows.tenant_a,
        grant.token_hash,
        _pending_job(
            tenant_rows.tenant_a,
            tenant_rows.collection_a,
            dataset.dataset_id,
        ),
    )

    assert wrong is None
    assert created is not None
    assert created.estimated_cost == grant.cost
    assert created.confirmation_digest == _job_confirmation_digest(
        grant.token_hash,
        _pending_job(
            tenant_rows.tenant_a,
            tenant_rows.collection_a,
            dataset.dataset_id,
            job_id=created.job_id,
        ),
        grant.cost,
    )


async def test_atomic_job_creation_produces_one_durable_job(
    database: _Database,
    tenant_rows: _TenantRows,
) -> None:
    repository = SQLRAFTRepository(database.owner_factory)
    dataset = _dataset(tenant_rows.tenant_a, tenant_rows.collection_a, uuid.uuid4().hex)
    await repository.save_dataset(tenant_rows.tenant_a, dataset)
    grant = _grant(tenant_rows.tenant_a, dataset.dataset_id, secrets.token_hex(32))
    await repository.save_confirmation(tenant_rows.tenant_a, grant)
    pending = _pending_job(
        tenant_rows.tenant_a,
        tenant_rows.collection_a,
        dataset.dataset_id,
    )

    results = await asyncio.gather(
        repository.create_job_from_confirmation(
            tenant_rows.tenant_a, grant.token_hash, pending
        ),
        repository.create_job_from_confirmation(
            tenant_rows.tenant_a, grant.token_hash, pending
        ),
    )

    assert any(result is not None for result in results)
    assert all(result is None or result.job_id == pending.job_id for result in results)
    persisted = await repository.get_job(tenant_rows.tenant_a, pending.job_id)
    assert persisted is not None
    assert (
        await repository.consume_confirmation(tenant_rows.tenant_a, grant.token_hash)
        is None
    )


async def test_consumed_confirmation_fast_path_requires_exact_job_identity(
    database: _Database,
    tenant_rows: _TenantRows,
) -> None:
    repository = SQLRAFTRepository(database.owner_factory)
    dataset = _dataset(tenant_rows.tenant_a, tenant_rows.collection_a, uuid.uuid4().hex)
    await repository.save_dataset(tenant_rows.tenant_a, dataset)
    grant = _grant(tenant_rows.tenant_a, dataset.dataset_id, secrets.token_hex(32))
    await repository.save_confirmation(tenant_rows.tenant_a, grant)
    pending = _pending_job(
        tenant_rows.tenant_a,
        tenant_rows.collection_a,
        dataset.dataset_id,
    )

    created = await repository.create_job_from_confirmation(
        tenant_rows.tenant_a,
        grant.token_hash,
        pending,
    )
    exact_retry = await repository.create_job_from_confirmation(
        tenant_rows.tenant_a,
        grant.token_hash,
        pending,
    )
    mismatches = (
        replace(pending, tenant_id=tenant_rows.tenant_b),
        replace(pending, dataset_id="different-dataset"),
        replace(pending, collection_id="different-collection"),
        replace(pending, provider_id="different-provider"),
        replace(pending, base_model="different-model"),
        replace(pending, capability="different-capability"),
        replace(pending, compatibility_key="e" * 64),
        replace(pending, confirmation_digest="d" * 64),
        replace(
            pending,
            estimated_cost=FineTuneCost("USD", Decimal("9.00")),
        ),
    )
    for mismatch in mismatches:
        with pytest.raises(ConfirmationRequiredError, match="does not match"):
            await repository.create_job_from_confirmation(
                tenant_rows.tenant_a,
                grant.token_hash,
                mismatch,
            )

    assert created is not None
    assert exact_retry == created


async def test_stale_sql_update_cannot_regress_terminal_state_or_erase_evaluation(
    database: _Database,
    tenant_rows: _TenantRows,
) -> None:
    repository = SQLRAFTRepository(database.owner_factory)
    dataset = _dataset(tenant_rows.tenant_a, tenant_rows.collection_a, uuid.uuid4().hex)
    await repository.save_dataset(tenant_rows.tenant_a, dataset)
    submitted = replace(
        _pending_job(
            tenant_rows.tenant_a,
            tenant_rows.collection_a,
            dataset.dataset_id,
        ),
        status="submitted",
        confirmation_digest=secrets.token_hex(32),
        provider_job_id="provider-job-stale",
    )
    await repository.save_job(tenant_rows.tenant_a, submitted)
    completed = replace(
        submitted,
        status="completed",
        fine_tuned_model="fine-tuned-model-stale",
        evaluation=FineTuneEvaluation(metrics={"accuracy": 1.0}),
    )
    winner = await repository.transition_job(
        tenant_rows.tenant_a,
        completed,
        submitted.version,
    )
    stale = await repository.transition_job(
        tenant_rows.tenant_a,
        replace(submitted, status="running"),
        submitted.version,
    )

    assert winner is not None and winner.status == "completed"
    assert stale is None
    assert await repository.get_job(tenant_rows.tenant_a, submitted.job_id) == winner

    with pytest.raises(DBAPIError, match="Illegal RAFT job status transition"):
        async with database.admin_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE raft_fine_tune_jobs SET status = 'running', "
                    "version = version + 1 WHERE id = :job_id"
                ),
                {"job_id": submitted.job_id},
            )


@pytest.mark.parametrize(
    ("currency", "amount"),
    [
        ("usd", Decimal("1.00")),
        ("USD", Decimal("-0.01")),
        ("USD", Decimal("NaN")),
    ],
)
async def test_database_rejects_invalid_raft_money(
    database: _Database,
    tenant_rows: _TenantRows,
    currency: str,
    amount: Decimal,
) -> None:
    dataset = _dataset(tenant_rows.tenant_a, tenant_rows.collection_a, uuid.uuid4().hex)
    repository = SQLRAFTRepository(database.owner_factory)
    await repository.save_dataset(tenant_rows.tenant_a, dataset)

    with pytest.raises(IntegrityError):
        async with database.admin_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO raft_confirmation_grants "
                    "(token_hash, tenant_id, dataset_id, provider_id, base_model, currency, "
                    "estimated_amount, binding_digest, expires_at) VALUES "
                    "(:token, :tenant, :dataset, 'test-provider', 'base-model', :currency, "
                    ":amount, :digest, now() + interval '10 minutes')"
                ),
                {
                    "token": secrets.token_hex(32),
                    "tenant": tenant_rows.tenant_a,
                    "dataset": dataset.dataset_id,
                    "currency": currency,
                    "amount": amount,
                    "digest": secrets.token_hex(32),
                },
            )


async def test_raft_tables_enable_and_force_rls(database: _Database) -> None:
    async with database.admin_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE relname = ANY(:tables) ORDER BY relname"
                ),
                {"tables": list(RAFT_TABLES)},
            )
        ).all()

    assert rows == [(table, True, True) for table in sorted(RAFT_TABLES)]