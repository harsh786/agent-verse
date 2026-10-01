"""PROV-23: queued goals, decision calls and embeddings emit GenAI spans with cost.

TracedProvider wrapped providers only in GoalService._make_agent_loop, so
Celery-run goals (production), workflows, complete_decision calls and
embeddings were untraced; spans lacked cost_usd / cache_hit; and a
MultiEndpointLLMProvider reported its class name as gen_ai.system.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app.observability.genai as genai_mod
from app.observability.traced_provider import TracedProvider, provider_system_of
from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
    Message,
)


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


class _Provider:
    _default_model = "gpt-4o-mini"
    _agentverse_provider_type = "nvidia"

    async def complete(self, request: Any) -> CompletionResponse:
        return CompletionResponse(
            content="ok", model="gpt-4o-mini", input_tokens=1000, output_tokens=100
        )

    async def embed(self, request: Any) -> EmbedResponse:
        return EmbedResponse(embeddings=[[0.1, 0.2] for _ in request.texts], model="emb-1")


def _req() -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="q")], model="gpt-4o-mini")


def test_gen_ai_system_comes_from_the_configured_provider_type() -> None:
    class MultiEndpointLLMProvider:
        _agentverse_provider_type = "onprem"

    assert provider_system_of(MultiEndpointLLMProvider()) == "onprem"


async def test_spans_carry_cost_and_cache_hit(spans: InMemorySpanExporter) -> None:
    from app.intelligence.cost_tracker import calculate_cost

    await TracedProvider(_Provider(), provider_system="nvidia").complete(_req())
    [span] = spans.get_finished_spans()
    attrs = dict(span.attributes or {})
    assert attrs["gen_ai.usage.cost_usd"] == pytest.approx(
        calculate_cost("gpt-4o-mini", 1000, 100)
    )
    assert attrs["gen_ai.cache_hit"] is False


async def test_decision_calls_emit_a_span(spans: InMemorySpanExporter) -> None:
    from app.providers.guarded_completion import complete_decision

    await complete_decision(_Provider(), _req(), role="eval_judge", charge=False)
    names = [s.name for s in spans.get_finished_spans()]
    assert names == ["gen_ai.eval_judge"]
    attrs = dict(spans.get_finished_spans()[0].attributes or {})
    assert attrs["gen_ai.system"] == "nvidia"


async def test_decision_call_on_a_traced_provider_is_not_double_traced(
    spans: InMemorySpanExporter,
) -> None:
    from app.providers.guarded_completion import complete_decision

    traced = TracedProvider(_Provider(), provider_system="nvidia", default_role="planner")
    await complete_decision(traced, _req(), role="planner", charge=False)
    assert len(spans.get_finished_spans()) == 1


async def test_embeddings_emit_a_span(spans: InMemorySpanExporter) -> None:
    traced = TracedProvider(_Provider(), provider_system="nvidia")
    await traced.embed(EmbedRequest(texts=["a", "b"], model="emb-1"))
    [span] = spans.get_finished_spans()
    assert span.name == "gen_ai.embeddings"
    assert dict(span.attributes or {})["gen_ai.operation.name"] == "embeddings"


def test_profiled_graph_wraps_role_providers_for_worker_goals() -> None:
    from app.orchestration.profiled_graph import _traced_graph_services

    services = {"planner": _Provider(), "executor": _Provider(), "verifier": _Provider()}
    wrapped = _traced_graph_services(services)
    assert all(isinstance(wrapped[r], TracedProvider) for r in ("planner", "executor", "verifier"))
    again = _traced_graph_services(wrapped)
    assert again["planner"] is wrapped["planner"]  # never double-wrapped
