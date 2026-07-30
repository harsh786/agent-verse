"""Lifecycle coverage for provider-neutral, tenant-scoped RAFT training."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.rag_platform import router as rag_router
from app.rag.agentic.patterns.raft import RAFTRAGRuntimeAdapter
from app.rag.contracts import RAGExecutionRequest, RAGStrategy
from app.rag.engine import RetrievalResult, RetrievalStrategyExecutionError
from app.rag.gateway import RetrievalExecutionContext, RetrievalRuntimeDependencies
from app.rag.raft import (
    RAFT_INFERENCE_CAPABILITY,
    ConfirmationRequiredError,
    FineTuneCost,
    FineTuneEvaluation,
    FineTuneJobState,
    InMemoryRAFTRepository,
    PersistedRAFTChunk,
    RAFTDatasetConfig,
    RAFTDatasetRecord,
    RAFTError,
    RAFTExample,
    RAFTJobRecord,
    RAFTService,
    _compatibility_key,
    _dataset_content_fingerprint,
    _token_hash,
)
from app.tenancy.context import TenantContext
from app.tenancy.middleware import TenantMiddleware

TENANT = TenantContext(tenant_id="tenant-1", api_key_id="key-1", plan="enterprise")
OTHER_TENANT = TenantContext(
    tenant_id="tenant-2",
    api_key_id="key-2",
    plan="enterprise",
)


@dataclass
class RecordingFineTuneProvider:
    submit_count: int = 0
    inference_calls: list[tuple[str, tuple[str, ...], str]] = field(default_factory=list)

    @property
    def provider_id(self) -> str:
        return "recording"

    @property
    def capability(self) -> str:
        return RAFT_INFERENCE_CAPABILITY

    async def preview_cost(
        self,
        *,
        training_examples: int,
        validation_examples: int,
        base_model: str,
    ) -> FineTuneCost:
        assert base_model == "base-model"
        return FineTuneCost(
            currency="USD",
            estimated_amount=(
                Decimal(training_examples + validation_examples) / Decimal(100)
            ),
        )

    async def submit(
        self,
        *,
        training_jsonl: str,
        validation_jsonl: str,
        base_model: str,
        idempotency_key: str,
    ) -> str:
        del training_jsonl, validation_jsonl, base_model, idempotency_key
        self.submit_count += 1
        return "provider-job-1"

    async def status(self, provider_job_id: str) -> FineTuneJobState:
        assert provider_job_id == "provider-job-1"
        return FineTuneJobState(
            status="completed",
            fine_tuned_model="raft-model-1",
        )

    async def evaluate(
        self,
        *,
        model: str,
        test_jsonl: str,
    ) -> FineTuneEvaluation:
        assert model == "raft-model-1"
        assert test_jsonl
        return FineTuneEvaluation(metrics={"accuracy": 0.9})

    async def infer(
        self,
        *,
        query: str,
        evidence: tuple[str, ...],
        fine_tuned_model_id: str,
    ) -> str:
        self.inference_calls.append((query, evidence, fine_tuned_model_id))
        return "Fine-tuned answer from persisted evidence."


def chunks() -> list[PersistedRAFTChunk]:
    return [
        PersistedRAFTChunk(
            chunk_id=f"chunk-{index}",
            document_id=f"document-{index // 3}",
            content=f"Policy fact {index}",
            metadata={
                "question": f"What is policy fact {index}?",
                "answer": f"Policy fact {index}",
            },
        )
        for index in range(12)
    ]


async def test_dataset_builds_oracles_distractors_and_document_isolated_splits() -> None:
    repository = InMemoryRAFTRepository()
    service = RAFTService(repository=repository, providers={})

    dataset = await service.create_dataset(
        TENANT,
        collection_id="collection-1",
        chunks=chunks(),
        config=RAFTDatasetConfig(distractors_per_example=2, test_fraction=0.25, seed=7),
    )

    assert dataset.tenant_id == TENANT.tenant_id
    assert dataset.collection_id == "collection-1"
    assert len(dataset.content_fingerprint) == 64
    assert not dataset.validation_errors
    assert dataset.train_examples
    assert dataset.test_examples
    assert {example.oracle_chunk_id for example in dataset.train_examples}.isdisjoint(
        {example.oracle_chunk_id for example in dataset.test_examples}
    )
    train_documents = {example.document_id for example in dataset.train_examples}
    test_documents = {example.document_id for example in dataset.test_examples}
    assert train_documents.isdisjoint(test_documents)
    train_chunk_ids = {
        chunk_id
        for example in dataset.train_examples
        for chunk_id in (example.oracle_chunk_id, *example.distractor_chunk_ids)
    }
    test_chunk_ids = {
        chunk_id
        for example in dataset.test_examples
        for chunk_id in (example.oracle_chunk_id, *example.distractor_chunk_ids)
    }
    assert train_chunk_ids.isdisjoint(test_chunk_ids)
    assert all(len(example.distractor_chunk_ids) == 2 for example in dataset.examples)
    assert all(
        example.oracle_chunk_id not in example.distractor_chunk_ids
        for example in dataset.examples
    )
    exported = [json.loads(line) for line in dataset.training_jsonl.splitlines()]
    assert len(exported) == len(dataset.train_examples)
    assert all(record["oracle_context"] in record["contexts"] for record in exported)

    persisted = await repository.get_dataset(TENANT.tenant_id, dataset.dataset_id)
    assert persisted == dataset
    assert (
        await repository.get_dataset(OTHER_TENANT.tenant_id, dataset.dataset_id)
        is None
    )


async def test_dataset_artifact_fingerprint_includes_generation_config() -> None:
    service = RAFTService(repository=InMemoryRAFTRepository(), providers={})
    configs = (
        RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25, seed=7),
        RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25, seed=8),
        RAFTDatasetConfig(distractors_per_example=2, test_fraction=0.5, seed=7),
    )

    datasets = [
        await service.create_dataset(
            TENANT,
            collection_id="collection-1",
            chunks=chunks(),
            config=config,
        )
        for config in configs
    ]
    compatibility_keys = {
        _compatibility_key(
            dataset=dataset,
            provider_id="recording",
            base_model="base-model",
            capability=RAFT_INFERENCE_CAPABILITY,
        )
        for dataset in datasets
    }

    assert len({dataset.content_fingerprint for dataset in datasets}) == len(configs)
    assert len(compatibility_keys) == len(configs)


def _fingerprint_examples() -> list[RAFTExample]:
    return [
        RAFTExample(
            example_id="example-train",
            document_id="document-train",
            question="Training question?",
            answer="Training answer.",
            oracle_chunk_id="oracle-train",
            oracle_context="Training oracle context.",
            distractor_chunk_ids=("distractor-train",),
            distractor_contexts=("Training distractor context.",),
            split="train",
        ),
        RAFTExample(
            example_id="example-test",
            document_id="document-test",
            question="Test question?",
            answer="Test answer.",
            oracle_chunk_id="oracle-test",
            oracle_context="Test oracle context.",
            distractor_chunk_ids=("distractor-test",),
            distractor_contexts=("Test distractor context.",),
            split="test",
        ),
    ]


def _compatibility_key_for_fingerprint(content_fingerprint: str) -> str:
    dataset = RAFTDatasetRecord(
        dataset_id="dataset-1",
        tenant_id=TENANT.tenant_id,
        collection_id="collection-1",
        content_fingerprint=content_fingerprint,
        examples=tuple(_fingerprint_examples()),
        validation_errors=(),
        created_at=datetime.now(UTC),
    )
    return _compatibility_key(
        dataset=dataset,
        provider_id="recording",
        base_model="base-model",
        capability=RAFT_INFERENCE_CAPABILITY,
    )


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda example: replace(example, example_id="changed"),
            id="example-id",
        ),
        pytest.param(lambda example: replace(example, split="test"), id="split"),
        pytest.param(lambda example: replace(example, question="Changed?"), id="question"),
        pytest.param(lambda example: replace(example, answer="Changed."), id="answer"),
        pytest.param(
            lambda example: replace(example, oracle_chunk_id="changed-oracle"),
            id="oracle-chunk-id",
        ),
        pytest.param(
            lambda example: replace(example, oracle_context="Changed oracle context."),
            id="oracle-context",
        ),
        pytest.param(
            lambda example: replace(
                example,
                distractor_chunk_ids=("changed-distractor",),
            ),
            id="distractor-chunk-ids",
        ),
        pytest.param(
            lambda example: replace(
                example,
                distractor_contexts=("Changed distractor context.",),
            ),
            id="distractor-contexts",
        ),
    ],
)
def test_dataset_artifact_fingerprint_covers_each_canonical_record_field(
    mutate: Callable[[RAFTExample], RAFTExample],
) -> None:
    config = RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25, seed=7)
    examples = _fingerprint_examples()
    original_fingerprint = _dataset_content_fingerprint(examples, config)
    changed_fingerprint = _dataset_content_fingerprint(
        [mutate(examples[0]), examples[1]],
        config,
    )

    assert changed_fingerprint != original_fingerprint
    assert _compatibility_key_for_fingerprint(
        changed_fingerprint
    ) != _compatibility_key_for_fingerprint(original_fingerprint)


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(
            RAFTDatasetConfig(distractors_per_example=2, test_fraction=0.25, seed=7),
            id="distractors-per-example",
        ),
        pytest.param(
            RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.5, seed=7),
            id="test-fraction",
        ),
        pytest.param(
            RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25, seed=8),
            id="seed",
        ),
    ],
)
def test_dataset_artifact_fingerprint_covers_each_generation_config_field(
    config: RAFTDatasetConfig,
) -> None:
    examples = _fingerprint_examples()
    original_fingerprint = _dataset_content_fingerprint(
        examples,
        RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25, seed=7),
    )
    changed_fingerprint = _dataset_content_fingerprint(examples, config)

    assert changed_fingerprint != original_fingerprint
    assert _compatibility_key_for_fingerprint(
        changed_fingerprint
    ) != _compatibility_key_for_fingerprint(original_fingerprint)


def test_dataset_artifact_fingerprint_ignores_record_mapping_insertion_order() -> None:
    examples = _fingerprint_examples()
    config = RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25, seed=7)
    original_fingerprint = _dataset_content_fingerprint(examples, config)
    original_to_json_record = RAFTExample.to_json_record

    def reversed_record(example: RAFTExample) -> dict[str, object]:
        record = original_to_json_record(example)
        return dict(reversed(tuple(record.items())))

    with patch.object(RAFTExample, "to_json_record", reversed_record):
        reordered_fingerprint = _dataset_content_fingerprint(examples, config)

    assert reordered_fingerprint == original_fingerprint
    assert _compatibility_key_for_fingerprint(
        reordered_fingerprint
    ) == _compatibility_key_for_fingerprint(original_fingerprint)


async def test_paid_submission_requires_action_time_confirmation() -> None:
    provider = RecordingFineTuneProvider()
    service = RAFTService(
        repository=InMemoryRAFTRepository(),
        providers={provider.provider_id: provider},
    )
    dataset = await service.create_dataset(
        TENANT,
        collection_id="collection-1",
        chunks=chunks(),
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25, seed=3),
    )

    preview = await service.preview_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
    )

    with pytest.raises(ConfirmationRequiredError):
        await service.submit_job(
            TENANT,
            dataset_id=dataset.dataset_id,
            provider_id=provider.provider_id,
            base_model="base-model",
            confirmation_token="",
        )

    assert preview.confirmation_token
    assert preview.estimated_cost.estimated_amount > 0
    assert provider.submit_count == 0


@pytest.mark.parametrize(
    "amount",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-0.01"), Decimal("1.0000001")],
)
def test_fine_tune_cost_rejects_invalid_exact_money(amount: Decimal) -> None:
    with pytest.raises(ValueError):
        FineTuneCost(currency="USD", estimated_amount=amount)


def test_fine_tune_cost_normalizes_currency_and_canonical_decimal() -> None:
    cost = FineTuneCost(currency=" usd ", estimated_amount=Decimal("1.25"))

    assert cost.currency == "USD"
    assert cost.estimated_amount == Decimal("1.250000")
    assert cost.canonical_amount == "1.250000"


async def test_dataset_rejects_uncurated_chunk_fallbacks() -> None:
    service = RAFTService(repository=InMemoryRAFTRepository(), providers={})
    uncurated = [
        PersistedRAFTChunk(
            chunk_id=f"chunk-{index}",
            document_id=f"document-{index // 3}",
            content=f"Policy fact {index}",
        )
        for index in range(12)
    ]

    with pytest.raises(ValueError, match="at least two documents"):
        await service.create_dataset(
            TENANT,
            collection_id="collection-1",
            chunks=uncurated,
        )


async def test_expired_confirmation_never_submits_paid_work() -> None:
    provider = RecordingFineTuneProvider()
    service = RAFTService(
        repository=InMemoryRAFTRepository(),
        providers={provider.provider_id: provider},
        confirmation_ttl=timedelta(seconds=-1),
    )
    dataset = await service.create_dataset(
        TENANT,
        collection_id="collection-1",
        chunks=chunks(),
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25),
    )
    preview = await service.preview_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
    )

    with pytest.raises(ConfirmationRequiredError):
        await service.submit_job(
            TENANT,
            dataset_id=dataset.dataset_id,
            provider_id=provider.provider_id,
            base_model="base-model",
            confirmation_token=preview.confirmation_token,
        )

    assert provider.submit_count == 0


async def test_wrong_binding_does_not_consume_confirmation_grant() -> None:
    provider = RecordingFineTuneProvider()
    service = RAFTService(
        repository=InMemoryRAFTRepository(),
        providers={provider.provider_id: provider},
    )
    dataset = await service.create_dataset(
        TENANT,
        collection_id="collection-1",
        chunks=chunks(),
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25),
    )
    preview = await service.preview_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
    )

    with pytest.raises(ConfirmationRequiredError):
        await service.submit_job(
            TENANT,
            dataset_id=dataset.dataset_id,
            provider_id=provider.provider_id,
            base_model="different-model",
            confirmation_token=preview.confirmation_token,
        )

    submitted = await service.submit_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
        confirmation_token=preview.confirmation_token,
    )

    assert submitted.status == "submitted"
    assert provider.submit_count == 1


async def test_confirmed_job_lifecycle_is_durable_and_token_is_one_time() -> None:
    provider = RecordingFineTuneProvider()
    repository = InMemoryRAFTRepository()
    service = RAFTService(
        repository=repository,
        providers={provider.provider_id: provider},
    )
    dataset = await service.create_dataset(
        TENANT,
        collection_id="collection-1",
        chunks=chunks(),
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25, seed=5),
    )
    preview = await service.preview_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
    )

    submitted = await service.submit_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
        confirmation_token=preview.confirmation_token,
    )

    duplicate = await service.submit_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
        confirmation_token=preview.confirmation_token,
    )

    completed = await service.refresh_job(TENANT, submitted.job_id)
    evaluated = await service.evaluate_job(TENANT, submitted.job_id)

    assert provider.submit_count == 1
    assert duplicate == submitted
    assert submitted.estimated_cost == preview.estimated_cost
    assert completed.status == "completed"
    assert completed.fine_tuned_model == "raft-model-1"
    assert evaluated.evaluation == FineTuneEvaluation(metrics={"accuracy": 0.9})
    assert await repository.get_job(TENANT.tenant_id, submitted.job_id) == evaluated
    assert await repository.get_job(OTHER_TENANT.tenant_id, submitted.job_id) is None


async def test_consumed_confirmation_retry_rejects_changed_dataset_without_provider_call() -> None:
    provider = RecordingFineTuneProvider()
    service = RAFTService(
        repository=InMemoryRAFTRepository(),
        providers={provider.provider_id: provider},
    )
    first_dataset = await service.create_dataset(
        TENANT,
        collection_id="collection-1",
        chunks=chunks(),
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25, seed=5),
    )
    changed_dataset = await service.create_dataset(
        TENANT,
        collection_id="collection-1",
        chunks=chunks(),
        config=RAFTDatasetConfig(distractors_per_example=2, test_fraction=0.25, seed=5),
    )
    preview = await service.preview_job(
        TENANT,
        dataset_id=first_dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
    )
    submitted = await service.submit_job(
        TENANT,
        dataset_id=first_dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
        confirmation_token=preview.confirmation_token,
    )

    exact_retry = await service.submit_job(
        TENANT,
        dataset_id=first_dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
        confirmation_token=preview.confirmation_token,
    )
    with pytest.raises(ConfirmationRequiredError, match="request does not match"):
        await service.submit_job(
            TENANT,
            dataset_id=changed_dataset.dataset_id,
            provider_id=provider.provider_id,
            base_model="base-model",
            confirmation_token=preview.confirmation_token,
        )

    assert exact_retry == submitted
    assert provider.submit_count == 1


async def test_in_memory_consumed_confirmation_compares_every_immutable_field() -> None:
    provider = RecordingFineTuneProvider()
    repository = InMemoryRAFTRepository()
    service = RAFTService(
        repository=repository,
        providers={provider.provider_id: provider},
    )
    dataset = await service.create_dataset(
        TENANT,
        collection_id="collection-1",
        chunks=chunks(),
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25),
    )
    preview = await service.preview_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
    )
    submitted = await service.submit_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
        confirmation_token=preview.confirmation_token,
    )
    pending = replace(
        submitted,
        status="pending",
        confirmation_digest="",
        estimated_cost=None,
        provider_job_id=None,
        version=0,
    )
    mismatches: tuple[RAFTJobRecord, ...] = (
        replace(pending, tenant_id=OTHER_TENANT.tenant_id),
        replace(pending, dataset_id="different-dataset"),
        replace(pending, collection_id="different-collection"),
        replace(pending, provider_id="different-provider"),
        replace(pending, base_model="different-model"),
        replace(pending, capability="different-capability"),
        replace(pending, compatibility_key="f" * 64),
        replace(pending, confirmation_digest="e" * 64),
        replace(
            pending,
            estimated_cost=FineTuneCost("USD", Decimal("9.00")),
        ),
    )

    for mismatch in mismatches:
        with pytest.raises(ConfirmationRequiredError, match="does not match"):
            await repository.create_job_from_confirmation(
                TENANT.tenant_id,
                _token_hash(preview.confirmation_token),
                mismatch,
            )

    assert provider.submit_count == 1


async def test_older_compatible_raft_model_wins_over_newer_incompatible() -> None:
    repository = InMemoryRAFTRepository()
    provider = RecordingFineTuneProvider()
    service = RAFTService(
        repository=repository,
        providers={},
        inference_providers={provider.provider_id: provider},
    )
    now = datetime.now(UTC)
    compatible = RAFTJobRecord(
        job_id="compatible",
        tenant_id=TENANT.tenant_id,
        dataset_id="dataset-compatible",
        collection_id="collection-1",
        provider_id=provider.provider_id,
        base_model="base-model",
        capability=RAFT_INFERENCE_CAPABILITY,
        compatibility_key="compatible-key",
        status="completed",
        confirmation_digest="digest",
        fine_tuned_model="compatible-model",
        updated_at=now,
    )
    incompatible = replace(
        compatible,
        job_id="incompatible",
        dataset_id="dataset-incompatible",
        provider_id="not-registered",
        compatibility_key="incompatible-key",
        fine_tuned_model="newer-incompatible-model",
        updated_at=now + timedelta(minutes=1),
    )
    await repository.save_job(TENANT.tenant_id, compatible)
    await repository.save_job(TENANT.tenant_id, incompatible)

    selected = await service.find_completed_model(
        TENANT,
        collection_id="collection-1",
        provider_id=provider.provider_id,
        capability=RAFT_INFERENCE_CAPABILITY,
    )

    assert selected == compatible
    assert await service.has_completed_model(TENANT, collection_id="collection-1")


async def test_stale_update_cannot_regress_terminal_job_or_erase_evaluation() -> None:
    provider = RecordingFineTuneProvider()
    repository = InMemoryRAFTRepository()
    service = RAFTService(
        repository=repository,
        providers={provider.provider_id: provider},
    )
    dataset = await service.create_dataset(
        TENANT,
        collection_id="collection-1",
        chunks=chunks(),
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25),
    )
    preview = await service.preview_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
    )
    stale = await service.submit_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
        confirmation_token=preview.confirmation_token,
    )
    await service.refresh_job(TENANT, stale.job_id)
    evaluated = await service.evaluate_job(TENANT, stale.job_id)

    lost = await repository.transition_job(
        TENANT.tenant_id,
        replace(stale, status="running", evaluation=None, fine_tuned_model=None),
        stale.version,
    )

    assert lost is None
    assert await repository.get_job(TENANT.tenant_id, stale.job_id) == evaluated


@dataclass
class ResponseLostProvider(RecordingFineTuneProvider):
    idempotency_keys: list[str] = field(default_factory=list)

    async def submit(
        self,
        *,
        training_jsonl: str,
        validation_jsonl: str,
        base_model: str,
        idempotency_key: str,
    ) -> str:
        del training_jsonl, validation_jsonl, base_model
        self.submit_count += 1
        self.idempotency_keys.append(idempotency_key)
        if self.submit_count == 1:
            raise TimeoutError("provider accepted but response was lost")
        return "provider-job-1"


async def test_response_lost_retry_reuses_durable_job_and_idempotency_key() -> None:
    provider = ResponseLostProvider()
    repository = InMemoryRAFTRepository()
    service = RAFTService(
        repository=repository,
        providers={provider.provider_id: provider},
    )
    dataset = await service.create_dataset(
        TENANT,
        collection_id="collection-1",
        chunks=chunks(),
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25),
    )
    preview = await service.preview_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
    )

    with pytest.raises(RAFTError, match="reconciling"):
        await service.submit_job(
            TENANT,
            dataset_id=dataset.dataset_id,
            provider_id=provider.provider_id,
            base_model="base-model",
            confirmation_token=preview.confirmation_token,
        )

    reconciling_job_id = provider.idempotency_keys[0]
    assert (await service.get_job(TENANT, reconciling_job_id)).status == "reconciling"
    submitted = await service.reconcile_job(TENANT, reconciling_job_id)

    assert submitted.status == "submitted"
    assert provider.idempotency_keys == [submitted.job_id, submitted.job_id]


@dataclass
class IdempotentProvider(RecordingFineTuneProvider):
    idempotency_keys: list[str] = field(default_factory=list)

    async def submit(
        self,
        *,
        training_jsonl: str,
        validation_jsonl: str,
        base_model: str,
        idempotency_key: str,
    ) -> str:
        del training_jsonl, validation_jsonl, base_model
        self.submit_count += 1
        self.idempotency_keys.append(idempotency_key)
        return "provider-job-1"


class BlockingProvider(IdempotentProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def submit(
        self,
        *,
        training_jsonl: str,
        validation_jsonl: str,
        base_model: str,
        idempotency_key: str,
    ) -> str:
        self.started.set()
        await self.release.wait()
        return await super().submit(
            training_jsonl=training_jsonl,
            validation_jsonl=validation_jsonl,
            base_model=base_model,
            idempotency_key=idempotency_key,
        )


async def test_concurrent_submit_has_one_provider_contact() -> None:
    provider = BlockingProvider()
    service = RAFTService(
        repository=InMemoryRAFTRepository(),
        providers={provider.provider_id: provider},
    )
    dataset = await service.create_dataset(
        TENANT,
        collection_id="collection-1",
        chunks=chunks(),
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25),
    )
    preview = await service.preview_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
    )

    async def submit() -> Any:
        return await service.submit_job(
            TENANT,
            dataset_id=dataset.dataset_id,
            provider_id=provider.provider_id,
            base_model="base-model",
            confirmation_token=preview.confirmation_token,
        )

    winner = asyncio.create_task(submit())
    await provider.started.wait()
    observer = await submit()

    assert observer.status == "reconciling"
    assert provider.submit_count == 0
    provider.release.set()
    submitted = await winner
    assert submitted.status == "submitted"
    assert provider.submit_count == 1


class CrashAfterClaimRepository(InMemoryRAFTRepository):
    def __init__(self) -> None:
        super().__init__()
        self.should_crash = True
        self.last_transition_job_id = ""

    async def transition_job(
        self,
        tenant_id: str,
        record: Any,
        expected_version: int,
    ) -> Any:
        self.last_transition_job_id = record.job_id
        updated = await super().transition_job(tenant_id, record, expected_version)
        if self.should_crash and record.status == "reconciling":
            self.should_crash = False
            raise RuntimeError("crash after durable claim")
        return updated


class CrashAfterProviderAcceptedRepository(InMemoryRAFTRepository):
    def __init__(self) -> None:
        super().__init__()
        self.should_crash = True
        self.last_transition_job_id = ""

    async def transition_job(
        self,
        tenant_id: str,
        record: Any,
        expected_version: int,
    ) -> Any:
        self.last_transition_job_id = record.job_id
        if self.should_crash and record.status == "submitted":
            self.should_crash = False
            raise RuntimeError("crash before submitted state persisted")
        return await super().transition_job(tenant_id, record, expected_version)


@pytest.mark.parametrize(
    "repository_type",
    [CrashAfterClaimRepository, CrashAfterProviderAcceptedRepository],
)
async def test_crash_boundaries_reconcile_by_durable_job_id(
    repository_type: type[InMemoryRAFTRepository],
) -> None:
    provider = IdempotentProvider()
    repository = repository_type()
    service = RAFTService(
        repository=repository,
        providers={provider.provider_id: provider},
    )
    dataset = await service.create_dataset(
        TENANT,
        collection_id="collection-1",
        chunks=chunks(),
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25),
    )
    preview = await service.preview_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
    )

    with pytest.raises(RuntimeError, match="crash"):
        await service.submit_job(
            TENANT,
            dataset_id=dataset.dataset_id,
            provider_id=provider.provider_id,
            base_model="base-model",
            confirmation_token=preview.confirmation_token,
        )

    observed_repository: Any = repository
    durable_job = await service.get_job(
        TENANT,
        observed_repository.last_transition_job_id,
    )
    assert durable_job.status == "reconciling"
    recovered = await service.reconcile_job(TENANT, durable_job.job_id)

    assert recovered.status == "submitted"
    assert set(provider.idempotency_keys) <= {recovered.job_id}
    assert provider.submit_count in {1, 2}


def execution_request(dataset_id: str = "dataset-1") -> RAGExecutionRequest:
    return RAGExecutionRequest(
        tenant_id=TENANT.tenant_id,
        query="What is policy fact 1?",
        requested_strategy_id=RAGStrategy.RAFT.value,
        collection_id="collection-1",
        top_k=2,
        filters={
            "raft_dataset_id": dataset_id,
            "raft_provider_id": "recording",
            "raft_base_model": "base-model",
            "raft_capability": RAFT_INFERENCE_CAPABILITY,
        },
    )


def execution_context() -> RetrievalExecutionContext:
    async def run(operation: Any) -> Any:
        return await operation(SimpleNamespace())

    return RetrievalExecutionContext(
        tenant_context=TENANT,
        strategy=RAGStrategy.RAFT,
        filters={},
        dependencies=RetrievalRuntimeDependencies(
            embedder=SimpleNamespace(),
            llm=None,
            graph_capability=None,
            search_capability=None,
            policy_services=(),
        ),
        _db_operation_runner=run,
    )


async def _completed_service() -> tuple[RAFTService, RecordingFineTuneProvider, str, str]:
    provider = RecordingFineTuneProvider()
    service = RAFTService(
        repository=InMemoryRAFTRepository(),
        providers={provider.provider_id: provider},
        inference_providers={provider.provider_id: provider},
    )
    dataset = await service.create_dataset(
        TENANT,
        collection_id="collection-1",
        chunks=chunks(),
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25, seed=2),
    )
    preview = await service.preview_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
    )
    job = await service.submit_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider.provider_id,
        base_model="base-model",
        confirmation_token=preview.confirmation_token,
    )
    await service.refresh_job(TENANT, job.job_id)
    return service, provider, job.job_id, dataset.dataset_id


async def test_raft_retrieval_requires_model_and_traces_job_and_model_ids() -> None:
    unavailable = RAFTService(repository=InMemoryRAFTRepository(), providers={})
    adapter = RAFTRAGRuntimeAdapter(unavailable)

    with pytest.raises(
        RetrievalStrategyExecutionError,
        match="RAFT inference capability is unavailable",
    ):
        await adapter.execute(execution_request(), execution_context())

    service, provider, job_id, dataset_id = await _completed_service()
    assert await service.has_completed_model(
        TENANT,
        collection_id="collection-1",
    )
    assert provider.inference_calls == []
    results = [
        RetrievalResult(
            chunk_id="chunk-1",
            content="Policy fact 1",
            score=0.9,
            source_metadata={"source": "policy.pdf"},
            retrieval_legs=["hybrid"],
        )
    ]

    async def embed(*args: object, **kwargs: object) -> list[float]:
        del args, kwargs
        return [1.0, 0.0]

    async def search(*args: object, **kwargs: object) -> list[RetrievalResult]:
        del args, kwargs
        return results

    with (
        patch("app.rag.gateway._embed_text", side_effect=embed),
        patch("app.rag.gateway._search_persisted", side_effect=search),
    ):
        result = await RAFTRAGRuntimeAdapter(service).execute(
            execution_request(dataset_id),
            execution_context(),
        )

    trace = next(item for item in result.strategy_trace if item.action == "raft_retrieval")
    assert trace.detail["job_id"] == job_id
    assert trace.detail["model_id"] == "raft-model-1"
    assert trace.detail["compatibility_key"]
    assert result.answer == "Fine-tuned answer from persisted evidence."
    assert provider.inference_calls == [
        ("What is policy fact 1?", ("Policy fact 1",), "raft-model-1")
    ]
    assert result.citations[0].chunk_id == "chunk-1"


async def test_raft_rejects_incompatible_dataset_model_binding() -> None:
    service, _, _, _ = await _completed_service()

    with pytest.raises(
        RetrievalStrategyExecutionError,
        match="dataset is incompatible",
    ):
        await RAFTRAGRuntimeAdapter(service).execute(
            execution_request("different-dataset"),
            execution_context(),
        )


def raft_api(service: RAFTService) -> TestClient:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return TENANT if key == "raft-api-key" else None

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(rag_router)
    app.state.raft_service = service
    return TestClient(app, raise_server_exceptions=False)


def test_raft_lifecycle_api_is_authenticated_and_rejects_unconfirmed_submission() -> None:
    provider = RecordingFineTuneProvider()
    repository = InMemoryRAFTRepository()
    repository.seed_chunks(TENANT.tenant_id, "collection-1", chunks())
    service = RAFTService(
        repository=repository,
        providers={provider.provider_id: provider},
    )
    client = raft_api(service)

    unauthorized = client.post(
        "/rag/raft/datasets",
        json={"collection_id": "collection-1"},
    )
    dataset_response = client.post(
        "/rag/raft/datasets",
        json={
            "collection_id": "collection-1",
            "distractors_per_example": 1,
            "test_fraction": 0.25,
            "seed": 4,
        },
        headers={"X-API-Key": "raft-api-key"},
    )
    dataset_id = dataset_response.json()["dataset_id"]
    preview = client.post(
        "/rag/raft/jobs/preview",
        json={
            "dataset_id": dataset_id,
            "provider_id": provider.provider_id,
            "base_model": "base-model",
        },
        headers={"X-API-Key": "raft-api-key"},
    )
    rejected = client.post(
        "/rag/raft/jobs",
        json={
            "dataset_id": dataset_id,
            "provider_id": provider.provider_id,
            "base_model": "base-model",
            "confirmation_token": "",
        },
        headers={"X-API-Key": "raft-api-key"},
    )

    assert unauthorized.status_code == 401
    assert dataset_response.status_code == 201, dataset_response.text
    assert preview.status_code == 200, preview.text
    assert preview.json()["confirmation_token"]
    assert rejected.status_code == 409
    assert provider.submit_count == 0
