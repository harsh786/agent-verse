"""Provider-neutral RAFT dataset and fine-tuning lifecycle."""

from __future__ import annotations

import hashlib
import json
import random
import secrets
import uuid
from asyncio import Lock
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal, Protocol

from app.tenancy.context import TenantContext

RAFTJobStatus = Literal[
    "pending",
    "reconciling",
    "submitted",
    "running",
    "completed",
    "failed",
]
MONEY_SCALE = Decimal("0.000001")
RAFT_INFERENCE_CAPABILITY = "raft-grounded-answer-v1"
_RAFT_DATASET_ARTIFACT_SCHEMA_VERSION = 1
_TERMINAL_STATUSES = frozenset({"completed", "failed"})
_LEGAL_TRANSITIONS: dict[RAFTJobStatus, frozenset[RAFTJobStatus]] = {
    "pending": frozenset({"pending", "reconciling", "submitted", "failed"}),
    "reconciling": frozenset({"reconciling", "submitted", "failed"}),
    "submitted": frozenset({"submitted", "running", "completed", "failed"}),
    "running": frozenset({"running", "completed", "failed"}),
    "completed": frozenset({"completed"}),
    "failed": frozenset({"failed"}),
}


class RAFTError(RuntimeError):
    """Base error for fail-closed RAFT lifecycle operations."""


class RAFTNotFoundError(RAFTError):
    """Raised when a tenant cannot access a RAFT record."""


class ConfirmationRequiredError(RAFTError):
    """Raised when paid work lacks fresh action-time confirmation."""


class RAFTModelUnavailableError(RAFTError):
    """Raised when no completed compatible RAFT model exists."""


class RAFTConcurrentUpdateError(RAFTError):
    """Raised when a stale writer loses an optimistic job update."""


@dataclass(frozen=True, slots=True)
class PersistedRAFTChunk:
    chunk_id: str
    document_id: str
    content: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RAFTDatasetConfig:
    distractors_per_example: int = 2
    test_fraction: float = 0.2
    seed: int = 0

    def __post_init__(self) -> None:
        if self.distractors_per_example < 1:
            raise ValueError("distractors_per_example must be positive")
        if not 0.0 < self.test_fraction < 1.0:
            raise ValueError("test_fraction must be between zero and one")


@dataclass(frozen=True, slots=True)
class RAFTExample:
    example_id: str
    document_id: str
    question: str
    answer: str
    oracle_chunk_id: str
    oracle_context: str
    distractor_chunk_ids: tuple[str, ...]
    distractor_contexts: tuple[str, ...]
    split: Literal["train", "test"]

    def to_json_record(self) -> dict[str, object]:
        contexts = [self.oracle_context, *self.distractor_contexts]
        return {
            "example_id": self.example_id,
            "question": self.question,
            "answer": self.answer,
            "oracle_chunk_id": self.oracle_chunk_id,
            "oracle_context": self.oracle_context,
            "distractor_chunk_ids": list(self.distractor_chunk_ids),
            "contexts": contexts,
        }


@dataclass(frozen=True, slots=True)
class RAFTDatasetRecord:
    dataset_id: str
    tenant_id: str
    collection_id: str
    content_fingerprint: str
    examples: tuple[RAFTExample, ...]
    validation_errors: tuple[str, ...]
    created_at: datetime

    @property
    def train_examples(self) -> tuple[RAFTExample, ...]:
        return tuple(example for example in self.examples if example.split == "train")

    @property
    def test_examples(self) -> tuple[RAFTExample, ...]:
        return tuple(example for example in self.examples if example.split == "test")

    @property
    def training_jsonl(self) -> str:
        return _export_jsonl(self.train_examples)

    @property
    def test_jsonl(self) -> str:
        return _export_jsonl(self.test_examples)


