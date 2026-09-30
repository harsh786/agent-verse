"""End-to-end RAFT: create → background poll → deploy → serve the fine-tuned model.

A fake fine-tune provider walks a job through ``queued → running → succeeded``
and a fake inference provider records which model id actually answered, so these
tests prove the retrieval strategy synthesizes with the *fine-tuned* model — and
refuses (unavailable) rather than quietly answering with anything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    FineTuneCost,
    FineTuneJobState,
    InMemoryRAFTRepository,
    PersistedRAFTChunk,
    RAFTDatasetConfig,
    RAFTJobRecord,
    RAFTJobStateError,
    RAFTModelUnavailableError,
    RAFTService,
    RAFTUnsupportedProviderError,
)
from app.tenancy.context import TenantContext
from app.tenancy.middleware import TenantMiddleware

TENANT = TenantContext(
    tenant_id="tenant-e2e", api_key_id="key-e2e", plan="enterprise", roles=("admin",)
)
OTHER = TenantContext(
    tenant_id="tenant-other", api_key_id="key-other", plan="enterprise", roles=("admin",)
)
COLLECTION = "collection-e2e"
FINE_TUNED_MODEL = "ft:base-model:tenant-e2e:raft:abc123"


@dataclass
class ScriptedFineTuneProvider:
    """Reports the queued → running → succeeded sequence, one step per poll."""

    provider_id: str = "scripted"
    script: list[FineTuneJobState] = field(
        default_factory=lambda: [
            FineTuneJobState(status="submitted"),
            FineTuneJobState(status="running"),
            FineTuneJobState(status="completed", fine_tuned_model=FINE_TUNED_MODEL),
        ]
    )
    status_calls: int = 0
    submissions: list[str] = field(default_factory=list)
    status_error: Exception | None = None

    async def preview_cost(
        self, *, training_examples: int, validation_examples: int, base_model: str
    ) -> FineTuneCost:
        del base_model
        return FineTuneCost(
            currency="USD",
            estimated_amount=Decimal(training_examples + validation_examples) / Decimal(100),
        )

    async def submit(
        self,
        *,
        training_jsonl: str,
        validation_jsonl: str,
        base_model: str,
        idempotency_key: str,
    ) -> str:
        del training_jsonl, validation_jsonl, base_model
        self.submissions.append(idempotency_key)
        return f"provider-job-{len(self.submissions)}"

    async def status(self, provider_job_id: str) -> FineTuneJobState:
        del provider_job_id
        if self.status_error is not None:
            raise self.status_error
        state = self.script[min(self.status_calls, len(self.script) - 1)]
        self.status_calls += 1
        return state


@dataclass
class RecordingInferenceProvider:
    provider_id: str = "scripted"
    capability: str = RAFT_INFERENCE_CAPABILITY
    calls: list[tuple[str, tuple[str, ...], str]] = field(default_factory=list)
    answer: str = "Fine-tuned grounded answer."

    async def infer(
        self,
        *,
        query: str,
        evidence: tuple[str, ...],
        fine_tuned_model_id: str,
        tenant_id: str | None = None,
    ) -> str:
        self.calls.append((query, evidence, fine_tuned_model_id))
        return self.answer


def _chunks(count: int = 12) -> list[PersistedRAFTChunk]:
    return [
        PersistedRAFTChunk(
            chunk_id=f"chunk-{index:03d}",
            document_id=f"document-{index // 3}",
            content=f"Policy fact {index}",
            metadata={"question": f"What is fact {index}?", "answer": f"Policy fact {index}"},
        )
        for index in range(count)
    ]


def _service(
    repository: InMemoryRAFTRepository | None = None,
    *,
    fine_tune: ScriptedFineTuneProvider | None = None,
    inference: RecordingInferenceProvider | None = None,
    **kwargs: Any,
) -> RAFTService:
    fine_tune = fine_tune or ScriptedFineTuneProvider()
    return RAFTService(
        repository=repository or InMemoryRAFTRepository(),
        providers={fine_tune.provider_id: fine_tune},
        inference_providers={inference.provider_id: inference} if inference else {},
        **kwargs,
    )


async def _submitted_job(service: RAFTService, provider_id: str = "scripted") -> RAFTJobRecord:
    dataset = await service.create_dataset(
        TENANT,
        collection_id=COLLECTION,
        chunks=_chunks(),
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25, seed=7),
    )
    preview = await service.preview_job(
        TENANT, dataset_id=dataset.dataset_id, provider_id=provider_id, base_model="base-model"
    )
    return await service.submit_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id=provider_id,
        base_model="base-model",
        confirmation_token=preview.confirmation_token,
    )


def _request(**filters: Any) -> RAGExecutionRequest:
    return RAGExecutionRequest(
        tenant_id=TENANT.tenant_id,
        query="What is fact 1?",
        requested_strategy_id=RAGStrategy.RAFT.value,
        collection_id=COLLECTION,
        top_k=2,
        filters=filters,
    )


def _context() -> RetrievalExecutionContext:
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


_EVIDENCE = [
    RetrievalResult(
        chunk_id="chunk-001",
        content="Policy fact 1",
        score=0.9,
        source_metadata={"source": "policy.pdf"},
        retrieval_legs=["hybrid"],
    )
]


async def _execute(service: RAFTService, request: RAGExecutionRequest) -> Any:
    async def embed(*args: object, **kwargs: object) -> list[float]:
        del args, kwargs
        return [1.0, 0.0]

    async def search(*args: object, **kwargs: object) -> list[RetrievalResult]:
        del args, kwargs
        return list(_EVIDENCE)

    with (
        patch("app.rag.gateway._embed_text", side_effect=embed),
        patch("app.rag.gateway._search_persisted", side_effect=search),
    ):
        return await RAFTRAGRuntimeAdapter(service).execute(request, _context())


# ── the full lifecycle ───────────────────────────────────────────────────────


async def test_created_job_is_polled_to_completion_deployed_and_served_by_fine_tuned_model() -> (
    None
):
    fine_tune = ScriptedFineTuneProvider()
    inference = RecordingInferenceProvider()
    service = _service(fine_tune=fine_tune, inference=inference)
    job = await _submitted_job(service)
    assert job.status == "submitted"

    # Not servable before completion + deployment.
    assert not await service.has_servable_model(TENANT, collection_id=COLLECTION)
    with pytest.raises(RAFTModelUnavailableError):
        await service.deploy_job(TENANT, job.job_id)

    statuses = []
    for _ in range(3):
        summary = await service.poll_in_flight_jobs(limit=10)
        assert summary.scanned == 1
        statuses.append((await service.get_job(TENANT, job.job_id)).status)
    assert statuses == ["submitted", "running", "completed"]
    completed = await service.get_job(TENANT, job.job_id)
    assert completed.fine_tuned_model == FINE_TUNED_MODEL

    # A terminal job is no longer in flight.
    assert (await service.poll_in_flight_jobs(limit=10)).scanned == 0

    # Completed but not deployed → still not served.
    assert not await service.has_servable_model(TENANT, collection_id=COLLECTION)
    with pytest.raises(RetrievalStrategyExecutionError, match="no RAFT model is deployed"):
        await _execute(service, _request())

    deployment = await service.deploy_job(TENANT, job.job_id)
    assert deployment.collection_id == COLLECTION
    assert deployment.job_id == job.job_id
    assert await service.has_servable_model(TENANT, collection_id=COLLECTION)
    assert not await service.has_servable_model(OTHER, collection_id=COLLECTION)

    result = await _execute(service, _request())

    assert result.answer == "Fine-tuned grounded answer."
    assert inference.calls == [("What is fact 1?", ("Policy fact 1",), FINE_TUNED_MODEL)]
    trace = next(item for item in result.strategy_trace if item.action == "raft_retrieval")
    assert trace.status == "complete"
    assert trace.detail["model_id"] == FINE_TUNED_MODEL
    assert trace.detail["job_id"] == job.job_id
    assert result.citations[0].chunk_id == "chunk-001"


async def test_exact_raft_filters_must_match_the_deployed_model() -> None:
    inference = RecordingInferenceProvider()
    service = _service(inference=inference)
    job = await _submitted_job(service)
    for _ in range(3):
        await service.poll_in_flight_jobs(limit=10)
    await service.deploy_job(TENANT, job.job_id)

    pinned = await _execute(
        service,
        _request(
            raft_dataset_id=job.dataset_id,
            raft_provider_id="scripted",
            raft_base_model="base-model",
            raft_capability=RAFT_INFERENCE_CAPABILITY,
        ),
    )
    assert pinned.answer

    with pytest.raises(RetrievalStrategyExecutionError, match="does not match the deployed"):
        await _execute(service, _request(raft_dataset_id="some-other-dataset"))
    assert len(inference.calls) == 1


async def test_deployed_model_without_a_serving_provider_is_unavailable_not_base_model() -> None:
    repository = InMemoryRAFTRepository()
    inference = RecordingInferenceProvider()
    service = _service(repository, inference=inference)
    job = await _submitted_job(service)
    for _ in range(3):
        await service.poll_in_flight_jobs(limit=10)
    await service.deploy_job(TENANT, job.job_id)

    # Same durable state, but the process that serves it has no inference provider.
    unservable = _service(repository, inference=None)

    assert not await unservable.has_servable_model(TENANT, collection_id=COLLECTION)
    with pytest.raises(RetrievalStrategyExecutionError, match="no configured inference provider"):
        await _execute(unservable, _request())
    assert inference.calls == []


async def test_raft_adapter_without_service_is_unavailable_instead_of_degrading() -> None:
    with pytest.raises(RetrievalStrategyExecutionError, match="raft_service_unavailable"):
        await RAFTRAGRuntimeAdapter(None).execute(_request(), _context())


# ── job creation fails fast for unsupported providers ────────────────────────


async def test_unknown_fine_tune_provider_fails_at_job_creation() -> None:
    service = _service(inference=RecordingInferenceProvider())
    dataset = await service.create_dataset(
        TENANT,
        collection_id=COLLECTION,
        chunks=_chunks(),
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25),
    )

    with pytest.raises(RAFTUnsupportedProviderError, match="not supported"):
        await service.preview_job(
            TENANT, dataset_id=dataset.dataset_id, provider_id="acme", base_model="base-model"
        )
    with pytest.raises(RAFTUnsupportedProviderError, match="not supported"):
        await service.submit_job(
            TENANT,
            dataset_id=dataset.dataset_id,
            provider_id="acme",
            base_model="base-model",
            confirmation_token="anything",
        )


async def test_fine_tune_provider_without_serving_provider_fails_at_job_creation() -> None:
    fine_tune = ScriptedFineTuneProvider()
    service = _service(fine_tune=fine_tune, inference=None)
    dataset = await service.create_dataset(
        TENANT,
        collection_id=COLLECTION,
        chunks=_chunks(),
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25),
    )

    with pytest.raises(RAFTUnsupportedProviderError, match="no configured inference provider"):
        await service.preview_job(
            TENANT, dataset_id=dataset.dataset_id, provider_id="scripted", base_model="base-model"
        )
    assert fine_tune.submissions == []


def test_provider_registry_rejects_objects_that_are_not_fine_tune_providers() -> None:
    with pytest.raises(TypeError, match="FineTuneProvider"):
        RAFTService(repository=InMemoryRAFTRepository(), providers={"x": object()})  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="provider_id"):
        RAFTService(
            repository=InMemoryRAFTRepository(),
            providers={"wrong-key": ScriptedFineTuneProvider()},
        )


# ── poller failure recording and bounds ──────────────────────────────────────


async def test_poller_records_provider_failure_status() -> None:
    fine_tune = ScriptedFineTuneProvider(
        script=[FineTuneJobState(status="failed", error="training diverged")]
    )
    service = _service(fine_tune=fine_tune, inference=RecordingInferenceProvider())
    job = await _submitted_job(service)

    summary = await service.poll_in_flight_jobs(limit=10)

    failed = await service.get_job(TENANT, job.job_id)
    assert failed.status == "failed"
    assert failed.error == "training diverged"
    assert summary.failed == 1


async def test_poller_records_status_errors_without_changing_state_and_continues() -> None:
    fine_tune = ScriptedFineTuneProvider(status_error=ConnectionError("provider down"))
    service = _service(fine_tune=fine_tune, inference=RecordingInferenceProvider())
    job = await _submitted_job(service)

    summary = await service.poll_in_flight_jobs(limit=10)

    current = await service.get_job(TENANT, job.job_id)
    assert current.status == "submitted"
    assert current.error == "status_poll_failed:ConnectionError"
    assert summary.errors == 1

    fine_tune.status_error = None
    await service.poll_in_flight_jobs(limit=10)
    recovered = await service.get_job(TENANT, job.job_id)
    assert recovered.status == "submitted"
    assert recovered.error is None


async def test_poller_records_error_when_job_provider_is_no_longer_configured() -> None:
    repository = InMemoryRAFTRepository()
    service = _service(repository, inference=RecordingInferenceProvider())
    job = await _submitted_job(service)
    other = ScriptedFineTuneProvider(provider_id="other")
    reconfigured = RAFTService(repository=repository, providers={"other": other})

    summary = await reconfigured.poll_in_flight_jobs(limit=10)

    assert summary.errors == 1
    current = await reconfigured.get_job(TENANT, job.job_id)
    assert current.status == "submitted"
    assert current.error == "status_poll_failed:RAFTNotFoundError"


async def test_poller_batch_is_bounded_and_oldest_first() -> None:
    repository = InMemoryRAFTRepository()
    service = _service(repository, inference=RecordingInferenceProvider())
    first = await _submitted_job(service)
    second = await _submitted_job(service)
    third = await _submitted_job(service)
    base = datetime.now(UTC) - timedelta(hours=1)
    for offset, job in enumerate((third, first, second)):
        current = await repository.get_job(TENANT.tenant_id, job.job_id)
        assert current is not None
        repository._jobs[(TENANT.tenant_id, job.job_id)] = _with_updated_at(
            current, base + timedelta(minutes=offset)
        )

    refs = await repository.list_in_flight_jobs(limit=2)

    assert refs == [(TENANT.tenant_id, third.job_id), (TENANT.tenant_id, first.job_id)]
    summary = await service.poll_in_flight_jobs(limit=2)
    assert summary.scanned == 2
    with pytest.raises(ValueError):
        await service.poll_in_flight_jobs(limit=0)


def _with_updated_at(job: RAFTJobRecord, when: datetime) -> RAFTJobRecord:
    from dataclasses import replace

    return replace(job, updated_at=when)


# ── collection loading is capped ─────────────────────────────────────────────


async def test_training_set_is_capped_by_the_configured_chunk_limit() -> None:
    repository = InMemoryRAFTRepository()
    repository.seed_chunks(TENANT.tenant_id, COLLECTION, _chunks(30))
    service = _service(repository, inference=RecordingInferenceProvider(), max_training_chunks=9)

    dataset = await service.create_dataset_from_collection(
        TENANT,
        collection_id=COLLECTION,
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.34),
    )

    assert len(dataset.examples) == 9
    with pytest.raises(ValueError):
        _service(max_training_chunks=0)


# ── evaluation is real inference over the held-out split ─────────────────────


async def test_evaluation_scores_held_out_examples_with_the_fine_tuned_model() -> None:
    inference = RecordingInferenceProvider(answer="The answer is Policy fact 1.")
    service = _service(inference=inference, max_eval_examples=2)
    job = await _submitted_job(service)
    for _ in range(3):
        await service.poll_in_flight_jobs(limit=10)

    evaluated = await service.evaluate_job(TENANT, job.job_id)

    assert evaluated.evaluation is not None
    metrics = evaluated.evaluation.metrics
    assert metrics["evaluated_examples"] == 2.0
    assert 0.0 <= metrics["answer_match_rate"] <= 1.0
    assert {call[2] for call in inference.calls} == {FINE_TUNED_MODEL}
    assert len(inference.calls) == 2


# ── HTTP surface ─────────────────────────────────────────────────────────────


def _client(service: RAFTService) -> TestClient:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return TENANT if key == "e2e-key" else None

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(rag_router)
    app.state.raft_service = service
    return TestClient(app, raise_server_exceptions=False)


HEADERS = {"X-API-Key": "e2e-key"}


def test_api_rejects_unsupported_provider_with_422() -> None:
    repository = InMemoryRAFTRepository()
    repository.seed_chunks(TENANT.tenant_id, COLLECTION, _chunks())
    client = _client(_service(repository, inference=RecordingInferenceProvider()))
    dataset = client.post(
        "/rag/raft/datasets",
        json={"collection_id": COLLECTION, "distractors_per_example": 1, "test_fraction": 0.25},
        headers=HEADERS,
    )
    assert dataset.status_code == 201, dataset.text

    preview = client.post(
        "/rag/raft/jobs/preview",
        json={
            "dataset_id": dataset.json()["dataset_id"],
            "provider_id": "acme",
            "base_model": "base-model",
        },
        headers=HEADERS,
    )

    assert preview.status_code == 422
    assert "not supported" in preview.json()["detail"]


async def test_api_deploy_and_deployment_lookup() -> None:
    inference = RecordingInferenceProvider()
    service = _service(inference=inference)
    job = await _submitted_job(service)
    client = _client(service)

    too_early = client.post(f"/rag/raft/jobs/{job.job_id}/deploy", headers=HEADERS)
    assert too_early.status_code == 409
    missing = client.get(f"/rag/raft/collections/{COLLECTION}/deployment", headers=HEADERS)
    assert missing.status_code == 404

    for _ in range(3):
        await service.poll_in_flight_jobs(limit=10)
    deployed = client.post(f"/rag/raft/jobs/{job.job_id}/deploy", headers=HEADERS)
    assert deployed.status_code == 200, deployed.text
    assert deployed.json()["fine_tuned_model"] == FINE_TUNED_MODEL
    assert deployed.json()["collection_id"] == COLLECTION

    lookup = client.get(f"/rag/raft/collections/{COLLECTION}/deployment", headers=HEADERS)
    assert lookup.status_code == 200
    assert lookup.json()["job_id"] == job.job_id


async def test_api_refresh_of_unsubmitted_job_is_a_conflict_not_an_outage() -> None:
    repository = InMemoryRAFTRepository()
    service = _service(repository, inference=RecordingInferenceProvider())
    job = await _submitted_job(service)
    from dataclasses import replace

    repository._jobs[(TENANT.tenant_id, job.job_id)] = replace(
        job, status="reconciling", provider_job_id=None
    )
    with pytest.raises(RAFTJobStateError):
        await service.refresh_job(TENANT, job.job_id)

    response = _client(service).post(f"/rag/raft/jobs/{job.job_id}/refresh", headers=HEADERS)

    assert response.status_code == 409


# ── gateway readiness follows deployment + serving provider ──────────────────


async def test_gateway_raft_readiness_requires_a_deployed_servable_model() -> None:
    from app.rag.catalogue import RAGRuntimeDependency
    from app.rag.gateway import RetrievalDependencies, RetrievalGateway

    inference = RecordingInferenceProvider()
    service = _service(inference=inference)

    def gateway() -> RetrievalGateway:
        return RetrievalGateway(
            RetrievalDependencies(
                session_factory=None,
                collection_authorizer=SimpleNamespace(),  # type: ignore[arg-type]
                strategy_capabilities={},
                raft_service=service,
            )
        )

    async def raft_fact() -> Any:
        context = await gateway().readiness_context(
            TENANT,
            collection_id=COLLECTION,
            probe_database=False,
            strategies=(RAGStrategy.RAFT,),
            resolve_providers=False,
        )
        return context.facts[RAGRuntimeDependency.RAFT_MODEL]

    job = await _submitted_job(service)
    for _ in range(3):
        await service.poll_in_flight_jobs(limit=10)
    assert (await raft_fact()).reason == "raft_model_unavailable"  # completed, not deployed

    await service.deploy_job(TENANT, job.job_id)
    fact = await raft_fact()
    assert fact.available
    assert fact.reason == "ready"


# ── the retriever never lets the base model answer as RAFT ───────────────────


async def test_retriever_refuses_base_model_synthesis_for_answerless_raft_result() -> None:
    from app.rag.gateway import ResolvedLLM, _canonical_result
    from app.rag_platform.retriever import RAGRetriever, RAGSynthesisError

    base_calls: list[object] = []

    class BaseModel:
        async def complete(self, request: object) -> object:
            base_calls.append(request)
            return SimpleNamespace(content="base model answer")

    answerless = _canonical_result(_request(), RAGStrategy.RAFT, list(_EVIDENCE), [])
    assert not answerless.answer
    assert answerless.citations

    class Gateway:
        dependencies = SimpleNamespace(
            llm_resolver=lambda tenant, strategy: ResolvedLLM(BaseModel(), "base-model")
        )

        async def execute(self, tenant: object, **kwargs: object) -> object:
            del tenant, kwargs
            return answerless

    with pytest.raises(RAGSynthesisError, match="fine-tuned"):
        await RAGRetriever(gateway=Gateway()).retrieve(
            "What is fact 1?", TENANT, collection_id=COLLECTION, strategy=RAGStrategy.RAFT
        )
    assert base_calls == []
