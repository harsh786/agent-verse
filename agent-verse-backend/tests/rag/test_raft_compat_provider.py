"""KB-32: RAFT fine-tuning beyond OpenAI — an OpenAI-compatible vendor adapter.

All HTTP is mocked (``httpx.MockTransport``): the adapter has never been run
against a real vendor fine-tune here.
"""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from app.rag.raft import FineTuneProvider, InMemoryRAFTRepository, RAFTService
from app.rag.raft_compat_provider import (
    CompatFineTuneError,
    OpenAICompatibleFineTuneProvider,
    build_compat_fine_tune_provider,
    map_compat_status,
)
from app.rag.raft_openai_provider import OpenAIFineTuneProvider, build_raft_providers
from tests.rag.test_raft_end_to_end import (
    COLLECTION,
    TENANT,
    RecordingInferenceProvider,
    _chunks,
)

BASE = "https://ft.vendor.example/v1"


class _Vendor:
    """A scripted OpenAI-compatible fine-tuning API."""

    def __init__(self, *, statuses: list[dict[str, Any]] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.files: list[dict[str, Any]] = []
        self.jobs: list[dict[str, Any]] = []
        self.statuses = statuses or [
            {"id": "ftjob-1", "status": "queued"},
            {"id": "ftjob-1", "status": "running"},
            {"id": "ftjob-1", "status": "succeeded", "fine_tuned_model": "ft:llama:acme:raft:1"},
        ]
        self.fail_create: tuple[int, dict[str, Any]] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/v1/files":
            body = request.content.decode("utf-8", "replace")
            self.files.append({"body": body})
            return httpx.Response(200, json={"id": f"file-{len(self.files)}", "object": "file"})
        if request.method == "POST" and path == "/v1/fine_tuning/jobs":
            if self.fail_create is not None:
                return httpx.Response(self.fail_create[0], json=self.fail_create[1])
            self.jobs.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "ftjob-1", "status": "validating_files"})
        if request.method == "GET" and path == "/v1/fine_tuning/jobs/ftjob-1":
            state = self.statuses[0] if len(self.statuses) == 1 else self.statuses.pop(0)
            return httpx.Response(200, json=state)
        return httpx.Response(404, json={"error": {"message": "not found"}})


def _provider(vendor: _Vendor, **kwargs: Any) -> OpenAICompatibleFineTuneProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(vendor.handler))
    return OpenAICompatibleFineTuneProvider(
        base_url=BASE, api_key="vendor-key", client=client, **kwargs
    )


_TRAIN = json.dumps({"question": "What is X?", "contexts": ["X is 1."], "answer": "1"})


async def test_submit_uploads_chat_splits_and_creates_the_job() -> None:
    vendor = _Vendor()
    provider = _provider(vendor)
    job_id = await provider.submit(
        training_jsonl=_TRAIN, validation_jsonl=_TRAIN, base_model="llama-3-8b",
        idempotency_key="idem-1",
    )
    assert job_id == "ftjob-1"
    assert len(vendor.files) == 2
    for upload in vendor.files:
        assert 'name="purpose"' in upload["body"] and "fine-tune" in upload["body"]
        assert '"messages"' in upload["body"]  # chat fine-tuning format
    assert vendor.jobs == [
        {
            "model": "llama-3-8b",
            "training_file": "file-1",
            "validation_file": "file-2",
            "metadata": {"raft_idempotency_key": "idem-1"},
        }
    ]
    create = next(r for r in vendor.requests if r.url.path == "/v1/fine_tuning/jobs")
    assert create.headers["Authorization"] == "Bearer vendor-key"
    assert create.headers["Idempotency-Key"] == "idem-1"
    assert all(r.headers["Authorization"] == "Bearer vendor-key" for r in vendor.requests)


async def test_submit_without_validation_split_uploads_only_training() -> None:
    vendor = _Vendor()
    await _provider(vendor).submit(
        training_jsonl=_TRAIN, validation_jsonl="", base_model="m", idempotency_key="k"
    )
    assert len(vendor.files) == 1
    assert "validation_file" not in vendor.jobs[0]


async def test_empty_training_split_is_refused_before_any_request() -> None:
    vendor = _Vendor()
    with pytest.raises(ValueError, match="empty"):
        await _provider(vendor).submit(
            training_jsonl="", validation_jsonl="", base_model="m", idempotency_key="k"
        )
    assert vendor.requests == []


async def test_vendor_error_is_surfaced_with_its_message() -> None:
    vendor = _Vendor()
    vendor.fail_create = (400, {"error": {"message": "model llama-x is not fine-tunable"}})
    with pytest.raises(CompatFineTuneError, match=r"HTTP 400.*not fine-tunable"):
        await _provider(vendor).submit(
            training_jsonl=_TRAIN, validation_jsonl="", base_model="llama-x", idempotency_key="k"
        )


async def test_status_maps_the_vendor_lifecycle() -> None:
    vendor = _Vendor()
    provider = _provider(vendor)
    states = [await provider.status("ftjob-1") for _ in range(3)]
    assert [s.status for s in states] == ["submitted", "running", "completed"]
    assert states[-1].fine_tuned_model == "ft:llama:acme:raft:1"


async def test_success_without_a_model_id_is_not_complete() -> None:
    vendor = _Vendor(statuses=[{"id": "ftjob-1", "status": "succeeded"}])
    state = await _provider(vendor).status("ftjob-1")
    assert state.status == "running"


