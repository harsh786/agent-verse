"""Logs carry the active trace id (log↔trace correlation)."""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider

from app.observability.logging import add_trace_correlation


def test_stamps_trace_and_span_id_when_span_active() -> None:
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("op") as span:
        ctx = span.get_span_context()
        out = add_trace_correlation(None, "info", {"event": "hello"})
    assert out["trace_id"] == format(ctx.trace_id, "032x")
    assert out["span_id"] == format(ctx.span_id, "016x")
    assert len(out["trace_id"]) == 32 and len(out["span_id"]) == 16


def test_no_trace_fields_without_active_span() -> None:
    out = add_trace_correlation(None, "info", {"event": "hello"})
    assert "trace_id" not in out and "span_id" not in out
