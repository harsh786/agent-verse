"""Fast-tier (no Docker/Postgres) unit coverage for ``SQLRAFTRepository``.

A sibling suite (``tests/rag/test_raft_repository_integration.py``) proves the
tenant-scoped RLS/ORM behavior end-to-end against a real Postgres container
(``pytest.mark.integration``), but that suite needs Docker and is excluded from
the fast tier. This file mocks the SQLAlchemy async session (``scalar``,
``scalars``, ``execute``, ``merge``, ``add``, ``flush``) so the repository's
branching logic — tenant-mismatch guards, dataset/job/(de)serialization,
confirmation-grant consumption, atomic job creation, optimistic version
transitions, and completed-model filtering — is exercised without live infra.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.db.models.raft import RAFTConfirmationGrant, RAFTDataset, RAFTFineTuneJob
from app.rag.raft import (
    RAFT_INFERENCE_CAPABILITY,
    ConfirmationRequiredError,
    FineTuneCost,
    RAFTDatasetRecord,
    RAFTExample,
    RAFTJobRecord,
    _ConfirmationGrant,
    _confirmation_binding_digest,
    _job_confirmation_digest,
)
from app.rag.raft_repository import SQLRAFTRepository

# asyncio_mode = "auto" (pyproject.toml) auto-detects `async def` tests.


# ── Fakes ──────────────────────────────────────────────────────────────────────


class FakeResult:
    def __init__(self, *, scalar_one_or_none: Any = None) -> None:
        self._son = scalar_one_or_none

    def scalar_one_or_none(self) -> Any:
        return self._son


class FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    def __init__(
        self,
        *,
        scalars: list[Any] | None = None,
        executes: list[FakeResult] | None = None,
        scalars_all: list[list[Any]] | None = None,
    ) -> None:
        self._scalar_queue = list(scalars or [])
        self._execute_queue = list(executes or [])
        self._scalars_queue = list(scalars_all or [])
        self.merged: list[Any] = []
        self.added: list[Any] = []
        self.flushed = False
        self.executed_sql: list[str] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def begin(self) -> FakeSession:
        return self

    async def scalar(self, *args: Any, **kwargs: Any) -> Any:
        return self._scalar_queue.pop(0) if self._scalar_queue else None

    async def scalars(self, *args: Any, **kwargs: Any) -> FakeScalars:
        rows = self._scalars_queue.pop(0) if self._scalars_queue else []
        return FakeScalars(rows)

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        sql = str(stmt)
        self.executed_sql.append(sql)
        if "set_config" in sql:
            return FakeResult()
        return self._execute_queue.pop(0) if self._execute_queue else FakeResult()

    async def merge(self, obj: Any) -> Any:
        self.merged.append(obj)
        return obj

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed = True


class FakeSessionFactory:
    def __init__(self, sessions: list[FakeSession]) -> None:
        self._sessions = list(sessions)
        self.used: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = self._sessions.pop(0)
        self.used.append(session)
        return session


# ── Fixtures / builders ──────────────────────────────────────────────────────

TENANT = "tenant-1"
OTHER_TENANT = "tenant-2"


def _example(example_id: str = "ex-1") -> RAFTExample:
    return RAFTExample(
        example_id=example_id,
        document_id="doc-1",
        question="What?",
        answer="This.",
        oracle_chunk_id="chunk-1",
        oracle_context="Context.",
        distractor_chunk_ids=("chunk-2",),
        distractor_contexts=("Other context.",),
        split="train",
    )


def _dataset_record(dataset_id: str = "ds-1", tenant_id: str = TENANT) -> RAFTDatasetRecord:
    return RAFTDatasetRecord(
        dataset_id=dataset_id,
        tenant_id=tenant_id,
        collection_id="col-1",
        content_fingerprint="a" * 64,
        examples=(_example(),),
        validation_errors=(),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _dataset_row(record: RAFTDatasetRecord) -> RAFTDataset:
    return RAFTDataset(
        id=record.dataset_id,
        tenant_id=record.tenant_id,
        collection_id=record.collection_id,
        content_fingerprint=record.content_fingerprint,
        examples=[
            {
                "example_id": e.example_id,
                "document_id": e.document_id,
                "question": e.question,
                "answer": e.answer,
                "oracle_chunk_id": e.oracle_chunk_id,
                "oracle_context": e.oracle_context,
                "distractor_chunk_ids": list(e.distractor_chunk_ids),
                "distractor_contexts": list(e.distractor_contexts),
                "split": e.split,
            }
            for e in record.examples
        ],
        validation_errors=list(record.validation_errors),
        created_at=record.created_at,
    )


def _grant(
    tenant_id: str = TENANT,
    dataset_id: str = "ds-1",
    token_hash: str = "t" * 64,
    provider_id: str = "provider-a",
    base_model: str = "base-model",
    expires_in: timedelta = timedelta(minutes=10),
) -> _ConfirmationGrant:
    cost = FineTuneCost(currency="USD", estimated_amount=Decimal("1.25"))
    expires_at = datetime.now(UTC) + expires_in
    return _ConfirmationGrant(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        provider_id=provider_id,
        base_model=base_model,
        cost=cost,
        token_hash=token_hash,
        binding_digest=_confirmation_binding_digest(
            token_hash=token_hash,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            provider_id=provider_id,
            base_model=base_model,
            cost=cost,
            expires_at=expires_at,
        ),
        expires_at=expires_at,
    )


def _grant_row(grant: _ConfirmationGrant) -> RAFTConfirmationGrant:
    return RAFTConfirmationGrant(
        token_hash=grant.token_hash,
        tenant_id=grant.tenant_id,
        dataset_id=grant.dataset_id,
        provider_id=grant.provider_id,
        base_model=grant.base_model,
        currency=grant.cost.currency,
        estimated_amount=grant.cost.estimated_amount,
        binding_digest=grant.binding_digest,
        expires_at=grant.expires_at,
    )


def _pending_job(
    tenant_id: str = TENANT,
    dataset_id: str = "ds-1",
    job_id: str = "job-1",
    base_model: str = "base-model",
    provider_id: str = "provider-a",
) -> RAFTJobRecord:
    return RAFTJobRecord(
        job_id=job_id,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        collection_id="col-1",
        provider_id=provider_id,
        base_model=base_model,
        capability=RAFT_INFERENCE_CAPABILITY,
        compatibility_key="c" * 64,
        status="pending",
        confirmation_digest="",
    )


def _job_row(record: RAFTJobRecord) -> RAFTFineTuneJob:
    return RAFTFineTuneJob(
        id=record.job_id,
        tenant_id=record.tenant_id,
        dataset_id=record.dataset_id,
        collection_id=record.collection_id,
        provider_id=record.provider_id,
        base_model=record.base_model,
        capability=record.capability,
        compatibility_key=record.compatibility_key,
        status=record.status,
        confirmation_digest=record.confirmation_digest,
        version=record.version,
        provider_job_id=record.provider_job_id,
        fine_tuned_model=record.fine_tuned_model,
        evaluation=record.evaluation.metrics if record.evaluation else None,
        cost_currency=record.estimated_cost.currency if record.estimated_cost else None,
        estimated_cost=record.estimated_cost.estimated_amount if record.estimated_cost else None,
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


# ── load_chunks ──────────────────────────────────────────────────────────────


async def test_load_chunks_unknown_collection_returns_empty() -> None:
    session = FakeSession(scalars=[None])
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    assert await repository.load_chunks(TENANT, "missing-collection") == []


async def test_load_chunks_unsupported_dimension_returns_empty() -> None:
    session = FakeSession(scalars=[999])
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    assert await repository.load_chunks(TENANT, "col-1") == []


async def test_load_chunks_maps_rows_for_supported_dimension() -> None:
    class _FetchResult(FakeResult):
        def fetchall(self) -> list[Any]:
            return [("chunk-1", "doc-1", "hello world", {"page": 1})]

    session = FakeSession(scalars=[1536], executes=[_FetchResult()])
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    chunks = await repository.load_chunks(TENANT, "col-1")
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "chunk-1"
    assert chunks[0].document_id == "doc-1"
    assert chunks[0].content == "hello world"
    assert chunks[0].metadata == {"page": 1}


# ── save_dataset / get_dataset ───────────────────────────────────────────────


async def test_save_dataset_tenant_mismatch_raises_before_touching_session() -> None:
    repository = SQLRAFTRepository(FakeSessionFactory([]))
    record = _dataset_record(tenant_id=OTHER_TENANT)
    with pytest.raises(ValueError, match="tenant does not match"):
        await repository.save_dataset(TENANT, record)


async def test_save_dataset_merges_and_flushes() -> None:
    session = FakeSession()
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    record = _dataset_record()
    await repository.save_dataset(TENANT, record)
    assert len(session.merged) == 1
    assert session.merged[0].id == record.dataset_id
    assert session.flushed


async def test_get_dataset_found_and_missing() -> None:
    record = _dataset_record()
    session_found = FakeSession(scalars=[_dataset_row(record)])
    repository = SQLRAFTRepository(FakeSessionFactory([session_found]))
    got = await repository.get_dataset(TENANT, record.dataset_id)
    assert got == record

    session_missing = FakeSession(scalars=[None])
    repository2 = SQLRAFTRepository(FakeSessionFactory([session_missing]))
    assert await repository2.get_dataset(TENANT, "missing") is None


# ── save_confirmation / consume_confirmation ─────────────────────────────────


async def test_save_confirmation_tenant_mismatch_raises() -> None:
    repository = SQLRAFTRepository(FakeSessionFactory([]))
    grant = _grant(tenant_id=OTHER_TENANT)
    with pytest.raises(ValueError, match="tenant does not match"):
        await repository.save_confirmation(TENANT, grant)


async def test_save_confirmation_merges_and_flushes() -> None:
    session = FakeSession()
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    grant = _grant()
    await repository.save_confirmation(TENANT, grant)
    assert len(session.merged) == 1
    assert session.flushed


async def test_consume_confirmation_missing_returns_none() -> None:
    session = FakeSession(scalars=[None])
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    assert await repository.consume_confirmation(TENANT, "missing-token") is None


async def test_consume_confirmation_found_deletes_and_returns_grant() -> None:
    grant = _grant()
    session = FakeSession(scalars=[_grant_row(grant)], executes=[FakeResult()])
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    consumed = await repository.consume_confirmation(TENANT, grant.token_hash)
    assert consumed == grant
    assert any("DELETE" in sql.upper() for sql in session.executed_sql)


# ── create_job_from_confirmation ─────────────────────────────────────────────


async def test_create_job_from_confirmation_tenant_mismatch_raises() -> None:
    repository = SQLRAFTRepository(FakeSessionFactory([]))
    pending = _pending_job(tenant_id=OTHER_TENANT)
    with pytest.raises(ConfirmationRequiredError, match="does not match the trusted tenant"):
        await repository.create_job_from_confirmation(TENANT, "token", pending)


async def test_create_job_from_confirmation_exact_retry_returns_existing() -> None:
    grant = _grant()
    pending = _pending_job()
    cost = grant.cost
    existing = replace(
        pending,
        estimated_cost=cost,
        confirmation_digest=_job_confirmation_digest("token", pending, cost),
    )
    session = FakeSession(scalars=[_job_row(existing)])
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    result = await repository.create_job_from_confirmation(TENANT, "token", pending)
    assert result is not None
    assert result.job_id == pending.job_id
    assert result.status == "pending"


async def test_create_job_from_confirmation_no_matching_grant_row_returns_none() -> None:
    pending = _pending_job()
    session = FakeSession(scalars=[None, None])
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    assert await repository.create_job_from_confirmation(TENANT, "token", pending) is None


async def test_create_job_from_confirmation_grant_mismatch_returns_none() -> None:
    pending = _pending_job(base_model="base-model")
    mismatched_grant = _grant(base_model="different-model")
    session = FakeSession(scalars=[None, _grant_row(mismatched_grant)])
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    assert (
        await repository.create_job_from_confirmation(TENANT, mismatched_grant.token_hash, pending)
        is None
    )


async def test_create_job_from_confirmation_success_creates_and_deletes_grant() -> None:
    grant = _grant()
    pending = _pending_job()
    session = FakeSession(scalars=[None, _grant_row(grant)], executes=[FakeResult()])
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    created = await repository.create_job_from_confirmation(TENANT, grant.token_hash, pending)
    assert created is not None
    assert created.estimated_cost == grant.cost
    assert created.confirmation_digest == _job_confirmation_digest(
        grant.token_hash, pending, grant.cost
    )
    assert len(session.added) == 1
    assert session.flushed
    assert any("DELETE" in sql.upper() for sql in session.executed_sql)


# ── save_job / get_job / find_completed_job ──────────────────────────────────


async def test_save_job_tenant_mismatch_raises() -> None:
    repository = SQLRAFTRepository(FakeSessionFactory([]))
    with pytest.raises(ValueError, match="tenant does not match"):
        await repository.save_job(TENANT, _pending_job(tenant_id=OTHER_TENANT))


async def test_save_job_adds_and_flushes() -> None:
    session = FakeSession()
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    await repository.save_job(TENANT, _pending_job())
    assert len(session.added) == 1
    assert session.flushed


async def test_get_job_found_and_missing() -> None:
    job = _pending_job()
    session_found = FakeSession(scalars=[_job_row(job)])
    repository = SQLRAFTRepository(FakeSessionFactory([session_found]))
    got = await repository.get_job(TENANT, job.job_id)
    assert got is not None
    assert got.job_id == job.job_id

    session_missing = FakeSession(scalars=[None])
    repository2 = SQLRAFTRepository(FakeSessionFactory([session_missing]))
    assert await repository2.get_job(TENANT, "missing") is None


async def test_find_completed_job_found_and_missing() -> None:
    job = replace(_pending_job(), status="completed", fine_tuned_model="model-x")
    session_found = FakeSession(scalars=[_job_row(job)])
    repository = SQLRAFTRepository(FakeSessionFactory([session_found]))
    got = await repository.find_completed_job(TENANT, job.compatibility_key)
    assert got is not None
    assert got.status == "completed"

    session_missing = FakeSession(scalars=[None])
    repository2 = SQLRAFTRepository(FakeSessionFactory([session_missing]))
    assert await repository2.find_completed_job(TENANT, "missing-key") is None


# ── transition_job ────────────────────────────────────────────────────────────


async def test_transition_job_tenant_mismatch_raises() -> None:
    repository = SQLRAFTRepository(FakeSessionFactory([]))
    with pytest.raises(ValueError, match="tenant does not match"):
        await repository.transition_job(TENANT, _pending_job(tenant_id=OTHER_TENANT), 0)


async def test_transition_job_current_missing_returns_none() -> None:
    session = FakeSession(scalars=[None])
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    assert await repository.transition_job(TENANT, _pending_job(), 0) is None


async def test_transition_job_version_mismatch_returns_none() -> None:
    current = _pending_job()
    current = replace(current, version=3)
    session = FakeSession(scalars=[_job_row(current)])
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    assert await repository.transition_job(TENANT, replace(current, status="submitted"), 0) is None


async def test_transition_job_success_updates_and_returns_new_record() -> None:
    current = _pending_job()
    updated = replace(current, status="submitted", provider_job_id="provider-job-1")
    session = FakeSession(
        scalars=[_job_row(current)],
        executes=[FakeResult(scalar_one_or_none=_job_row(updated))],
    )
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    result = await repository.transition_job(TENANT, updated, current.version)
    assert result is not None
    assert result.status == "submitted"
    assert result.provider_job_id == "provider-job-1"


async def test_transition_job_concurrent_update_lost_returns_none() -> None:
    current = _pending_job()
    updated = replace(current, status="submitted", provider_job_id="provider-job-1")
    session = FakeSession(
        scalars=[_job_row(current)],
        executes=[FakeResult(scalar_one_or_none=None)],
    )
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    assert await repository.transition_job(TENANT, updated, current.version) is None


async def test_transition_job_illegal_transition_raises() -> None:
    current = replace(_pending_job(), status="completed", fine_tuned_model="m")
    illegal = replace(current, status="pending")
    session = FakeSession(scalars=[_job_row(current)])
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    from app.rag.raft import RAFTError

    with pytest.raises(RAFTError):
        await repository.transition_job(TENANT, illegal, current.version)


# ── list_completed_models / find_completed_model ─────────────────────────────


async def test_list_completed_models_applies_all_filters() -> None:
    job = replace(_pending_job(), status="completed", fine_tuned_model="model-x")
    session = FakeSession(scalars_all=[[_job_row(job)]])
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    results = await repository.list_completed_models(
        TENANT,
        "col-1",
        provider_ids=frozenset({"provider-a"}),
        capability=RAFT_INFERENCE_CAPABILITY,
        dataset_id="ds-1",
        base_model="base-model",
    )
    assert len(results) == 1
    assert results[0].job_id == job.job_id


async def test_list_completed_models_empty() -> None:
    session = FakeSession(scalars_all=[[]])
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    assert await repository.list_completed_models(TENANT, "col-1") == []


async def test_find_completed_model_returns_first_match_or_none() -> None:
    job = replace(_pending_job(), status="completed", fine_tuned_model="model-x")
    session = FakeSession(scalars_all=[[_job_row(job)]])
    repository = SQLRAFTRepository(FakeSessionFactory([session]))
    found = await repository.find_completed_model(TENANT, "col-1")
    assert found is not None
    assert found.job_id == job.job_id

    session_empty = FakeSession(scalars_all=[[]])
    repository2 = SQLRAFTRepository(FakeSessionFactory([session_empty]))
    assert await repository2.find_completed_model(TENANT, "col-1") is None
