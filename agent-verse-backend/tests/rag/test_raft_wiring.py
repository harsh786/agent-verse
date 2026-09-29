"""RAFT wiring: the app's provider registry serves what the fine-tune adapter trains."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.providers.base import CompletionRequest
from app.rag.raft import (
    RAFT_INFERENCE_CAPABILITY,
    RAFT_SYSTEM_PROMPT,
    FineTuneJobState,
    InMemoryRAFTRepository,
    RAFTService,
    RAFTUnsupportedProviderError,
    raft_chat_jsonl,
)
from app.rag.raft_inference import LLMFineTunedInferenceProvider, build_raft_inference_providers
from app.rag.raft_openai_provider import OpenAIFineTuneProvider
from app.rag.raft_repository import SQLRAFTRepository
from app.rag.raft_wiring import build_raft_service, poll_raft_jobs_once
from app.tenancy.context import TenantContext
from tests.rag.test_raft_end_to_end import TENANT as TENANT_E2E
from tests.rag.test_raft_end_to_end import (
    RecordingInferenceProvider,
    ScriptedFineTuneProvider,
    _submitted_job,
)

TENANT = TenantContext(tenant_id="tenant-w", api_key_id="key-w", plan="enterprise")


def _settings(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "openai_api_key": "",
        "raft_max_training_chunks": 2000,
        "raft_chunk_page_size": 500,
        "raft_max_eval_examples": 50,
        "raft_poll_batch_size": 50,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_openai_key_wires_a_fine_tune_provider_and_the_registry_provider_that_serves_it() -> None:
    from app.providers.openai_compatible import OpenAICompatibleProvider

    service = build_raft_service(_settings(openai_api_key="sk-test-raft"))

    assert service.supported_provider_ids == frozenset({"openai"})
    inference = service._inference_providers["openai"]
    assert isinstance(inference, LLMFineTunedInferenceProvider)
    assert isinstance(inference._llm, OpenAICompatibleProvider)
    assert inference.capability == RAFT_INFERENCE_CAPABILITY


async def test_without_a_key_nothing_is_supported_and_job_creation_fails_fast() -> None:
    service = build_raft_service(_settings())

    assert service.supported_provider_ids == frozenset()
    with pytest.raises(RAFTUnsupportedProviderError, match="not supported"):
        await service.preview_job(
            TENANT, dataset_id="any", provider_id="openai", base_model="gpt-4o-mini"
        )


def test_db_backed_service_uses_sql_repository_with_configured_page_size() -> None:
    def factory() -> Any:
        raise AssertionError("not opened during construction")

    service = build_raft_service(
        _settings(openai_api_key="sk-test-raft", raft_chunk_page_size=7),
        session_factory=factory,
    )

    assert isinstance(service._repository, SQLRAFTRepository)
    assert service._repository._chunk_page_size == 7


def test_unmapped_fine_tune_provider_gets_no_serving_provider() -> None:
    fine_tune = SimpleNamespace(provider_id="acme")
    assert build_raft_inference_providers(_settings(openai_api_key="sk"), {"acme": fine_tune}) == {}  # type: ignore[dict-item]


async def test_inference_sends_the_fine_tuned_model_id_and_the_training_prompt_shape() -> None:
    requests: list[CompletionRequest] = []

    class LLM:
        async def complete(self, request: CompletionRequest) -> Any:
            requests.append(request)
            return SimpleNamespace(content="  grounded answer  ")

    provider = LLMFineTunedInferenceProvider(provider_id="openai", llm=LLM())

    answer = await provider.infer(
        query="What is X?",
        evidence=("doc one", "doc two"),
        fine_tuned_model_id="ft:gpt-4o-mini:org::raft1",
    )

    assert answer == "grounded answer"
    (request,) = requests
    assert request.model == "ft:gpt-4o-mini:org::raft1"
    assert request.messages[0].role == "system"
    assert request.messages[0].content == RAFT_SYSTEM_PROMPT
    assert "doc one" in str(request.messages[1].content)
    assert str(request.messages[1].content).endswith("Question: What is X?")


def test_inference_provider_requires_an_async_llm() -> None:
    with pytest.raises(TypeError):
        LLMFineTunedInferenceProvider(provider_id="openai", llm=SimpleNamespace(complete=None))


def test_chat_export_is_openai_chat_fine_tuning_format_with_shuffled_contexts() -> None:
    record = {
        "example_id": "ex-1",
        "question": "What is the refund window?",
        "answer": "30 days",
        "oracle_chunk_id": "c1",
        "oracle_context": "Refunds within 30 days.",
        "distractor_chunk_ids": ["c2", "c3"],
        "contexts": ["Refunds within 30 days.", "Shipping is free.", "Support is 24/7."],
    }

    lines = raft_chat_jsonl(json.dumps(record) + "\n").splitlines()

    (line,) = lines
    messages = json.loads(line)["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant"]
    assert messages[2]["content"] == "30 days"
    for context in record["contexts"]:
        assert context in messages[1]["content"]
    assert raft_chat_jsonl(json.dumps(record)) == line  # deterministic


async def test_openai_submit_uploads_chat_format_files(monkeypatch: pytest.MonkeyPatch) -> None:
    uploads: list[bytes] = []

    class Files:
        async def create(self, *, file: tuple[str, Any], purpose: str) -> Any:
            assert purpose == "fine-tune"
            uploads.append(file[1].getvalue())
            return SimpleNamespace(id=f"file-{len(uploads)}")

    class Jobs:
        async def create(self, **kwargs: Any) -> Any:
            assert kwargs["training_file"] == "file-1"
            assert kwargs["validation_file"] == "file-2"
            return SimpleNamespace(id="ftjob-1")

    client = SimpleNamespace(files=Files(), fine_tuning=SimpleNamespace(jobs=Jobs()))
    provider = OpenAIFineTuneProvider(api_key="sk-test")
    monkeypatch.setattr(provider, "_client", lambda: client)
    record = json.dumps({"example_id": "e", "question": "q?", "answer": "a", "contexts": ["ctx"]})

    job_id = await provider.submit(
        training_jsonl=record, validation_jsonl=record, base_model="gpt", idempotency_key="k"
    )

    assert job_id == "ftjob-1"
    for payload in uploads:
        assert set(json.loads(payload.decode())) == {"messages"}


# ── the poll entrypoint used by the Celery beat task ─────────────────────────


async def test_poll_once_skips_when_no_fine_tune_provider_is_configured() -> None:
    service = RAFTService(repository=InMemoryRAFTRepository(), providers={})

    assert await poll_raft_jobs_once(service=service) == {
        "status": "skipped",
        "reason": "no_fine_tune_provider_configured",
    }


async def test_poll_once_advances_jobs_and_reports_the_summary() -> None:
    fine_tune = ScriptedFineTuneProvider(
        script=[FineTuneJobState(status="completed", fine_tuned_model="ft:model")]
    )
    inference = RecordingInferenceProvider()
    service = RAFTService(
        repository=InMemoryRAFTRepository(),
        providers={fine_tune.provider_id: fine_tune},
        inference_providers={inference.provider_id: inference},
    )
    job = await _submitted_job(service)

    result = await poll_raft_jobs_once(service=service, limit=5)

    assert result == {
        "status": "ok",
        "scanned": 1,
        "advanced": 1,
        "completed": 1,
        "failed": 0,
        "errors": 0,
    }
    assert (await service.get_job(TENANT_E2E, job.job_id)).status == "completed"
