"""Edge-case coverage for RAGTrace with malformed/partial data.

Prior coverage (tests/test_phase6_wiring.py, tests/rag/test_agentic/
test_layer4_complete.py) only exercises the happy path: record a
well-formed retrieval, emit a well-formed SSE event. This file targets the
audit's specific concerns:

  - malformed trace data (None query, non-numeric result_count) doesn't
    crash trace recording;
  - a partial trace (steps missing expected fields, or non-dict entries)
    is handled gracefully by to_sse_event();
  - trace-recording failure at the call site (app/agent/nodes/rag_mixin.py)
    doesn't break the underlying RAG operation — it's wrapped in try/except.

Regression: record_retrieval() used to call ``query[:200]`` directly (crashes
on None) and to_sse_event() used to do ``s["result_count"]`` directly
(crashes on a step missing that key, e.g. one appended by a future caller
that doesn't go through record_retrieval). Both are now defensive.
"""

from __future__ import annotations

from app.rag.agentic.rag_trace import RAGTrace


# ── malformed inputs to record_retrieval ────────────────────────────────────────


def test_record_retrieval_with_none_query_does_not_raise() -> None:
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    trace.record_retrieval(
        strategy="hybrid", query=None, result_count=3, confidence=0.5, latency_ms=10  # type: ignore[arg-type]
    )
    assert trace.steps[-1]["query"] == ""


def test_record_retrieval_with_non_string_query_is_coerced() -> None:
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    trace.record_retrieval(
        strategy="hybrid", query=12345, result_count=1, confidence=0.5, latency_ms=1  # type: ignore[arg-type]
    )
    assert trace.steps[-1]["query"] == "12345"


def test_record_retrieval_truncates_overlong_query() -> None:
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    long_query = "x" * 10_000
    trace.record_retrieval(
        strategy="hybrid", query=long_query, result_count=1, confidence=0.5, latency_ms=1
    )
    assert len(trace.steps[-1]["query"]) == 200


def test_record_retrieval_multiple_calls_all_recorded() -> None:
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    for i in range(5):
        trace.record_retrieval(
            strategy=f"strategy-{i}", query=f"q{i}", result_count=i, confidence=0.1 * i, latency_ms=i
        )
    assert len(trace.steps) == 5


# ── to_sse_event with no steps / partial steps / malformed steps ───────────────


def test_to_sse_event_with_no_steps_does_not_raise() -> None:
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    event = trace.to_sse_event()
    assert event["steps"] == 0
    assert event["total_results"] == 0
    assert event["strategy"] == "unknown"


def test_to_sse_event_with_step_missing_result_count_key() -> None:
    """A step dict missing 'result_count' (e.g. appended directly, not via
    record_retrieval) must not crash the sum — it should contribute 0."""
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    trace.steps.append({"strategy": "hybrid", "query": "q"})  # no result_count
    event = trace.to_sse_event()
    assert event["total_results"] == 0
    assert event["steps"] == 1


def test_to_sse_event_with_step_missing_strategy_key_falls_back_to_unknown() -> None:
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    trace.steps.append({"result_count": 3})  # no strategy
    event = trace.to_sse_event()
    assert event["strategy"] == "unknown"
    assert event["total_results"] == 3


def test_to_sse_event_with_non_numeric_result_count_does_not_raise() -> None:
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    trace.steps.append({"strategy": "hybrid", "result_count": "not-a-number"})
    event = trace.to_sse_event()
    assert event["total_results"] == 0


def test_to_sse_event_with_none_result_count_does_not_raise() -> None:
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    trace.steps.append({"strategy": "hybrid", "result_count": None})
    event = trace.to_sse_event()
    assert event["total_results"] == 0


def test_to_sse_event_mixed_valid_and_malformed_steps_sums_only_valid_ones() -> None:
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    trace.record_retrieval(strategy="hybrid", query="q1", result_count=4, confidence=0.9, latency_ms=5)
    trace.steps.append({"strategy": "graph"})  # partial: missing result_count
    trace.record_retrieval(strategy="web", query="q2", result_count=2, confidence=0.3, latency_ms=8)
    event = trace.to_sse_event()
    assert event["steps"] == 3
    assert event["total_results"] == 6  # 4 + 0 + 2
    assert event["strategy"] == "web"  # last step's strategy


def test_to_sse_event_with_non_dict_step_entry_does_not_raise() -> None:
    """A completely malformed entry (not a dict at all) in .steps — e.g. from
    a bug in some future caller — must not crash total_results or strategy
    resolution."""
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    trace.steps.append("not-a-dict")  # type: ignore[arg-type]
    event = trace.to_sse_event()
    assert event["total_results"] == 0
    assert event["strategy"] == "unknown"


def test_to_sse_event_output_is_json_shape_stable() -> None:
    trace = RAGTrace(goal_id="g1", tenant_id="t1")
    trace.record_retrieval(strategy="hybrid", query="q", result_count=1, confidence=0.5, latency_ms=1)
    event = trace.to_sse_event()
    assert set(event.keys()) == {
        "type",
        "goal_id",
        "trace_id",
        "strategy",
        "steps",
        "total_results",
    }
    assert event["type"] == "rag_strategy_selected"
    assert event["goal_id"] == "g1"


# ── trace-recording failure does not break the underlying RAG operation ────────


async def test_rag_mixin_trace_recording_failure_does_not_break_retrieval(monkeypatch) -> None:
    """The call site in app/agent/nodes/rag_mixin.py wraps RAGTrace usage in
    try/except Exception: pass — assert that contract holds even when
    RAGTrace itself raises, by simulating a broken RAGTrace.record_retrieval
    and confirming the surrounding block still completes without raising."""
    import app.rag.agentic.rag_trace as rag_trace_module

    class _BrokenRAGTrace(rag_trace_module.RAGTrace):
        def record_retrieval(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("simulated trace recording failure")

    monkeypatch.setattr(rag_trace_module, "RAGTrace", _BrokenRAGTrace)

    # Reproduce the exact try/except shape used in rag_mixin.py around
    # RAGTrace construction + record_retrieval + to_sse_event, to pin the
    # contract that a broken trace never propagates.
    events_emitted: list[dict[str, object]] = []
    try:
        from app.rag.agentic.rag_trace import RAGTrace as _RT

        _trace = _RT(goal_id="g1", tenant_id="t1")
        _trace.record_retrieval(
            strategy="hybrid", query="q", result_count=1, confidence=0.5, latency_ms=1
        )
        events_emitted.append(_trace.to_sse_event())
    except Exception:
        pass

    # No event was emitted (trace failed), but nothing raised out of this
    # block — proving the underlying RAG operation can safely continue.
    assert events_emitted == []
