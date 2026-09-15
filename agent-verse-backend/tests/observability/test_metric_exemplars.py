"""Prometheus exemplars link latency metrics to the exact trace (Phase 4)."""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider

from app.observability import metrics


def test_trace_exemplar_none_without_span() -> None:
    assert metrics.trace_exemplar() is None


def test_observe_with_exemplar_attaches_trace_id_and_renders_openmetrics() -> None:
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("op") as span:
        tid = format(span.get_span_context().trace_id, "032x")
        assert metrics.trace_exemplar() == {"trace_id": tid}
        # Observe a real histogram child under the active span.
        metrics.observe_with_exemplar(
            metrics.GOAL_DURATION.labels(status="complete", priority="normal"),
            0.42,
        )

    body, content_type = metrics.render_metrics(accept="application/openmetrics-text")
    text = body.decode()
    # OpenMetrics exposition carries the exemplar with our trace id.
    assert "openmetrics" in content_type
    assert tid in text
    assert "trace_id" in text
    # Default (legacy) negotiation stays text/plain for existing consumers.
    _, legacy_ct = metrics.render_metrics()
    assert legacy_ct.startswith("text/plain")


def test_observe_with_exemplar_plain_observe_without_span() -> None:
    # No active span → plain observe, no raise, metric still recorded.
    before = metrics.GOAL_DURATION.labels(status="failed", priority="low")._sum.get()
    metrics.observe_with_exemplar(
        metrics.GOAL_DURATION.labels(status="failed", priority="low"), 1.0
    )
    after = metrics.GOAL_DURATION.labels(status="failed", priority="low")._sum.get()
    assert after == before + 1.0
