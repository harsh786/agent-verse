"""SQLAlchemy persistence for tenant-scoped RAFT lifecycle records."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from typing import Literal, cast

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.raft import (
    RAFTConfirmationGrant,
    RAFTDataset,
    RAFTFineTuneJob,
    RAFTModelDeployment,
)
from app.db.rls import sqlalchemy_rls_context, system_session
from app.rag.raft import (
    DEFAULT_MAX_TRAINING_CHUNKS,
    IN_FLIGHT_STATUSES,
    ConfirmationRequiredError,
    FineTuneCost,
    FineTuneEvaluation,
    PersistedRAFTChunk,
    RAFTDatasetRecord,
    RAFTDeploymentRecord,
    RAFTError,
    RAFTExample,
    RAFTJobRecord,
    RAFTJobStatus,
    RAFTNotFoundError,
    _ConfirmationGrant,
    _grant_matches_job,
    _job_confirmation_digest,
    _require_exact_job_retry,
    _require_legal_transition,
    _require_matching_tenant,
)
from app.rag.store import SUPPORTED_EMBEDDING_DIMENSIONS

DEFAULT_CHUNK_PAGE_SIZE = 500

# Only curated chunks (a non-blank question AND answer) can become RAFT examples,
# so the cap and the paging apply to those rows, not the whole collection.
_CURATED_CHUNK_FILTER = (
    "AND btrim(COALESCE(metadata->>'question', '')) <> '' "
    "AND btrim(COALESCE(metadata->>'answer', '')) <> '' "
)


class SQLRAFTRepository:
    """Persist all RAFT state inside tenant RLS transactions.

    ``system_session_factory`` (a BYPASSRLS maintenance role) is used only by the
    cross-tenant in-flight scan of the background status poller; every other
    read and write runs under the owning tenant's RLS context.
    """

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        *,
        system_session_factory: Callable[[], AsyncSession] | None = None,
        chunk_page_size: int = DEFAULT_CHUNK_PAGE_SIZE,
    ) -> None:
        if chunk_page_size < 1:
            raise ValueError("chunk_page_size must be positive")
        self._session_factory = session_factory
        self._system_session_factory = system_session_factory
        self._chunk_page_size = chunk_page_size

    async def load_chunks(
        self,
        tenant_id: str,
        collection_id: str,
        *,
        limit: int = DEFAULT_MAX_TRAINING_CHUNKS,
    ) -> list[PersistedRAFTChunk]:
        """Keyset-page curated chunks in ``(document_id, chunk_index, id)`` order.

        Never materialises more than ``limit`` rows: each page is bounded, and the
        next page resumes strictly after the last row seen.
        """
        if limit < 1:
            return []
        async with self._tenant_session(tenant_id) as session:
            dimension = await session.scalar(
                text(
                    "SELECT embedding_dim FROM knowledge_collections "
                    "WHERE id = :collection_id AND tenant_id = :tenant_id "
                    "AND is_active IS TRUE"
                ),
                {"collection_id": collection_id, "tenant_id": tenant_id},
            )
            if dimension is None or int(dimension) not in SUPPORTED_EMBEDDING_DIMENSIONS:
                return []
            table = f"knowledge_chunks_{int(dimension)}"
            base = (
                f"SELECT id, document_id, chunk_index, content, metadata FROM {table} "
                "WHERE tenant_id = :tenant_id AND collection_id = :collection_id "
                f"AND is_proposition IS FALSE {_CURATED_CHUNK_FILTER}"
            )
            first_page = text(f"{base}ORDER BY document_id, chunk_index, id LIMIT :page_size")
            next_page = text(
                f"{base}AND (document_id, chunk_index, id) > "
                "(:after_document_id, :after_chunk_index, :after_id) "
                "ORDER BY document_id, chunk_index, id LIMIT :page_size"
            )
            chunks: list[PersistedRAFTChunk] = []
            cursor: tuple[str, int, str] | None = None
            while len(chunks) < limit:
                page_size = min(self._chunk_page_size, limit - len(chunks))
                params: dict[str, object] = {
                    "tenant_id": tenant_id,
                    "collection_id": collection_id,
                    "page_size": page_size,
                }
                if cursor is None:
                    statement = first_page
                else:
                    statement = next_page
                    params.update(
                        after_document_id=cursor[0],
                        after_chunk_index=cursor[1],
                        after_id=cursor[2],
                    )
                rows = (await session.execute(statement, params)).fetchall()
                for row in rows:
                    chunks.append(
                        PersistedRAFTChunk(
                            chunk_id=str(row[0]),
                            document_id=str(row[1]),
                            content=str(row[3]),
                            metadata=dict(row[4] or {}),
                        )
                    )
                if len(rows) < page_size:
                    break
                last = rows[-1]
                cursor = (str(last[1]), int(last[2]), str(last[0]))
        return chunks

    async def save_dataset(self, tenant_id: str, record: RAFTDatasetRecord) -> None:
        _require_matching_tenant(tenant_id, record.tenant_id)
        async with self._tenant_session(tenant_id) as session:
            await session.merge(
                RAFTDataset(
                    id=record.dataset_id,
                    tenant_id=record.tenant_id,
                    collection_id=record.collection_id,
                    content_fingerprint=record.content_fingerprint,
                    examples=[asdict(example) for example in record.examples],
                    validation_errors=list(record.validation_errors),
                    created_at=record.created_at,
                )
            )
            await session.flush()

    async def get_dataset(self, tenant_id: str, dataset_id: str) -> RAFTDatasetRecord | None:
        async with self._tenant_session(tenant_id) as session:
            row = await session.scalar(
                select(RAFTDataset).where(
                    RAFTDataset.id == dataset_id,
                    RAFTDataset.tenant_id == tenant_id,
                )
            )
        if row is None:
            return None
        return RAFTDatasetRecord(
            dataset_id=row.id,
            tenant_id=row.tenant_id,
            collection_id=row.collection_id,
            content_fingerprint=row.content_fingerprint,
            examples=tuple(_example_record(example) for example in row.examples),
            validation_errors=tuple(row.validation_errors),
            created_at=row.created_at,
        )

    async def save_confirmation(self, tenant_id: str, grant: _ConfirmationGrant) -> None:
        _require_matching_tenant(tenant_id, grant.tenant_id)
        async with self._tenant_session(tenant_id) as session:
            await session.merge(
                RAFTConfirmationGrant(
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
            )
            await session.flush()

    async def consume_confirmation(
        self, tenant_id: str, token_hash: str
    ) -> _ConfirmationGrant | None:
        async with self._tenant_session(tenant_id) as session:
            row = await session.scalar(
                select(RAFTConfirmationGrant)
                .where(
                    RAFTConfirmationGrant.token_hash == token_hash,
                    RAFTConfirmationGrant.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            grant = _ConfirmationGrant(
                tenant_id=row.tenant_id,
                dataset_id=row.dataset_id,
                provider_id=row.provider_id,
                base_model=row.base_model,
                cost=FineTuneCost(row.currency, row.estimated_amount),
                token_hash=row.token_hash,
                binding_digest=row.binding_digest,
                expires_at=row.expires_at,
            )
            await session.execute(
                delete(RAFTConfirmationGrant).where(
                    RAFTConfirmationGrant.token_hash == token_hash,
                    RAFTConfirmationGrant.tenant_id == tenant_id,
                )
            )
            return grant

    async def create_job_from_confirmation(
        self,
        tenant_id: str,
        token_hash: str,
        pending_job: RAFTJobRecord,
    ) -> RAFTJobRecord | None:
        if tenant_id != pending_job.tenant_id:
            raise ConfirmationRequiredError(
                "Consumed confirmation request does not match the trusted tenant"
            )
        async with self._tenant_session(tenant_id) as session:
            existing = await session.scalar(
                select(RAFTFineTuneJob).where(
                    RAFTFineTuneJob.id == pending_job.job_id,
                    RAFTFineTuneJob.tenant_id == tenant_id,
                )
            )
            if existing is not None:
                existing_record = _job_record(existing)
                _require_exact_job_retry(existing_record, pending_job, token_hash)
                return existing_record
            row = await session.scalar(
                select(RAFTConfirmationGrant)
                .join(RAFTDataset, RAFTDataset.id == RAFTConfirmationGrant.dataset_id)
                .where(
                    RAFTConfirmationGrant.token_hash == token_hash,
                    RAFTConfirmationGrant.tenant_id == tenant_id,
                    RAFTConfirmationGrant.dataset_id == pending_job.dataset_id,
                    RAFTConfirmationGrant.provider_id == pending_job.provider_id,
                    RAFTConfirmationGrant.base_model == pending_job.base_model,
                    RAFTDataset.tenant_id == tenant_id,
                    RAFTDataset.collection_id == pending_job.collection_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            grant = _ConfirmationGrant(
                tenant_id=row.tenant_id,
                dataset_id=row.dataset_id,
                provider_id=row.provider_id,
                base_model=row.base_model,
                cost=FineTuneCost(row.currency, row.estimated_amount),
                token_hash=row.token_hash,
                binding_digest=row.binding_digest,
                expires_at=row.expires_at,
            )
            if not _grant_matches_job(grant, pending_job):
                return None
            created = replace(
                pending_job,
                estimated_cost=grant.cost,
                confirmation_digest=_job_confirmation_digest(
                    token_hash,
                    pending_job,
                    grant.cost,
                ),
            )
            session.add(_job_row(created))
            await session.execute(
                delete(RAFTConfirmationGrant).where(
                    RAFTConfirmationGrant.token_hash == token_hash,
                    RAFTConfirmationGrant.tenant_id == tenant_id,
                )
            )
            await session.flush()
            return created

    async def save_job(self, tenant_id: str, record: RAFTJobRecord) -> None:
        _require_matching_tenant(tenant_id, record.tenant_id)
        async with self._tenant_session(tenant_id) as session:
            session.add(_job_row(record))
            await session.flush()

    async def transition_job(
        self,
        tenant_id: str,
        record: RAFTJobRecord,
        expected_version: int,
    ) -> RAFTJobRecord | None:
        _require_matching_tenant(tenant_id, record.tenant_id)
        async with self._tenant_session(tenant_id) as session:
            current = await session.scalar(
                select(RAFTFineTuneJob).where(
                    RAFTFineTuneJob.id == record.job_id,
                    RAFTFineTuneJob.tenant_id == tenant_id,
                )
            )
            if current is None or current.version != expected_version:
                return None
            _require_legal_transition(_job_record(current), record)
            values = _job_values(record)
            values["version"] = expected_version + 1
            result = await session.execute(
                update(RAFTFineTuneJob)
                .where(
                    RAFTFineTuneJob.id == record.job_id,
                    RAFTFineTuneJob.tenant_id == tenant_id,
                    RAFTFineTuneJob.version == expected_version,
                )
                .values(**values)
                .returning(RAFTFineTuneJob)
            )
            updated = result.scalar_one_or_none()
            return _job_record(updated) if updated is not None else None

    async def get_job(self, tenant_id: str, job_id: str) -> RAFTJobRecord | None:
        async with self._tenant_session(tenant_id) as session:
            row = await session.scalar(
                select(RAFTFineTuneJob).where(
                    RAFTFineTuneJob.id == job_id,
                    RAFTFineTuneJob.tenant_id == tenant_id,
                )
            )
        return _job_record(row) if row is not None else None

    async def find_completed_job(
        self, tenant_id: str, compatibility_key: str
    ) -> RAFTJobRecord | None:
        async with self._tenant_session(tenant_id) as session:
            row = await session.scalar(
                select(RAFTFineTuneJob)
                .where(
                    RAFTFineTuneJob.tenant_id == tenant_id,
                    RAFTFineTuneJob.compatibility_key == compatibility_key,
                    RAFTFineTuneJob.status == "completed",
                    RAFTFineTuneJob.fine_tuned_model.is_not(None),
                )
                .order_by(RAFTFineTuneJob.updated_at.desc())
                .limit(1)
            )
        return _job_record(row) if row is not None else None

    async def find_completed_model(
        self, tenant_id: str, collection_id: str
    ) -> RAFTJobRecord | None:
        matches = await self.list_completed_models(tenant_id, collection_id)
        return matches[0] if matches else None

    async def list_completed_models(
        self,
        tenant_id: str,
        collection_id: str,
        *,
        provider_ids: frozenset[str] | None = None,
        capability: str | None = None,
        dataset_id: str | None = None,
        base_model: str | None = None,
    ) -> list[RAFTJobRecord]:
        async with self._tenant_session(tenant_id) as session:
            statement = select(RAFTFineTuneJob).where(
                RAFTFineTuneJob.tenant_id == tenant_id,
                RAFTFineTuneJob.collection_id == collection_id,
                RAFTFineTuneJob.status == "completed",
                RAFTFineTuneJob.fine_tuned_model.is_not(None),
            )
            if provider_ids is not None:
                statement = statement.where(RAFTFineTuneJob.provider_id.in_(provider_ids))
            if capability is not None:
                statement = statement.where(RAFTFineTuneJob.capability == capability)
            if dataset_id is not None:
                statement = statement.where(RAFTFineTuneJob.dataset_id == dataset_id)
            if base_model is not None:
                statement = statement.where(RAFTFineTuneJob.base_model == base_model)
            rows = (
                await session.scalars(statement.order_by(RAFTFineTuneJob.updated_at.desc()))
            ).all()
        return [_job_record(row) for row in rows]

    async def list_in_flight_jobs(self, *, limit: int) -> list[tuple[str, str]]:
        if limit < 1:
            return []
        if self._system_session_factory is None:
            raise RAFTError("RAFT in-flight scan requires a maintenance session factory")
        async with (
            self._system_session_factory() as session,
            session.begin(),
            system_session(session),
        ):
            rows = (
                await session.execute(
                    select(RAFTFineTuneJob.tenant_id, RAFTFineTuneJob.id)
                    .where(
                        RAFTFineTuneJob.status.in_(sorted(IN_FLIGHT_STATUSES)),
                        RAFTFineTuneJob.provider_job_id.is_not(None),
                    )
                    .order_by(RAFTFineTuneJob.updated_at.asc(), RAFTFineTuneJob.id.asc())
                    .limit(limit)
                )
            ).all()
        return [(str(row[0]), str(row[1])) for row in rows]

    async def save_deployment(self, tenant_id: str, record: RAFTDeploymentRecord) -> None:
        _require_matching_tenant(tenant_id, record.tenant_id)
        async with self._tenant_session(tenant_id) as session:
            job_exists = await session.scalar(
                select(RAFTFineTuneJob.id).where(
                    RAFTFineTuneJob.tenant_id == tenant_id,
                    RAFTFineTuneJob.id == record.job_id,
                    RAFTFineTuneJob.collection_id == record.collection_id,
                )
            )
            if job_exists is None:
                raise RAFTNotFoundError("RAFT job not found for this collection")
            statement = pg_insert(RAFTModelDeployment).values(
                tenant_id=record.tenant_id,
                collection_id=record.collection_id,
                job_id=record.job_id,
                deployed_at=record.deployed_at,
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        RAFTModelDeployment.tenant_id,
                        RAFTModelDeployment.collection_id,
                    ],
                    set_={
                        "job_id": statement.excluded.job_id,
                        "deployed_at": statement.excluded.deployed_at,
                    },
                )
            )

    async def get_deployment(
        self, tenant_id: str, collection_id: str
    ) -> RAFTDeploymentRecord | None:
        async with self._tenant_session(tenant_id) as session:
            row = await session.scalar(
                select(RAFTModelDeployment).where(
                    RAFTModelDeployment.tenant_id == tenant_id,
                    RAFTModelDeployment.collection_id == collection_id,
                )
            )
        if row is None:
            return None
        return RAFTDeploymentRecord(
            tenant_id=row.tenant_id,
            collection_id=row.collection_id,
            job_id=row.job_id,
            deployed_at=row.deployed_at,
        )

    @asynccontextmanager
    async def _tenant_session(self, tenant_id: str) -> AsyncIterator[AsyncSession]:
        async with (
            self._session_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            yield session


def _example_record(payload: dict[str, object]) -> RAFTExample:
    return RAFTExample(
        example_id=str(payload["example_id"]),
        document_id=str(payload["document_id"]),
        question=str(payload["question"]),
        answer=str(payload["answer"]),
        oracle_chunk_id=str(payload["oracle_chunk_id"]),
        oracle_context=str(payload["oracle_context"]),
        distractor_chunk_ids=tuple(
            str(value) for value in cast(list[object], payload["distractor_chunk_ids"])
        ),
        distractor_contexts=tuple(
            str(value) for value in cast(list[object], payload["distractor_contexts"])
        ),
        split=cast(Literal["train", "test"], payload["split"]),
    )


def _job_record(row: RAFTFineTuneJob) -> RAFTJobRecord:
    evaluation = FineTuneEvaluation(dict(row.evaluation)) if row.evaluation else None
    estimated_cost = (
        FineTuneCost(row.cost_currency, row.estimated_cost)
        if row.cost_currency is not None and row.estimated_cost is not None
        else None
    )
    return RAFTJobRecord(
        job_id=row.id,
        tenant_id=row.tenant_id,
        dataset_id=row.dataset_id,
        collection_id=row.collection_id,
        provider_id=row.provider_id,
        base_model=row.base_model,
        capability=row.capability,
        compatibility_key=row.compatibility_key,
        status=cast(RAFTJobStatus, row.status),
        confirmation_digest=row.confirmation_digest,
        version=row.version,
        provider_job_id=row.provider_job_id,
        fine_tuned_model=row.fine_tuned_model,
        evaluation=evaluation,
        estimated_cost=estimated_cost,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _job_values(record: RAFTJobRecord) -> dict[str, object]:
    return {
        "status": record.status,
        "provider_job_id": record.provider_job_id,
        "fine_tuned_model": record.fine_tuned_model,
        "evaluation": record.evaluation.metrics if record.evaluation else None,
        "cost_currency": record.estimated_cost.currency if record.estimated_cost else None,
        "estimated_cost": (
            record.estimated_cost.estimated_amount if record.estimated_cost else None
        ),
        "error": record.error,
        "updated_at": record.updated_at,
    }


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
        confirmation_digest=record.confirmation_digest,
        version=record.version,
        created_at=record.created_at,
        **_job_values(record),
    )


__all__ = ["SQLRAFTRepository"]