async def test_failed_job_carries_the_vendor_error() -> None:
    vendor = _Vendor(
        statuses=[{"id": "ftjob-1", "status": "failed", "error": {"message": "bad jsonl"}}]
    )
    state = await _provider(vendor).status("ftjob-1")
    assert state.status == "failed" and state.error == "bad jsonl"


def test_status_mapping_never_reads_an_unknown_status_as_terminal() -> None:
    assert map_compat_status("SUCCESS") == "completed"
    assert map_compat_status("cancelled") == "failed"
    assert map_compat_status("compressing") == "running"


async def test_preview_cost_uses_the_configured_price() -> None:
    provider = _provider(_Vendor(), usd_per_training_example=Decimal("0.05"))
    cost = await provider.preview_cost(training_examples=10, validation_examples=0, base_model="m")
    assert cost.currency == "USD" and cost.estimated_amount == Decimal("0.500000")


async def test_private_endpoint_is_blocked_unless_trusted() -> None:
    provider = OpenAICompatibleFineTuneProvider(base_url="http://10.0.0.5/v1", api_key="k")
    with pytest.raises(CompatFineTuneError, match="blocked"):
        await provider.status("ftjob-1")


def test_adapter_satisfies_the_fine_tune_provider_protocol() -> None:
    provider = OpenAICompatibleFineTuneProvider(base_url=BASE, api_key="k", provider_id="acme")
    assert isinstance(provider, FineTuneProvider)
    assert provider.provider_id == "acme"


# ── Registry wiring ──────────────────────────────────────────────────────────


def _settings(**values: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "openai_api_key": "",
        "raft_compat_fine_tune_base_url": "",
        "raft_compat_fine_tune_api_key": "",
        "raft_compat_fine_tune_provider_id": "openai_compatible",
        "raft_compat_fine_tune_usd_per_example": "0.008",
        "raft_compat_fine_tune_allow_internal": False,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_compat_provider_is_registered_from_settings() -> None:
    providers = build_raft_providers(
        _settings(
            openai_api_key="sk-openai",
            raft_compat_fine_tune_base_url=BASE,
            raft_compat_fine_tune_api_key="vendor-key",
            raft_compat_fine_tune_provider_id="acme",
        )
    )
    assert isinstance(providers["openai"], OpenAIFineTuneProvider)
    assert isinstance(providers["acme"], OpenAICompatibleFineTuneProvider)


def test_compat_provider_needs_both_url_and_key() -> None:
    assert build_compat_fine_tune_provider(_settings(raft_compat_fine_tune_base_url=BASE)) is None
    assert build_raft_providers(_settings(raft_compat_fine_tune_api_key="k")) == {}


def test_compat_provider_cannot_shadow_openai() -> None:
    providers = build_raft_providers(
        _settings(
            openai_api_key="sk-openai",
            raft_compat_fine_tune_base_url=BASE,
            raft_compat_fine_tune_api_key="vendor-key",
            raft_compat_fine_tune_provider_id="openai",
        )
    )
    assert isinstance(providers["openai"], OpenAIFineTuneProvider)


def test_compat_models_are_served_by_the_vendors_openai_compatible_endpoint() -> None:
    from app.rag.raft_inference import (
        LLMFineTunedInferenceProvider,
        build_raft_inference_providers,
    )

    settings = _settings(
        raft_compat_fine_tune_base_url=BASE,
        raft_compat_fine_tune_api_key="vendor-key",
        raft_compat_fine_tune_provider_id="acme",
    )
    calls: list[dict[str, Any]] = []

    class _LLM:
        async def complete(self, request: Any) -> Any:
            raise AssertionError("not called")

    def fake_instantiate(provider_type: str, **kwargs: Any) -> Any:
        calls.append({"provider_type": provider_type, **kwargs})
        return _LLM()

    with patch("app.providers.registry.instantiate_configured_provider", fake_instantiate):
        serving = build_raft_inference_providers(settings, build_raft_providers(settings))
    assert isinstance(serving["acme"], LLMFineTunedInferenceProvider)
    assert calls[0]["provider_type"] == "openai_compatible"
    assert calls[0]["base_url"] == BASE
    assert calls[0]["api_key"] == "vendor-key"


# ── Lifecycle through RAFTService ────────────────────────────────────────────


async def test_raft_lifecycle_on_a_compat_vendor() -> None:
    from app.rag.raft import RAFTDatasetConfig

    vendor = _Vendor()
    fine_tune = _provider(vendor, provider_id="acme")
    inference = RecordingInferenceProvider(provider_id="acme")
    service = RAFTService(
        repository=InMemoryRAFTRepository(),
        providers={"acme": fine_tune},
        inference_providers={"acme": inference},
    )
    dataset = await service.create_dataset(
        TENANT,
        collection_id=COLLECTION,
        chunks=_chunks(),
        config=RAFTDatasetConfig(distractors_per_example=1, test_fraction=0.25, seed=7),
    )
    preview = await service.preview_job(
        TENANT, dataset_id=dataset.dataset_id, provider_id="acme", base_model="llama-3-8b"
    )
    job = await service.submit_job(
        TENANT,
        dataset_id=dataset.dataset_id,
        provider_id="acme",
        base_model="llama-3-8b",
        confirmation_token=preview.confirmation_token,
    )
    assert job.provider_job_id == "ftjob-1"
    for _ in range(3):
        await service.poll_in_flight_jobs(limit=10)
    done = await service.get_job(TENANT, job.job_id)
    assert done.status == "completed"
    assert done.fine_tuned_model == "ft:llama:acme:raft:1"
    await service.deploy_job(TENANT, job.job_id)
    assert await service.has_servable_model(TENANT, collection_id=COLLECTION)
