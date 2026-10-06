"""a01-F024-01: the deployment embedder emits ``gen_ai.embeddings`` spans.

TracedProvider could trace ``embed`` but nothing wrapped the embedder: only the
graph role providers and decision calls were traced, so ingestion, retrieval and
re-embedding vectors were invisible in traces. Every process now wraps the ONE
embedder it resolves (API ``create_app``, the worker's ingestion services, the
re-embed task) with :func:`traced_embedder`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app.observability.genai as genai_mod
from app.observability.traced_provider import TracedProvider, traced_embedder, unwrap_provider
from app.providers.base import EmbedRequest, EmbedResponse
from app.providers.embedder_factory import EmbedderResolution, embedder_model_name


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    original = genai_mod._tracer
    genai_mod._tracer = provider.get_tracer("agentverse.genai")
    try:
        yield exporter
    finally:
        genai_mod._tracer = original


class _Embedder:
    _embed_model = "qwen3-embedding-8b"
    embedding_dim = 4

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        return EmbedResponse(embeddings=[[0.1] * 4 for _ in request.texts], model="")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [[0.2] * 4 for _ in texts]


class _EmbedOnly:
    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        return EmbedResponse(embeddings=[[0.0] for _ in request.texts], model="m")


class FakeEmbedProvider(_EmbedOnly):
    pass


async def test_embed_span_names_the_embedders_model(spans: InMemorySpanExporter) -> None:
    traced = traced_embedder(_Embedder(), provider_system="vllm")
    out = await traced.embed(EmbedRequest(texts=["a", "b", "c"]))

    assert len(out.embeddings) == 3
    (span,) = spans.get_finished_spans()
    attrs = dict(span.attributes or {})
    assert span.name == "gen_ai.embeddings"
    assert attrs["gen_ai.system"] == "vllm"
    # The request carried no model: the span names the model that produced the vectors.
    assert attrs["gen_ai.request.model"] == "qwen3-embedding-8b"
    assert attrs["gen_ai.request.input_count"] == 3


async def test_embed_batch_is_traced_too(spans: InMemorySpanExporter) -> None:
    inner = _Embedder()
    traced = traced_embedder(inner, provider_system="vllm")
    vectors = await traced.embed_batch(["x", "y"])

    assert len(vectors) == 2 and inner.batches == [["x", "y"]]
    (span,) = spans.get_finished_spans()
    assert span.name == "gen_ai.embeddings"
    assert dict(span.attributes or {})["gen_ai.request.input_count"] == 2


def test_wrapping_keeps_feature_detection_and_identity_helpers() -> None:
    traced = traced_embedder(_EmbedOnly(), provider_system="x")
    assert not hasattr(traced, "embed_batch")  # the inner has none
    assert traced_embedder(traced, provider_system="x") is traced  # idempotent
    assert traced_embedder(None) is None
    inner = _Embedder()
    wrapped = traced_embedder(inner, provider_system="x")
    assert unwrap_provider(wrapped) is inner
    assert embedder_model_name(wrapped) == "qwen3-embedding-8b"
    # Fallback name is the real class, not "TracedProvider".
    assert embedder_model_name(traced_embedder(_EmbedOnly(), provider_system="x")) == "_EmbedOnly"


def test_simulated_embedder_is_still_reported_degraded_through_the_wrapper() -> None:
    from types import SimpleNamespace

    from app.orchestration.strategy_probes import _embedder_probe

    state = SimpleNamespace(embedder=traced_embedder(FakeEmbedProvider(), provider_system="fake"))
    result = _embedder_probe(state)()
    assert "simulated_embedder" in str(result)


def test_worker_ingestion_services_embed_through_a_traced_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.providers.embedder_factory as factory
    from app.ingestion import worker_services

    inner = _Embedder()
    monkeypatch.setattr(
        factory,
        "resolve_embedder",
        lambda **_k: EmbedderResolution(embedder=inner, provider="dedicated", dimension=4),
    )
    monkeypatch.setattr(worker_services, "bind_worker_guardrail_rules", lambda _f: None)
    _store, embedder = worker_services.build_worker_knowledge_services(lambda: None)
    assert isinstance(embedder, TracedProvider)
    assert unwrap_provider(embedder) is inner


def test_api_query_embedder_is_traced(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.embedder_factory as factory

    inner = _Embedder()
    resolution = EmbedderResolution(embedder=inner, provider="dedicated", dimension=4)
    monkeypatch.setattr(factory, "resolve_embedder", lambda *_a, **_k: resolution)
    from app.main import create_app

    app: Any = create_app()
    assert isinstance(app.state.embedder, TracedProvider)
    assert unwrap_provider(app.state.embedder) is inner
    # The resolution (health / readiness reporting) keeps the real embedder.
    assert app.state.embedder_resolution.embedder is inner