@dataclass(frozen=True, slots=True)
class FineTuneCost:
    currency: str
    estimated_amount: Decimal

    def __post_init__(self) -> None:
        currency = self.currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha() or not currency.isascii():
            raise ValueError("currency must be a three-letter ISO code")
        if not isinstance(self.estimated_amount, Decimal):
            raise ValueError("estimated_amount must be an exact Decimal")
        try:
            amount = Decimal(self.estimated_amount)
        except InvalidOperation as exc:
            raise ValueError("estimated_amount must be an exact decimal") from exc
        if not amount.is_finite() or amount < 0:
            raise ValueError("estimated_amount must be finite and non-negative")
        exponent = amount.as_tuple().exponent
        if not isinstance(exponent, int) or exponent < -6:
            raise ValueError("estimated_amount cannot exceed six decimal places")
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "estimated_amount", amount.quantize(MONEY_SCALE))

    @property
    def canonical_amount(self) -> str:
        return format(self.estimated_amount, ".6f")


@dataclass(frozen=True, slots=True)
class FineTuneJobState:
    status: RAFTJobStatus
    fine_tuned_model: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class FineTuneEvaluation:
    metrics: dict[str, float]


class FineTuneProvider(Protocol):
    """Provider adapter for explicitly confirmed external fine-tuning work."""

    @property
    def provider_id(self) -> str: ...

    async def preview_cost(
        self,
        *,
        training_examples: int,
        validation_examples: int,
        base_model: str,
    ) -> FineTuneCost: ...

    async def submit(
        self,
        *,
        training_jsonl: str,
        validation_jsonl: str,
        base_model: str,
        idempotency_key: str,
    ) -> str: ...

    async def status(self, provider_job_id: str) -> FineTuneJobState: ...

    async def evaluate(
        self,
        *,
        model: str,
        test_jsonl: str,
    ) -> FineTuneEvaluation: ...


@dataclass(frozen=True, slots=True)
class RAFTCostPreview:
    dataset_id: str
    provider_id: str
    base_model: str
    estimated_cost: FineTuneCost
    confirmation_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RAFTJobRecord:
    job_id: str
    tenant_id: str
    dataset_id: str
    collection_id: str
    provider_id: str
    base_model: str
    capability: str
    compatibility_key: str
    status: RAFTJobStatus
    confirmation_digest: str
    version: int = 0
    provider_job_id: str | None = None
    fine_tuned_model: str | None = None
    evaluation: FineTuneEvaluation | None = None
    estimated_cost: FineTuneCost | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class _ConfirmationGrant:
    tenant_id: str
    dataset_id: str
    provider_id: str
    base_model: str
    cost: FineTuneCost
    token_hash: str
    binding_digest: str
    expires_at: datetime


class FineTunedInferenceProvider(Protocol):
    """Provider-neutral inference capability for a selected fine-tuned model."""

    @property
    def provider_id(self) -> str: ...

    @property
    def capability(self) -> str: ...

    async def infer(
        self,
        *,
        query: str,
        evidence: tuple[str, ...],
        fine_tuned_model_id: str,
    ) -> str: ...


class RAFTRepository(Protocol):
    async def load_chunks(
        self, tenant_id: str, collection_id: str
    ) -> list[PersistedRAFTChunk]: ...
    async def save_dataset(
        self, tenant_id: str, record: RAFTDatasetRecord
    ) -> None: ...
    async def get_dataset(
        self, tenant_id: str, dataset_id: str
    ) -> RAFTDatasetRecord | None: ...
    async def save_confirmation(
        self, tenant_id: str, grant: _ConfirmationGrant
    ) -> None: ...
    async def consume_confirmation(
        self, tenant_id: str, token_hash: str
    ) -> _ConfirmationGrant | None: ...
    async def create_job_from_confirmation(
        self,
        tenant_id: str,
        token_hash: str,
        pending_job: RAFTJobRecord,
    ) -> RAFTJobRecord | None: ...
    async def save_job(self, tenant_id: str, record: RAFTJobRecord) -> None: ...
    async def transition_job(
        self,
        tenant_id: str,
        record: RAFTJobRecord,
        expected_version: int,
    ) -> RAFTJobRecord | None: ...
    async def get_job(self, tenant_id: str, job_id: str) -> RAFTJobRecord | None: ...
    async def find_completed_job(
        self, tenant_id: str, compatibility_key: str
    ) -> RAFTJobRecord | None: ...
    async def find_completed_model(
        self, tenant_id: str, collection_id: str
    ) -> RAFTJobRecord | None: ...
    async def list_completed_models(
        self,
        tenant_id: str,
        collection_id: str,
        *,
        provider_ids: frozenset[str] | None = None,
        capability: str | None = None,
        dataset_id: str | None = None,
        base_model: str | None = None,
    ) -> list[RAFTJobRecord]: ...


