"""TracedProvider — wraps any LLMProvider so every call emits a GenAI span.

Applied once where providers are constructed, it gives per-call observability for
planner/executor/verifier/embeddings without touching each provider or call site.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app.observability.genai as genai_mod
from app.observability.traced_provider import TracedProvider
from app.providers.base import CompletionRequest, Message
from app.providers.fake import FakeProvider


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


async def test_complete_is_traced_and_returns_real_response(spans: InMemorySpanExporter) -> None:
    inner = FakeProvider(responses=["real answer from the model"])
    traced = TracedProvider(inner, provider_system="nvidia")
    req = CompletionRequest(
        messages=[Message(role="user", content="q")],
        model="qwen2.5-72b",
        metadata={"agentverse.role": "planner"},
    )
    resp = await traced.complete(req)

    # Real response passes through untouched.
    assert resp.content == "real answer from the model"
    # Exactly one generation span, with the model + role captured.
    finished = spans.get_finished_spans()
    assert len(finished) == 1
    a = dict(finished[0].attributes or {})
    assert a["gen_ai.system"] == "nvidia"
    assert a["gen_ai.request.model"] == "qwen2.5-72b"
    assert a["agentverse.role"] == "planner"
    assert a["gen_ai.usage.output_tokens"] >= 1
    assert finished[0].name == "gen_ai.planner"


async def test_delegates_capability_methods(spans: InMemorySpanExporter) -> None:
    inner = FakeProvider(responses=["x"], vision=True)
    traced = TracedProvider(inner, provider_system="openai")
    # Non-wrapped methods delegate straight through to the inner provider.
    assert traced.supports_vision() is True
    assert traced.supports_tool_use() == inner.supports_tool_use()


async def test_provider_error_is_recorded_and_reraised(spans: InMemorySpanExporter) -> None:
    class Boom:
        async def complete(self, request: CompletionRequest) -> object:
            raise RuntimeError("upstream 500")

    traced = TracedProvider(Boom(), provider_system="openai")
    with pytest.raises(RuntimeError, match="upstream 500"):
        await traced.complete(
            CompletionRequest(messages=[Message(role="user", content="q")], model="m")
        )
    span = spans.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR"
