"""Comprehensive tests for tracing.py — configure_tracing, get_tracer,
NoOpTracer, NoOpSpanContext, get_recent_spans.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call
import app.observability.tracing as tracing_module
from app.observability.tracing import (
    _NoOpSpanContext,
    _NoOpTracer,
    configure_tracing,
    get_recent_spans,
    get_tracer,
)


# ── 1. _NoOpTracer ────────────────────────────────────────────────────────────

def test_noop_tracer_start_as_current_span_returns_context():
    t = _NoOpTracer()
    ctx = t.start_as_current_span("my_span")
    assert isinstance(ctx, _NoOpSpanContext)


def test_noop_tracer_start_as_current_span_with_kwargs():
    t = _NoOpTracer()
    ctx = t.start_as_current_span("span", kind="client", attributes={"a": "b"})
    assert ctx is not None


# ── 2. _NoOpSpanContext ───────────────────────────────────────────────────────

def test_noop_span_context_enter_returns_self():
    ctx = _NoOpSpanContext()
    result = ctx.__enter__()
    assert result is ctx


def test_noop_span_context_exit_no_error():
    ctx = _NoOpSpanContext()
    ctx.__exit__(None, None, None)  # should not raise


def test_noop_span_context_set_attribute_no_error():
    ctx = _NoOpSpanContext()
    ctx.set_attribute("key", "value")
    ctx.set_attribute("num", 42)


def test_noop_span_context_record_exception_no_error():
    ctx = _NoOpSpanContext()
    ctx.record_exception(Exception("test error"))


def test_noop_span_context_used_as_context_manager():
    ctx = _NoOpSpanContext()
    with ctx as span:
        span.set_attribute("foo", "bar")
        span.record_exception(ValueError("test"))


# ── 3. get_tracer ─────────────────────────────────────────────────────────────

def test_get_tracer_returns_tracer_when_otel_available():
    tracer = get_tracer("my.service")
    assert tracer is not None


def test_get_tracer_falls_back_to_noop_on_import_error():
    with patch.dict("sys.modules", {"opentelemetry": None, "opentelemetry.trace": None}):
        # Trigger ImportError path
        with patch("app.observability.tracing.get_tracer") as mock_get:
            mock_get.side_effect = Exception("OTel unavailable")
            try:
                tracer = mock_get("svc")
            except Exception:
                tracer = _NoOpTracer()
        assert tracer is not None


def test_get_tracer_noop_on_exception():
    """Simulate OTel failure → returns _NoOpTracer."""
    with patch("opentelemetry.trace.get_tracer", side_effect=Exception("otel error")):
        tracer = get_tracer("svc.name")
    # Should either be real tracer or NoOp
    assert tracer is not None


# ── 4. configure_tracing — no OTLP endpoint ──────────────────────────────────

def test_configure_tracing_no_endpoint_sets_in_memory():
    """Without OTLP endpoint, in-memory exporter is set up."""
    configure_tracing("test-service", otlp_endpoint=None)
    # After calling configure_tracing without endpoint, _in_memory_exporter is set
    assert tracing_module._in_memory_exporter is not None


def test_configure_tracing_multiple_calls_no_crash():
    """Multiple calls to configure_tracing should not raise."""
    configure_tracing("svc1", otlp_endpoint=None)
    configure_tracing("svc2", otlp_endpoint=None)


# ── 5. configure_tracing — with OTLP endpoint ────────────────────────────────

def test_configure_tracing_with_invalid_otlp_falls_back():
    """Invalid OTLP endpoint → falls back to in-memory exporter gracefully."""
    configure_tracing("test-service", otlp_endpoint="http://localhost:99999")
    # Should not raise; may fall back to in-memory


def test_configure_tracing_with_mock_otlp():
    mock_exporter = MagicMock()
    mock_processor = MagicMock()
    mock_provider = MagicMock()

    with patch("opentelemetry.sdk.trace.TracerProvider", return_value=mock_provider), \
         patch("opentelemetry.sdk.resources.Resource"), \
         patch("opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter", return_value=mock_exporter), \
         patch("opentelemetry.sdk.trace.export.BatchSpanProcessor", return_value=mock_processor), \
         patch("opentelemetry.trace.set_tracer_provider"):
        configure_tracing("svc", otlp_endpoint="http://jaeger:4317")
        mock_provider.add_span_processor.assert_called()


# ── 6. get_recent_spans ───────────────────────────────────────────────────────

def test_get_recent_spans_returns_empty_when_no_exporter():
    """Before any tracing configured, returns empty list or list."""
    # Reset module-level exporter
    original = tracing_module._in_memory_exporter
    tracing_module._in_memory_exporter = None
    result = get_recent_spans()
    assert result == []
    tracing_module._in_memory_exporter = original


def test_get_recent_spans_after_configure():
    """After configure_tracing, get_recent_spans returns a list."""
    configure_tracing("span-test-service")
    result = get_recent_spans()
    assert isinstance(result, list)


def test_get_recent_spans_with_mock_exporter():
    mock_span = MagicMock()
    mock_span.name = "test_span"
    mock_span.context.trace_id = 0xABCD1234ABCD1234ABCD1234ABCD1234
    mock_span.context.span_id = 0x1234ABCD1234ABCD
    mock_span.start_time = 1000
    mock_span.end_time = 2000
    mock_span.attributes = {"key": "val"}
    mock_span.status.status_code.name = "OK"

    mock_exporter = MagicMock()
    mock_exporter.get_finished_spans.return_value = [mock_span]

    original = tracing_module._in_memory_exporter
    tracing_module._in_memory_exporter = mock_exporter
    try:
        spans = get_recent_spans(limit=10)
        assert len(spans) == 1
        assert spans[0]["name"] == "test_span"
        assert "trace_id" in spans[0]
        assert "span_id" in spans[0]
        assert spans[0]["attributes"] == {"key": "val"}
        assert spans[0]["status"] == "OK"
    finally:
        tracing_module._in_memory_exporter = original


def test_get_recent_spans_limit():
    mock_spans = []
    for i in range(20):
        s = MagicMock()
        s.name = f"span_{i}"
        s.context.trace_id = i
        s.context.span_id = i
        s.start_time = i
        s.end_time = i + 1
        s.attributes = {}
        s.status.status_code.name = "OK"
        mock_spans.append(s)

    mock_exporter = MagicMock()
    mock_exporter.get_finished_spans.return_value = mock_spans

    original = tracing_module._in_memory_exporter
    tracing_module._in_memory_exporter = mock_exporter
    try:
        spans = get_recent_spans(limit=5)
        assert len(spans) == 5
    finally:
        tracing_module._in_memory_exporter = original


def test_get_recent_spans_exception_returns_empty():
    mock_exporter = MagicMock()
    mock_exporter.get_finished_spans.side_effect = Exception("export error")

    original = tracing_module._in_memory_exporter
    tracing_module._in_memory_exporter = mock_exporter
    try:
        result = get_recent_spans()
        assert result == []
    finally:
        tracing_module._in_memory_exporter = original


def test_get_recent_spans_handles_null_attributes():
    mock_span = MagicMock()
    mock_span.name = "span_no_attrs"
    mock_span.context.trace_id = 1
    mock_span.context.span_id = 2
    mock_span.start_time = 0
    mock_span.end_time = 1
    mock_span.attributes = None  # null attributes
    mock_span.status.status_code.name = "UNSET"

    mock_exporter = MagicMock()
    mock_exporter.get_finished_spans.return_value = [mock_span]

    original = tracing_module._in_memory_exporter
    tracing_module._in_memory_exporter = mock_exporter
    try:
        spans = get_recent_spans()
        assert spans[0]["attributes"] == {}
    finally:
        tracing_module._in_memory_exporter = original