class InMemoryRAFTRepository:
    """Deterministic test repository with the same tenant boundaries as SQL storage."""

    def __init__(self) -> None:
        self._chunks: dict[tuple[str, str], list[PersistedRAFTChunk]] = {}
        self._datasets: dict[tuple[str, str], RAFTDatasetRecord] = {}
        self._confirmations: dict[tuple[str, str], _ConfirmationGrant] = {}
        self._jobs: dict[tuple[str, str], RAFTJobRecord] = {}
        self._submission_lock = Lock()

    def seed_chunks(
        self,
        tenant_id: str,
        collection_id: str,
        chunks: list[PersistedRAFTChunk],
    ) -> None:
        self._chunks[(tenant_id, collection_id)] = list(chunks)

    async def load_chunks(
        self, tenant_id: str, collection_id: str
    ) -> list[PersistedRAFTChunk]:
        return list(self._chunks.get((tenant_id, collection_id), []))

    async def save_dataset(self, tenant_id: str, record: RAFTDatasetRecord) -> None:
        _require_matching_tenant(tenant_id, record.tenant_id)
        self._datasets[(tenant_id, record.dataset_id)] = record

    async def get_dataset(
        self, tenant_id: str, dataset_id: str
    ) -> RAFTDatasetRecord | None:
        return self._datasets.get((tenant_id, dataset_id))

    async def save_confirmation(
        self, tenant_id: str, grant: _ConfirmationGrant
    ) -> None:
        _require_matching_tenant(tenant_id, grant.tenant_id)
        self._confirmations[(tenant_id, grant.token_hash)] = grant

    async def consume_confirmation(
        self, tenant_id: str, token_hash: str
    ) -> _ConfirmationGrant | None:
        return self._confirmations.pop((tenant_id, token_hash), None)

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
        async with self._submission_lock:
            existing = self._jobs.get((tenant_id, pending_job.job_id))
            if existing is not None:
                _require_exact_job_retry(existing, pending_job, token_hash)
                return existing
            grant = self._confirmations.get((tenant_id, token_hash))
            if grant is None or not _grant_matches_job(grant, pending_job):
                return None
            self._confirmations.pop((tenant_id, token_hash))
            created = replace(
                pending_job,
                estimated_cost=grant.cost,
                confirmation_digest=_job_confirmation_digest(
                    token_hash,
                    pending_job,
                    grant.cost,
                ),
            )
            self._jobs[(tenant_id, created.job_id)] = created
            return created

    async def save_job(self, tenant_id: str, record: RAFTJobRecord) -> None:
        _require_matching_tenant(tenant_id, record.tenant_id)
        if (tenant_id, record.job_id) in self._jobs:
            raise RAFTError("RAFT job already exists")
        self._jobs[(tenant_id, record.job_id)] = record

    async def transition_job(
        self,
        tenant_id: str,
        record: RAFTJobRecord,
        expected_version: int,
    ) -> RAFTJobRecord | None:
        async with self._submission_lock:
            current = self._jobs.get((tenant_id, record.job_id))
            if current is None or current.version != expected_version:
                return None
            _require_legal_transition(current, record)
            updated = replace(record, version=expected_version + 1)
            self._jobs[(tenant_id, record.job_id)] = updated
            return updated

    async def get_job(self, tenant_id: str, job_id: str) -> RAFTJobRecord | None:
        return self._jobs.get((tenant_id, job_id))

    async def find_completed_job(
        self, tenant_id: str, compatibility_key: str
    ) -> RAFTJobRecord | None:
        matches = [
            job
            for (job_tenant, _), job in self._jobs.items()
            if job_tenant == tenant_id
            and job.compatibility_key == compatibility_key
            and job.status == "completed"
            and job.fine_tuned_model
        ]
        return max(matches, key=lambda job: job.updated_at) if matches else None

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
        matches = [
            job
            for (job_tenant, _), job in self._jobs.items()
            if job_tenant == tenant_id
            and job.collection_id == collection_id
            and job.status == "completed"
            and job.fine_tuned_model
            and (provider_ids is None or job.provider_id in provider_ids)
            and (capability is None or job.capability == capability)
            and (dataset_id is None or job.dataset_id == dataset_id)
            and (base_model is None or job.base_model == base_model)
        ]
        return sorted(matches, key=lambda job: job.updated_at, reverse=True)


