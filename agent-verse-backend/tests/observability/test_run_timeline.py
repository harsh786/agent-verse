"""Per-goal step timeline captured from spans (queryable without Jaeger/Langfuse).

A span processor turns each goal-scoped span (gen_ai generations, tool calls,
LangGraph nodes) into a compact timeline entry keyed by goal, so a UI can render
"how this goal ran" (steps, model calls, tokens, cost, latency, deep-link ids)
straight from the platform.
"""

from __future__ import annotations

from app.observability.run_timeline import (
    InMemoryRunTimelineStore,
    RunTimelineSpanProcessor,
)


class _FakeSpanContext:
    def __init__(self) -> None:
        self.trace_id = 0x0123456789ABCDEF0123456789ABCDEF
        self.span_id = 0x0123456789ABCDEF


class _FakeStatus:
    class _Code:
        name = "OK"

    status_code = _Code()


class _FakeSpan:
    def __init__(self, name: str, attributes: dict, start: int, end: int) -> None:
        self.name = name
        self.attributes = attributes
        self.start_time = start
        self.end_time = end
        self.status = _FakeStatus()

    def get_span_context(self) -> _FakeSpanContext:
        return _FakeSpanContext()


def test_processor_captures_goal_scoped_gen_ai_span() -> None:
    store = InMemoryRunTimelineStore()
    proc = RunTimelineSpanProcessor(store)
    span = _FakeSpan(
        "gen_ai.planner",
        {
            "agentverse.goal_id": "g1",
            "agentverse.tenant_id": "acme",
            "agentverse.role": "planner",
            "gen_ai.request.model": "qwen2.5-72b",
            "gen_ai.usage.input_tokens": 12,
            "gen_ai.usage.output_tokens": 5,
            "gen_ai.usage.cost_usd": 0.001,
        },
        start=1_000_000_000,
        end=1_500_000_000,  # 500ms later
    )
    proc.on_end(span)

    entries = store.get("acme", "g1")
    assert len(entries) == 1
    e = entries[0]
    assert e["name"] == "gen_ai.planner"
    assert e["role"] == "planner"
    assert e["model"] == "qwen2.5-72b"
    assert e["output_tokens"] == 5
    assert e["cost_usd"] == 0.001
    assert e["duration_ms"] == 500.0
    assert len(e["trace_id"]) == 32 and len(e["span_id"]) == 16


def test_on_start_stamps_goal_scope_from_baggage() -> None:
    # A node/tool span that doesn't self-carry goal_id gets stamped from the run
    # baggage at start, so it is captured on end (nodes + tools, not just gen_ai).
    from app.observability.run_timeline import RunTimelineSpanProcessor as _P
    from app.observability.trace_propagation import run_context

    class _Writable:
        def __init__(self) -> None:
            self.attributes: dict = {}

        def set_attribute(self, k: str, v: object) -> None:
            self.attributes[k] = v

    store = InMemoryRunTimelineStore()
    proc = _P(store)
    span = _Writable()
    with run_context(goal_id="g9", tenant_id="acme"):
        proc.on_start(span)  # reads current baggage
    assert span.attributes["agentverse.goal_id"] == "g9"
    assert span.attributes["agentverse.tenant_id"] == "acme"


def test_processor_ignores_spans_without_goal_id() -> None:
    store = InMemoryRunTimelineStore()
    proc = RunTimelineSpanProcessor(store)
    proc.on_end(_FakeSpan("http.server", {"http.route": "/x"}, 0, 1))
    assert store.get("acme", "g1") == []


def test_store_is_tenant_scoped_and_capped() -> None:
    store = InMemoryRunTimelineStore(max_entries=3)
    for i in range(5):
        store.append("t", "g", {"name": f"e{i}"})
    kept = [e["name"] for e in store.get("t", "g")]
    assert kept == ["e2", "e3", "e4"]  # oldest dropped, cap honored
    assert store.get("other", "g") == []  # isolated by tenant
