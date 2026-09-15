"""Run correlation baggage → per-goal traces + Langfuse session/user grouping."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app.observability.genai as genai_mod
from app.observability.genai import record_generation
from app.observability.trace_propagation import current_run_baggage, run_context
from app.providers.base import CompletionRequest, CompletionResponse, Message


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


def test_run_context_sets_and_clears_baggage() -> None:
    assert current_run_baggage() == {}
    with run_context(goal_id="g1", conversation_id="c1", tenant_id="t1"):
        assert current_run_baggage() == {
            "goal_id": "g1",
            "conversation_id": "c1",
            "tenant_id": "t1",
        }
    assert current_run_baggage() == {}  # detached after the block


async def test_generation_span_inherits_run_context(spans: InMemorySpanExporter) -> None:
    req = CompletionRequest(messages=[Message(role="user", content="q")], model="m")
    with run_context(goal_id="goal-42", conversation_id="conv-7", tenant_id="acme"):
        async with record_generation(req, provider_system="nvidia", role="planner") as rec:
            rec.set_response(CompletionResponse(content="a", model="m"))
    a = dict(spans.get_finished_spans()[0].attributes or {})
    assert a["agentverse.goal_id"] == "goal-42"
    assert a["agentverse.tenant_id"] == "acme"
    assert a["langfuse.session.id"] == "conv-7"  # conversation groups the session
    assert a["langfuse.user.id"] == "acme"


async def test_session_falls_back_to_goal_when_no_conversation(
    spans: InMemorySpanExporter,
) -> None:
    req = CompletionRequest(messages=[Message(role="user", content="q")], model="m")
    with run_context(goal_id="goal-99", tenant_id="t"):
        async with record_generation(req, provider_system="nvidia") as rec:
            rec.set_response(CompletionResponse(content="a", model="m"))
    a = dict(spans.get_finished_spans()[0].attributes or {})
    assert a["langfuse.session.id"] == "goal-99"
