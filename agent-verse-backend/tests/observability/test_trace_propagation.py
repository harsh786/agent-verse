"""W3C trace-context propagation across process boundaries (Celery, channels).

Goals actually execute inside Celery workers; without propagating the active
trace into the task, the goal.run span tree is severed from the submitting
request. These helpers inject the context into a header carrier on submit and
re-attach it at task start.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from app.observability.trace_propagation import (
    extract_trace_context,
    inject_trace_headers,
    use_trace_context,
)


def test_inject_then_extract_preserves_trace_id() -> None:
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("submit") as span:
        expected = span.get_span_context().trace_id
        headers = inject_trace_headers()

    assert "traceparent" in headers  # W3C standard header
    ctx = extract_trace_context(headers)
    extracted = trace.get_current_span(ctx).get_span_context()
    assert extracted.trace_id == expected


def test_use_trace_context_attaches_parent() -> None:
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("submit") as parent:
        expected = parent.get_span_context().trace_id
        headers = inject_trace_headers()

    # Simulate the worker: no active span, then re-attach from headers and open a child.
    with use_trace_context(headers):
        with tracer.start_as_current_span("worker") as child:
            assert child.get_span_context().trace_id == expected


def test_empty_headers_are_safe() -> None:
    # A task enqueued before instrumentation (no headers) must not blow up.
    ctx = extract_trace_context({})
    assert ctx is not None
    with use_trace_context(None):
        pass  # no-op, no raise
