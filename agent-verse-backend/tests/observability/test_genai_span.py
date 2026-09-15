"""Per-LLM-call GenAI span — the Langfuse/LangSmith 'generation' primitive.

Verifies app/observability/genai.py emits one OTel span per LLM call with GenAI
semantic-convention attributes (model, tokens, cost, latency, finish reason,
role), parented under the active span, with prompt/completion capture gated by a
privacy flag and redacted when captured.
"""

from __future__ import annotations

import pytest
from collections.abc import Iterator

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app.observability.genai as genai_mod
from app.observability.genai import record_generation
from app.providers.base import CompletionRequest, CompletionResponse, Message


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    # Inject a dedicated tracer/exporter into the module (the OTel global provider
    # can only be set once per process, so we swap the module tracer directly).
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    original = genai_mod._tracer
    genai_mod._tracer = provider.get_tracer("agentverse.genai")
    try:
        yield exporter
    finally:
        genai_mod._tracer = original


def _attrs(exporter: InMemorySpanExporter) -> dict:
    finished = exporter.get_finished_spans()
    assert len(finished) == 1, f"expected exactly one generation span, got {len(finished)}"
    return dict(finished[0].attributes or {})


async def test_generation_span_records_genai_semconv(spans: InMemorySpanExporter) -> None:
    req = CompletionRequest(
        messages=[Message(role="user", content="hi")],
        model="qwen2.5-72b",
        temperature=0.2,
        max_tokens=512,
    )
    async with record_generation(req, provider_system="nvidia", role="planner") as rec:
        resp = CompletionResponse(
            content="hello there", model="qwen2.5-72b", input_tokens=10, output_tokens=3,
            stop_reason="end_turn",
        )
        rec.set_response(resp, cost_usd=0.00042, cache_hit=False)

    a = _attrs(spans)
    assert a["gen_ai.system"] == "nvidia"
    assert a["gen_ai.operation.name"] == "chat"
    assert a["gen_ai.request.model"] == "qwen2.5-72b"
    assert a["gen_ai.request.temperature"] == 0.2
    assert a["gen_ai.request.max_tokens"] == 512
    assert a["gen_ai.usage.input_tokens"] == 10
    assert a["gen_ai.usage.output_tokens"] == 3
    assert a["gen_ai.response.finish_reason"] == "end_turn"
    assert a["gen_ai.usage.cost_usd"] == pytest.approx(0.00042)
    assert a["agentverse.role"] == "planner"
    assert a["gen_ai.cache_hit"] is False
    assert a.get("agentverse.llm.duration_ms", 0) >= 0


async def test_content_capture_is_off_by_default(spans: InMemorySpanExporter) -> None:
    req = CompletionRequest(messages=[Message(role="user", content="secret prompt")],
                            model="m")
    async with record_generation(req, provider_system="openai") as rec:
        rec.set_response(CompletionResponse(content="secret answer", model="m"))
    span = spans.get_finished_spans()[0]
    dumped = str([(e.name, dict(e.attributes or {})) for e in span.events])
    assert "secret prompt" not in dumped and "secret answer" not in dumped


async def test_content_capture_redacts_when_enabled(spans: InMemorySpanExporter) -> None:
    req = CompletionRequest(
        messages=[Message(role="user", content="my api_key=sk-ABC123SECRET please use it")],
        model="m",
    )
    async with record_generation(req, provider_system="openai", capture_content=True) as rec:
        rec.set_response(CompletionResponse(content="ok", model="m"))
    span = spans.get_finished_spans()[0]
    events = {e.name: dict(e.attributes or {}) for e in span.events}
    prompt_event = events.get("gen_ai.content.prompt", {})
    body = str(prompt_event)
    assert "gen_ai.content.prompt" in events  # captured
    assert "sk-ABC123SECRET" not in body  # but redacted


async def test_generation_span_records_exception(spans: InMemorySpanExporter) -> None:
    req = CompletionRequest(messages=[Message(role="user", content="x")], model="m")
    with pytest.raises(RuntimeError):
        async with record_generation(req, provider_system="openai"):
            raise RuntimeError("provider exploded")
    span = spans.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR"
