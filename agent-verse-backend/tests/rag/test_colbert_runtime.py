"""Behavioral coverage for the canonical ColBERT runtime."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.providers.base import EmbedRequest, EmbedResponse
from app.rag.agentic.patterns.colbert import (
    ColBERTLateInteractionReranker,
    ColBERTRAGRuntimeAdapter,
    _load_colbert_model,
    maxsim_score,
)
from app.rag.catalogue import RAGAdapterConfiguration, ReadinessFact
from app.rag.contracts import RAGExecutionRequest, RAGStrategy
from app.rag.engine import RetrievalResult
from app.rag.gateway import RetrievalExecutionContext, core_strategy_capabilities
from app.rag.readiness import probe_colbert_readiness
from app.rag_platform.reranker import (
    CrossEncoderDocumentReranker,
    RerankerInferenceError,
    RerankerLoadError,
    build_reranker,
)


class RecordingRAGatouilleModel:
    def __init__(self) -> None:
        self.load_thread_id = threading.get_ident()
        self.inference_thread_ids: list[int] = []
        self.calls: list[tuple[str, list[str], int]] = []

    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        k: int,
    ) -> list[dict[str, object]]:
        self.inference_thread_ids.append(threading.get_ident())
        self.calls.append((query, documents, k))
        return [
            {
                "content": documents[index],
                "score": float(index),
                "rank": rank,
                "result_index": index,
            }
            for rank, index in enumerate(reversed(range(len(documents))), start=1)
        ]


class DeterministicEmbedder:
    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        assert request.texts == ["python database"]
        return EmbedResponse(embeddings=[[1.0, 0.0]])


def request(top_k: int = 2) -> RAGExecutionRequest:
    return RAGExecutionRequest(
        tenant_id="tenant-1",
        query="python database",
        requested_strategy_id=RAGStrategy.COLBERT.value,
        collection_id="collection-1",
        top_k=top_k,
    )


def context() -> RetrievalExecutionContext:
    async def unused_runner(operation: Any) -> Any:
        return await operation(SimpleNamespace())

    return RetrievalExecutionContext(
        tenant_context=SimpleNamespace(tenant_id="tenant-1"),
        strategy=RAGStrategy.COLBERT,
        filters={},
        dependencies=SimpleNamespace(embedder=DeterministicEmbedder()),
        _db_operation_runner=unused_runner,
    )


def candidate(chunk_id: str, content: str, score: float) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        content=content,
        score=score,
        source_metadata={"source": "policy.pdf", "page": int(chunk_id[-1])},
        retrieval_legs=["hybrid"],
        component_scores={"hybrid": score},
    )


def test_maxsim_is_token_level_late_interaction() -> None:
    query_embeddings = [[1.0, 0.0], [0.0, 1.0]]

    score = maxsim_score(
        query_embeddings,
        [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]],
    )
    partial_score = maxsim_score(query_embeddings, [[1.0, 0.0]])

    assert score == pytest.approx(2.0)
    assert partial_score == pytest.approx(1.0)


def test_colbert_readiness_rejects_missing_local_checkpoint_without_network() -> None:
    with patch(
        "app.rag.readiness.snapshot_download",
        side_effect=FileNotFoundError,
    ) as lookup:
        fact = probe_colbert_readiness(
            "colbert-ir/colbertv2.0",
            library_fact=ReadinessFact(True, "ready"),
        )

    assert not fact.available
    assert fact.reason == "colbert_checkpoint_unavailable"
    lookup.assert_called_once_with(
        repo_id="colbert-ir/colbertv2.0",
        local_files_only=True,
    )


def test_colbert_readiness_accepts_configured_local_checkpoint(tmp_path: Any) -> None:
    checkpoint = tmp_path / "colbert"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text("{}")
    with (
        patch("app.rag.readiness.importlib.util.find_spec", return_value=object()),
        patch("app.rag.readiness.importlib.import_module", return_value=object()),
    ):
        fact = probe_colbert_readiness(str(checkpoint))

    assert fact.available
    assert fact.reason == "ready"


@pytest.mark.parametrize(
    "checkpoint",
    [
        "/srv/models/tenant-colbert",
        "acme/legal-colbert-v3",
    ],
)
async def test_gateway_factory_loads_exact_configured_colbert_checkpoint(
    checkpoint: str,
) -> None:
    pretrained_calls: list[str] = []

    class FakeRAGPretrainedModel:
        @classmethod
        def from_pretrained(cls, model_name: str) -> RecordingRAGatouilleModel:
            pretrained_calls.append(model_name)
            return RecordingRAGatouilleModel()

    capabilities = core_strategy_capabilities(
        RAGAdapterConfiguration(colbert_checkpoint=checkpoint)
    )
    adapter = capabilities[RAGStrategy.COLBERT].adapter
    assert isinstance(adapter, ColBERTRAGRuntimeAdapter)
    with patch.dict(
        sys.modules,
        {"ragatouille": SimpleNamespace(RAGPretrainedModel=FakeRAGPretrainedModel)},
    ):
        try:
            await adapter._reranker.score("policy", ["policy document"])
        finally:
            await adapter.aclose()

    assert pretrained_calls == [checkpoint]
    assert "colbert-ir/colbertv2.0" not in pretrained_calls


async def test_ragatouille_loader_and_rerank_run_in_worker_threads() -> None:
    event_loop_thread_id = threading.get_ident()
    loader_thread_ids: list[int] = []
    model: RecordingRAGatouilleModel | None = None
    pretrained_calls: list[str] = []

    class FakeRAGPretrainedModel:
        @classmethod
        def from_pretrained(cls, model_name: str) -> RecordingRAGatouilleModel:
            nonlocal model
            pretrained_calls.append(model_name)
            loader_thread_ids.append(threading.get_ident())
            model = RecordingRAGatouilleModel()
            return model

    with patch.dict(
        sys.modules,
        {"ragatouille": SimpleNamespace(RAGPretrainedModel=FakeRAGPretrainedModel)},
    ):
        reranker = ColBERTLateInteractionReranker(model_loader=_load_colbert_model)
        try:
            scores = await reranker.score(
                "python database",
                ["weather", "python database"],
            )
        finally:
            await reranker.aclose()

    assert scores == [0.0, 1.0]
    assert pretrained_calls == ["colbert-ir/colbertv2.0"]
    assert loader_thread_ids
    assert all(thread_id != event_loop_thread_id for thread_id in loader_thread_ids)
    assert model is not None
    assert model.calls == [
        ("python database", ["weather", "python database"], 2)
    ]
    assert model.inference_thread_ids
    assert all(
        thread_id != event_loop_thread_id for thread_id in model.inference_thread_ids
    )


async def test_ragatouille_public_results_map_by_unique_document_content() -> None:
    class PublicResultModel:
        def rerank(
            self,
            query: str,
            documents: list[str],
            *,
            k: int,
        ) -> list[dict[str, object]]:
            assert query == "policy"
            assert k == len(documents)
            return [
                {"content": documents[1], "score": 0.9, "rank": 1},
                {"content": documents[0], "score": 0.2, "rank": 2},
            ]

    reranker = ColBERTLateInteractionReranker(model_loader=PublicResultModel)
    try:
        scores = await reranker.score(
            "policy",
            ["first candidate", "second candidate"],
        )
    finally:
        await reranker.aclose()

    assert scores == [0.2, 0.9]


async def test_ragatouille_result_indices_disambiguate_duplicate_content() -> None:
    reranker = ColBERTLateInteractionReranker(
        model_loader=RecordingRAGatouilleModel
    )
    try:
        scores = await reranker.score("policy", ["duplicate", "duplicate"])
    finally:
        await reranker.aclose()

    assert scores == [0.0, 1.0]


async def test_ragatouille_rejects_ambiguous_duplicate_document_content() -> None:
    class AmbiguousResultModel:
        def rerank(
            self,
            query: str,
            documents: list[str],
            *,
            k: int,
        ) -> list[dict[str, object]]:
            del query, k
            return [
                {"content": documents[0], "score": 0.9, "rank": 1},
                {"content": documents[1], "score": 0.8, "rank": 2},
            ]

    reranker = ColBERTLateInteractionReranker(model_loader=AmbiguousResultModel)
    try:
        with pytest.raises(RerankerInferenceError, match="multiple candidates"):
            await reranker.score("policy", ["duplicate", "duplicate"])
    finally:
        await reranker.aclose()


async def test_ragatouille_rejects_duplicate_result_indices() -> None:
    class DuplicateIndexModel:
        def rerank(
            self,
            query: str,
            documents: list[str],
            *,
            k: int,
        ) -> list[dict[str, object]]:
            del query, k
            return [
                {
                    "content": documents[0],
                    "score": 0.9,
                    "rank": 1,
                    "result_index": 0,
                },
                {
                    "content": documents[0],
                    "score": 0.8,
                    "rank": 2,
                    "result_index": 0,
                },
            ]

    reranker = ColBERTLateInteractionReranker(model_loader=DuplicateIndexModel)
    try:
        with pytest.raises(RerankerInferenceError, match="duplicate candidate"):
            await reranker.score("policy", ["first", "second"])
    finally:
        await reranker.aclose()


async def test_ragatouille_rejects_result_content_missing_from_candidates() -> None:
    class MissingResultModel:
        def rerank(
            self,
            query: str,
            documents: list[str],
            *,
            k: int,
        ) -> list[dict[str, object]]:
            del query, k
            return [
                {"content": documents[0], "score": 0.9, "rank": 1},
                {"content": "not a candidate", "score": 0.8, "rank": 2},
            ]

    reranker = ColBERTLateInteractionReranker(model_loader=MissingResultModel)
    try:
        with pytest.raises(RerankerInferenceError, match="not a candidate"):
            await reranker.score("policy", ["first", "second"])
    finally:
        await reranker.aclose()


async def test_concurrent_first_use_loads_ragatouille_model_once() -> None:
    load_count = 0

    def load() -> RecordingRAGatouilleModel:
        nonlocal load_count
        load_count += 1
        time.sleep(0.05)
        return RecordingRAGatouilleModel()

    reranker = ColBERTLateInteractionReranker(model_loader=load)
    try:
        first, second = await asyncio.gather(
            reranker.score("policy", ["first", "second"]),
            reranker.score("policy", ["first", "second"]),
        )
    finally:
        await reranker.aclose()

    assert first == [0.0, 1.0]
    assert second == [0.0, 1.0]
    assert load_count == 1


async def test_colbert_bounds_concurrent_inference_before_worker_submission() -> None:
    started = threading.Event()
    release = threading.Event()
    call_count = 0

    class BlockingModel(RecordingRAGatouilleModel):
        def rerank(
            self,
            query: str,
            documents: list[str],
            *,
            k: int,
        ) -> list[dict[str, object]]:
            nonlocal call_count
            call_count += 1
            started.set()
            release.wait(timeout=1.0)
            return super().rerank(query, documents, k=k)

    reranker = ColBERTLateInteractionReranker(
        model_loader=BlockingModel,
        max_workers=1,
        max_queue_size=0,
    )
    first = asyncio.create_task(reranker.score("policy", ["first"]))
    await asyncio.to_thread(started.wait, 0.5)
    second = asyncio.create_task(reranker.score("policy", ["second"]))
    await asyncio.sleep(0.05)

    assert call_count == 1

    release.set()
    await asyncio.gather(first, second)
    await reranker.aclose()


async def test_colbert_does_not_starve_unrelated_default_executor_work() -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingModel(RecordingRAGatouilleModel):
        def rerank(
            self,
            query: str,
            documents: list[str],
            *,
            k: int,
        ) -> list[dict[str, object]]:
            started.set()
            release.wait(timeout=1.0)
            return super().rerank(query, documents, k=k)

    loop = asyncio.get_running_loop()
    loop.set_default_executor(
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-default")
    )
    reranker = ColBERTLateInteractionReranker(model_loader=BlockingModel)
    scoring = asyncio.create_task(reranker.score("policy", ["document"]))
    for _ in range(50):
        if started.is_set():
            break
        await asyncio.sleep(0.01)
    assert started.is_set()

    heartbeat = await asyncio.wait_for(asyncio.to_thread(lambda: "alive"), timeout=0.2)

    assert heartbeat == "alive"
    release.set()
    await scoring
    await reranker.aclose()


async def test_colbert_rejects_work_after_shutdown() -> None:
    reranker = ColBERTLateInteractionReranker(
        model_loader=RecordingRAGatouilleModel
    )
    await reranker.aclose()

    with pytest.raises(RuntimeError, match="closed"):
        await reranker.score("policy", ["document"])


async def test_colbert_adapter_does_not_close_injected_shared_reranker() -> None:
    class SharedReranker:
        def __init__(self) -> None:
            self.close_count = 0

        async def score(self, query: str, documents: list[str]) -> list[float]:
            del query
            return [0.0] * len(documents)

        async def aclose(self) -> None:
            self.close_count += 1

    reranker = SharedReranker()
    adapter = ColBERTRAGRuntimeAdapter(reranker=reranker)

    await adapter.aclose()

    assert reranker.close_count == 0


async def test_colbert_adapter_waits_for_active_owned_work_during_shutdown() -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingModel(RecordingRAGatouilleModel):
        def rerank(
            self,
            query: str,
            documents: list[str],
            *,
            k: int,
        ) -> list[dict[str, object]]:
            started.set()
            release.wait(timeout=1.0)
            return super().rerank(query, documents, k=k)

    reranker = ColBERTLateInteractionReranker(model_loader=BlockingModel)
    adapter = ColBERTRAGRuntimeAdapter(reranker=reranker, owns_reranker=True)
    scoring = asyncio.create_task(reranker.score("policy", ["document"]))
    await asyncio.to_thread(started.wait, 0.5)

    closing = asyncio.create_task(adapter.aclose())
    await asyncio.sleep(0)

    assert not closing.done()
    release.set()
    await asyncio.gather(scoring, closing)


@pytest.mark.parametrize("mode", ["cancel", "timeout"])
async def test_colbert_abandoned_waiters_do_not_create_queued_work(mode: str) -> None:
    started = threading.Event()
    release = threading.Event()
    call_count = 0

    class BlockingModel(RecordingRAGatouilleModel):
        def rerank(
            self,
            query: str,
            documents: list[str],
            *,
            k: int,
        ) -> list[dict[str, object]]:
            nonlocal call_count
            call_count += 1
            started.set()
            release.wait(timeout=1.0)
            return super().rerank(query, documents, k=k)

    reranker = ColBERTLateInteractionReranker(
        model_loader=BlockingModel,
        max_workers=1,
        max_queue_size=0,
    )
    running = asyncio.create_task(reranker.score("policy", ["running"]))
    await asyncio.to_thread(started.wait, 0.5)

    if mode == "cancel":
        abandoned = asyncio.create_task(reranker.score("policy", ["abandoned"]))
        await asyncio.sleep(0)
        abandoned.cancel()
        with pytest.raises(asyncio.CancelledError):
            await abandoned
    else:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                reranker.score("policy", ["abandoned"]),
                timeout=0.02,
            )

    release.set()
    await running
    await asyncio.sleep(0.05)
    assert call_count == 1
    await reranker.aclose()


async def test_colbert_uses_wider_candidates_and_preserves_citations_and_scores() -> None:
    class DeterministicReranker:
        async def score(self, query: str, documents: list[str]) -> list[float]:
            assert query == "python database"
            assert len(documents) == 8
            return [float(index) for index in range(len(documents))]

    candidates = [
        candidate(f"c{index}", f"document {index}", score=1.0 - index / 10)
        for index in range(8)
    ]
    observed_candidate_limits: list[int] = []

    async def search(*args: object, **kwargs: Any) -> list[RetrievalResult]:
        del args
        observed_candidate_limits.append(kwargs["top_k"])
        kwargs["evidence"].append(
            {
                "component": "hybrid",
                "result_count": len(candidates),
                "component_scores": {item.chunk_id: item.score for item in candidates},
            }
        )
        return candidates

    with patch("app.rag.gateway._search_persisted", side_effect=search):
        result = await ColBERTRAGRuntimeAdapter(
            reranker=DeterministicReranker(),
            candidate_multiplier=4,
        ).execute(request(top_k=2), context())

    assert observed_candidate_limits == [8]
    assert [citation.chunk_id for citation in result.citations] == ["c7", "c6"]
    assert [citation.content for citation in result.citations] == [
        "document 7",
        "document 6",
    ]
    assert result.citations[0].source == "policy.pdf"
    assert result.citations[0].metadata["page"] == 7
    assert result.citations[0].metadata["original_score"] == pytest.approx(0.3)
    assert result.citations[0].metadata["colbert_score"] == pytest.approx(7.0)
    assert result.citations[0].metadata["rerank_score"] == pytest.approx(7.0)
    assert result.resolved_strategy_id is RAGStrategy.COLBERT


async def test_configured_colbert_loader_failure_is_explicit() -> None:
    def fail_load() -> RecordingRAGatouilleModel:
        raise OSError("model files unavailable")

    reranker = ColBERTLateInteractionReranker(model_loader=fail_load)
    try:
        with pytest.raises(RerankerLoadError, match="ColBERT"):
            await reranker.score("query", ["document"])
    finally:
        await reranker.aclose()


async def test_colbert_and_cross_encoder_are_registered_separately() -> None:
    colbert = build_reranker("colbert")
    cross_encoder = build_reranker("cross_encoder")
    capabilities = core_strategy_capabilities()
    colbert_adapter = capabilities[RAGStrategy.COLBERT].adapter
    try:
        assert isinstance(colbert, ColBERTLateInteractionReranker)
        assert isinstance(cross_encoder, CrossEncoderDocumentReranker)
        assert type(colbert) is not type(cross_encoder)
        assert isinstance(colbert_adapter, ColBERTRAGRuntimeAdapter)

        with pytest.raises(ValueError, match="Unknown reranker"):
            build_reranker("late_interaction_fallback")
    finally:
        await colbert.aclose()
        await cross_encoder.aclose()
        await colbert_adapter.aclose()