class RAFTService:
    """Construct datasets and coordinate explicitly confirmed provider jobs."""

    def __init__(
        self,
        *,
        repository: RAFTRepository,
        providers: dict[str, FineTuneProvider],
        inference_providers: dict[str, FineTunedInferenceProvider] | None = None,
        confirmation_ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        self._repository = repository
        self._providers = providers
        self._inference_providers = inference_providers or {}
        self._confirmation_ttl = confirmation_ttl

    async def create_dataset(
        self,
        tenant: TenantContext,
        *,
        collection_id: str,
        chunks: list[PersistedRAFTChunk],
        config: RAFTDatasetConfig | None = None,
    ) -> RAFTDatasetRecord:
        dataset = _build_dataset(
            tenant,
            collection_id,
            chunks,
            config or RAFTDatasetConfig(),
        )
        await self._repository.save_dataset(tenant.tenant_id, dataset)
        return dataset

    async def create_dataset_from_collection(
        self,
        tenant: TenantContext,
        *,
        collection_id: str,
        config: RAFTDatasetConfig | None = None,
    ) -> RAFTDatasetRecord:
        chunks = await self._repository.load_chunks(tenant.tenant_id, collection_id)
        if not chunks:
            raise RAFTNotFoundError("Persisted collection chunks not found")
        return await self.create_dataset(
            tenant,
            collection_id=collection_id,
            chunks=chunks,
            config=config,
        )

    async def preview_job(
        self,
        tenant: TenantContext,
        *,
        dataset_id: str,
        provider_id: str,
        base_model: str,
    ) -> RAFTCostPreview:
        dataset = await self._require_dataset(tenant, dataset_id)
        provider = self._require_provider(provider_id)
        cost = await provider.preview_cost(
            training_examples=len(dataset.train_examples),
            validation_examples=len(dataset.test_examples),
            base_model=base_model,
        )
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + self._confirmation_ttl
        await self._repository.save_confirmation(
            tenant.tenant_id,
            _ConfirmationGrant(
                tenant_id=tenant.tenant_id,
                dataset_id=dataset_id,
                provider_id=provider_id,
                base_model=base_model,
                cost=cost,
                token_hash=_token_hash(token),
                binding_digest=_confirmation_binding_digest(
                    token_hash=_token_hash(token),
                    tenant_id=tenant.tenant_id,
                    dataset_id=dataset_id,
                    provider_id=provider_id,
                    base_model=base_model,
                    cost=cost,
                    expires_at=expires_at,
                ),
                expires_at=expires_at,
            )
        )
        return RAFTCostPreview(
            dataset_id=dataset_id,
            provider_id=provider_id,
            base_model=base_model,
            estimated_cost=cost,
            confirmation_token=token,
            expires_at=expires_at,
        )

    async def submit_job(
        self,
        tenant: TenantContext,
        *,
        dataset_id: str,
        provider_id: str,
        base_model: str,
        confirmation_token: str,
    ) -> RAFTJobRecord:
        dataset = await self._require_dataset(tenant, dataset_id)
        provider = self._require_provider(provider_id)
        token_hash = _token_hash(confirmation_token)
        job_id = hashlib.sha256(
            f"raft-job:{tenant.tenant_id}:{token_hash}".encode()
        ).hexdigest()[:32]
        compatibility_key = _compatibility_key(
            dataset=dataset,
            provider_id=provider_id,
            base_model=base_model,
            capability=RAFT_INFERENCE_CAPABILITY,
        )
        pending_job = RAFTJobRecord(
            job_id=job_id,
            tenant_id=tenant.tenant_id,
            dataset_id=dataset_id,
            collection_id=dataset.collection_id,
            provider_id=provider_id,
            base_model=base_model,
            capability=RAFT_INFERENCE_CAPABILITY,
            compatibility_key=compatibility_key,
            status="pending",
            confirmation_digest="",
        )
        job = await self._repository.create_job_from_confirmation(
            tenant.tenant_id,
            token_hash,
            pending_job,
        )
        if job is None:
            raise ConfirmationRequiredError(
                "A fresh matching cost confirmation token is required"
            )
        should_submit = False
        if job.status == "pending":
            claimed = await self._repository.transition_job(
                tenant.tenant_id,
                replace(
                    job,
                    status="reconciling",
                    error=None,
                    updated_at=datetime.now(UTC),
                ),
                job.version,
            )
            if claimed is None:
                current = await self._repository.get_job(tenant.tenant_id, job.job_id)
                if current is None:
                    raise RAFTConcurrentUpdateError("RAFT submission claim was lost")
                return current
            job = claimed
            should_submit = True
        elif job.status != "reconciling":
            return job
        elif job.error is not None:
            should_submit = True
        if not should_submit:
            return job
        return await self._submit_reconciling_job(tenant, dataset, job, provider)

    async def reconcile_job(
        self,
        tenant: TenantContext,
        job_id: str,
    ) -> RAFTJobRecord:
        job = await self.get_job(tenant, job_id)
        if job.status not in {"pending", "reconciling"}:
            return job
        if job.status == "pending":
            claimed = await self._repository.transition_job(
                tenant.tenant_id,
                replace(
                    job,
                    status="reconciling",
                    error=None,
                    updated_at=datetime.now(UTC),
                ),
                job.version,
            )
            if claimed is None:
                current = await self.get_job(tenant, job_id)
                return current
            job = claimed
        dataset = await self._require_dataset(tenant, job.dataset_id)
        provider = self._require_provider(job.provider_id)
        return await self._submit_reconciling_job(tenant, dataset, job, provider)

    async def _submit_reconciling_job(
        self,
        tenant: TenantContext,
        dataset: RAFTDatasetRecord,
        job: RAFTJobRecord,
        provider: FineTuneProvider,
    ) -> RAFTJobRecord:
        try:
            provider_job_id = await provider.submit(
                training_jsonl=dataset.training_jsonl,
                validation_jsonl=dataset.test_jsonl,
                base_model=job.base_model,
                idempotency_key=job.job_id,
            )
        except Exception as exc:
            await self._transition(
                tenant.tenant_id,
                replace(
                    job,
                    status="reconciling",
                    error=type(exc).__name__,
                    updated_at=datetime.now(UTC),
                ),
            )
            raise RAFTError(
                f"Fine-tune submission outcome is reconciling for job {job.job_id}"
            ) from exc
        return await self._transition(
            tenant.tenant_id,
            replace(
                job,
                status="submitted",
                provider_job_id=provider_job_id,
                error=None,
                updated_at=datetime.now(UTC),
            ),
        )

    async def get_job(
        self, tenant: TenantContext, job_id: str
    ) -> RAFTJobRecord:
        job = await self._repository.get_job(tenant.tenant_id, job_id)
        if job is None:
            raise RAFTNotFoundError("RAFT job not found")
        return job

    async def refresh_job(
        self, tenant: TenantContext, job_id: str
    ) -> RAFTJobRecord:
        job = await self.get_job(tenant, job_id)
        if job.status in _TERMINAL_STATUSES:
            return job
        if not job.provider_job_id:
            raise RAFTError("RAFT job has not been submitted")
        state = await self._require_provider(job.provider_id).status(
            job.provider_job_id
        )
        updated = replace(
            job,
            status=state.status,
            fine_tuned_model=state.fine_tuned_model,
            error=state.error,
            updated_at=datetime.now(UTC),
        )
        return await self._transition(tenant.tenant_id, updated)

    async def evaluate_job(
        self, tenant: TenantContext, job_id: str
    ) -> RAFTJobRecord:
        job = await self.get_job(tenant, job_id)
        if job.status != "completed" or not job.fine_tuned_model:
            raise RAFTModelUnavailableError("RAFT job has no completed model to evaluate")
        dataset = await self._require_dataset(tenant, job.dataset_id)
        evaluation = await self._require_provider(job.provider_id).evaluate(
            model=job.fine_tuned_model,
            test_jsonl=dataset.test_jsonl,
        )
        updated = replace(
            job,
            evaluation=evaluation,
            updated_at=datetime.now(UTC),
        )
        return await self._transition(tenant.tenant_id, updated)

    async def require_completed_model(
        self,
        tenant: TenantContext,
        *,
        compatibility_key: str,
    ) -> RAFTJobRecord:
        job = await self._repository.find_completed_job(
            tenant.tenant_id,
            compatibility_key,
        )
        if job is None:
            raise RAFTModelUnavailableError(
                "A completed compatible RAFT model is required"
            )
        return job

    async def has_completed_model(
        self,
        tenant: TenantContext,
        *,
        collection_id: str,
    ) -> bool:
        """Check durable model and local inference compatibility without provider calls."""
        job = await self.find_completed_model(
            tenant,
            collection_id=collection_id,
        )
        return job is not None

    async def find_completed_model(
        self,
        tenant: TenantContext,
        *,
        collection_id: str,
        provider_id: str | None = None,
        capability: str | None = None,
        dataset_id: str | None = None,
        base_model: str | None = None,
    ) -> RAFTJobRecord | None:
        """Select the newest completed model after exact local compatibility filters."""
        if provider_id is not None:
            provider = self._inference_providers.get(provider_id)
            if provider is None or (
                capability is not None and provider.capability != capability
            ):
                return None
            provider_ids = frozenset({provider_id})
        else:
            provider_ids = frozenset(
                registered_provider_id
                for registered_provider_id, provider in self._inference_providers.items()
                if capability is None or provider.capability == capability
            )
        if not provider_ids:
            return None
        candidates = await self._repository.list_completed_models(
            tenant.tenant_id,
            collection_id,
            provider_ids=provider_ids,
            capability=capability,
            dataset_id=dataset_id,
            base_model=base_model,
        )
        return next(
            (
                job
                for job in candidates
                if self._inference_providers[job.provider_id].capability
                == job.capability
            ),
            None,
        )

    async def compatibility_key_for_dataset(
        self,
        tenant: TenantContext,
        *,
        dataset_id: str,
        collection_id: str,
        provider_id: str,
        base_model: str,
        capability: str,
    ) -> str:
        provider = self._inference_providers.get(provider_id)
        if provider is None or provider.capability != capability:
            raise RAFTModelUnavailableError("RAFT inference capability is unavailable")
        dataset = await self._repository.get_dataset(tenant.tenant_id, dataset_id)
        if dataset is None or dataset.collection_id != collection_id:
            raise RAFTModelUnavailableError("RAFT dataset is incompatible with collection")
        return _compatibility_key(
            dataset=dataset,
            provider_id=provider_id,
            base_model=base_model,
            capability=capability,
        )

    async def infer(
        self,
        job: RAFTJobRecord,
        *,
        query: str,
        evidence: tuple[str, ...],
    ) -> str:
        provider = self._inference_providers.get(job.provider_id)
        if (
            provider is None
            or provider.capability != job.capability
            or not job.fine_tuned_model
        ):
            raise RAFTModelUnavailableError("RAFT inference capability is unavailable")
        answer = await provider.infer(
            query=query,
            evidence=evidence,
            fine_tuned_model_id=job.fine_tuned_model,
        )
        if not answer.strip():
            raise RAFTModelUnavailableError("RAFT inference returned no answer")
        return answer.strip()

    async def _transition(
        self,
        tenant_id: str,
        updated: RAFTJobRecord,
    ) -> RAFTJobRecord:
        persisted = await self._repository.transition_job(
            tenant_id,
            updated,
            updated.version,
        )
        if persisted is None:
            current = await self._repository.get_job(tenant_id, updated.job_id)
            if current is not None and current.status in _TERMINAL_STATUSES:
                return current
            raise RAFTConcurrentUpdateError("RAFT job was updated concurrently")
        return persisted

    async def _require_dataset(
        self, tenant: TenantContext, dataset_id: str
    ) -> RAFTDatasetRecord:
        dataset = await self._repository.get_dataset(tenant.tenant_id, dataset_id)
        if dataset is None:
            raise RAFTNotFoundError("RAFT dataset not found")
        return dataset

    def _require_provider(self, provider_id: str) -> FineTuneProvider:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise RAFTNotFoundError("Fine-tune provider not found")
        return provider


def _build_dataset(
    tenant: TenantContext,
    collection_id: str,
    chunks: list[PersistedRAFTChunk],
    config: RAFTDatasetConfig,
) -> RAFTDatasetRecord:
    usable = [
        chunk
        for chunk in chunks
        if chunk.content.strip()
        and str(chunk.metadata.get("question", "")).strip()
        and str(chunk.metadata.get("answer", "")).strip()
    ]
    documents = sorted({chunk.document_id for chunk in usable})
    if len(documents) < 2:
        raise ValueError("RAFT datasets require chunks from at least two documents")
    if len(usable) <= config.distractors_per_example:
        raise ValueError("RAFT datasets do not contain enough distractors")
    rng = random.Random(config.seed)
    rng.shuffle(documents)
    test_count = min(
        len(documents) - 1,
        max(1, round(len(documents) * config.test_fraction)),
    )
    test_documents = set(documents[:test_count])
    examples: list[RAFTExample] = []
    for chunk in usable:
        is_test = chunk.document_id in test_documents
        candidates = [
            item
            for item in usable
            if item.chunk_id != chunk.chunk_id
            and (item.document_id in test_documents) is is_test
        ]
        if len(candidates) < config.distractors_per_example:
            raise ValueError(
                "Each RAFT split must contain enough chunks for its distractors"
            )
        distractors = rng.sample(candidates, config.distractors_per_example)
        question = str(chunk.metadata["question"]).strip()
        answer = str(chunk.metadata["answer"]).strip()
        examples.append(
            RAFTExample(
                example_id=uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"raft:{tenant.tenant_id}:{collection_id}:{chunk.chunk_id}",
                ).hex,
                document_id=chunk.document_id,
                question=question,
                answer=answer,
                oracle_chunk_id=chunk.chunk_id,
                oracle_context=chunk.content.strip(),
                distractor_chunk_ids=tuple(item.chunk_id for item in distractors),
                distractor_contexts=tuple(item.content.strip() for item in distractors),
                split="test" if is_test else "train",
            )
        )
    errors = _validate_examples(examples)
    if errors:
        raise ValueError("Invalid RAFT dataset: " + "; ".join(errors))
    return RAFTDatasetRecord(
        dataset_id=uuid.uuid4().hex,
        tenant_id=tenant.tenant_id,
        collection_id=collection_id,
        content_fingerprint=_dataset_content_fingerprint(examples, config),
        examples=tuple(examples),
        validation_errors=tuple(errors),
        created_at=datetime.now(UTC),
    )


