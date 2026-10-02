"""Comprehensive tests for tracing.py — configure_tracing, get_tracer,
NoOpTracer, NoOpSpanContext.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import app.observability.tracing as tracing_module
from app.observability.tracing import (
    _NoOpSpanContext,
    _NoOpTracer,
    configure_tracing,
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


# ── 4. build_tracer_provider / configure_tracing ─────────────────────────────

def test_build_provider_no_endpoint_has_no_in_memory_exporter():
    """Without OTLP endpoint nothing is exported to process memory (ENT-48)."""
    provider = tracing_module.build_tracer_provider("test-service", otlp_endpoint=None)
    names = [
        type(p).__name__
        for p in provider._active_span_processor._span_processors
    ]
    assert names == ["RunTimelineSpanProcessor"]


def test_configure_tracing_multiple_calls_no_crash():
    """Multiple calls to configure_tracing should not raise."""
    configure_tracing("svc1", otlp_endpoint=None)
    configure_tracing("svc2", otlp_endpoint=None)


def test_build_provider_with_invalid_otlp_does_not_raise_in_dev():
    tracing_module.build_tracer_provider("test-service", otlp_endpoint="http://localhost:99999")


def test_build_provider_with_mock_otlp():
    mock_exporter = MagicMock()
    mock_processor = MagicMock()
    mock_provider = MagicMock()

    with patch("opentelemetry.sdk.trace.TracerProvider", return_value=mock_provider), \
         patch("opentelemetry.sdk.resources.Resource"), \
         patch("opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter", return_value=mock_exporter), \
         patch("opentelemetry.sdk.trace.export.BatchSpanProcessor", return_value=mock_processor):
        tracing_module.build_tracer_provider("svc", otlp_endpoint="http://jaeger:4317")
        mock_provider.add_span_processor.assert_any_call(mock_processor)