def _require_matching_tenant(tenant_id: str, entity_tenant_id: str) -> None:
    if tenant_id != entity_tenant_id:
        raise ValueError("RAFT entity tenant does not match the trusted tenant")


def _validate_examples(examples: list[RAFTExample]) -> list[str]:
    errors: list[str] = []
    if not any(example.split == "train" for example in examples):
        errors.append("training split is empty")
    if not any(example.split == "test" for example in examples):
        errors.append("test split is empty")
    for example in examples:
        if not example.question.strip() or not example.answer.strip():
            errors.append(
                f"example {example.example_id} has an empty question or answer"
            )
        if example.oracle_chunk_id in example.distractor_chunk_ids:
            errors.append(
                f"example {example.example_id} includes its oracle as a distractor"
            )
        if len(set(example.distractor_chunk_ids)) != len(example.distractor_chunk_ids):
            errors.append(f"example {example.example_id} has duplicate distractors")
    return errors


def _export_jsonl(examples: tuple[RAFTExample, ...]) -> str:
    return "\n".join(
        json.dumps(example.to_json_record(), sort_keys=True, separators=(",", ":"))
        for example in examples
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _confirmation_binding_digest(
    *,
    token_hash: str,
    tenant_id: str,
    dataset_id: str,
    provider_id: str,
    base_model: str,
    cost: FineTuneCost,
    expires_at: datetime,
) -> str:
    payload = "\n".join(
        (
            token_hash,
            tenant_id,
            dataset_id,
            provider_id,
            base_model,
            cost.currency,
            cost.canonical_amount,
            expires_at.astimezone(UTC).isoformat(timespec="microseconds"),
        )
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _grant_matches_job(grant: _ConfirmationGrant, job: RAFTJobRecord) -> bool:
    expected_digest = _confirmation_binding_digest(
        token_hash=grant.token_hash,
        tenant_id=job.tenant_id,
        dataset_id=job.dataset_id,
        provider_id=job.provider_id,
        base_model=job.base_model,
        cost=grant.cost,
        expires_at=grant.expires_at,
    )
    return (
        grant.expires_at > datetime.now(UTC)
        and grant.dataset_id == job.dataset_id
        and grant.provider_id == job.provider_id
        and grant.base_model == job.base_model
        and secrets.compare_digest(grant.binding_digest, expected_digest)
    )


def _dataset_content_fingerprint(
    examples: list[RAFTExample],
    config: RAFTDatasetConfig,
) -> str:
    canonical = json.dumps(
        {
            "schema_version": _RAFT_DATASET_ARTIFACT_SCHEMA_VERSION,
            "generation_config": asdict(config),
            "training_records": [
                example.to_json_record()
                for example in examples
                if example.split == "train"
            ],
            "test_records": [
                example.to_json_record()
                for example in examples
                if example.split == "test"
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _job_confirmation_digest(
    token_hash: str,
    job: RAFTJobRecord,
    cost: FineTuneCost,
) -> str:
    canonical = json.dumps(
        {
            "token_hash": token_hash,
            "tenant_id": job.tenant_id,
            "dataset_id": job.dataset_id,
            "collection_id": job.collection_id,
            "provider_id": job.provider_id,
            "base_model": job.base_model,
            "capability": job.capability,
            "compatibility_key": job.compatibility_key,
            "cost": {
                "currency": cost.currency,
                "amount": cost.canonical_amount,
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _require_exact_job_retry(
    existing: RAFTJobRecord,
    pending: RAFTJobRecord,
    token_hash: str,
) -> None:
    immutable_existing = (
        existing.tenant_id,
        existing.dataset_id,
        existing.collection_id,
        existing.provider_id,
        existing.base_model,
        existing.capability,
        existing.compatibility_key,
    )
    immutable_pending = (
        pending.tenant_id,
        pending.dataset_id,
        pending.collection_id,
        pending.provider_id,
        pending.base_model,
        pending.capability,
        pending.compatibility_key,
    )
    cost = existing.estimated_cost
    expected_digest = (
        _job_confirmation_digest(token_hash, pending, cost)
        if cost is not None
        else ""
    )
    if (
        immutable_existing != immutable_pending
        or cost is None
        or not secrets.compare_digest(existing.confirmation_digest, expected_digest)
        or (pending.estimated_cost is not None and pending.estimated_cost != cost)
        or (
            bool(pending.confirmation_digest)
            and not secrets.compare_digest(
                pending.confirmation_digest,
                expected_digest,
            )
        )
    ):
        raise ConfirmationRequiredError(
            "Consumed confirmation request does not match the existing RAFT job"
        )


def _compatibility_key(
    *,
    dataset: RAFTDatasetRecord,
    provider_id: str,
    base_model: str,
    capability: str,
) -> str:
    canonical = json.dumps(
        {
            "dataset_content_version": dataset.content_fingerprint,
            "collection_id": dataset.collection_id,
            "provider_id": provider_id,
            "base_model": base_model,
            "capability": capability,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _require_legal_transition(
    current: RAFTJobRecord,
    updated: RAFTJobRecord,
) -> None:
    if updated.status not in _LEGAL_TRANSITIONS[current.status]:
        raise RAFTError(f"Illegal RAFT status transition: {current.status} -> {updated.status}")
    if current.evaluation is not None and updated.evaluation is None:
        raise RAFTError("RAFT evaluation cannot be erased")
    if (
        current.fine_tuned_model is not None
        and updated.fine_tuned_model != current.fine_tuned_model
    ):
        raise RAFTError("RAFT fine-tuned model cannot change")
    if (
        current.provider_job_id is not None
        and updated.provider_job_id != current.provider_job_id
    ):
        raise RAFTError("RAFT provider job identity cannot change")
    if updated.estimated_cost != current.estimated_cost:
        raise RAFTError("RAFT confirmed cost cannot change")


__all__ = [
    name
    for name in globals()
    if name.startswith("RAFT") or name.startswith("FineTune")
]
__all__.extend(
    ["ConfirmationRequiredError", "InMemoryRAFTRepository", "PersistedRAFTChunk"]
)