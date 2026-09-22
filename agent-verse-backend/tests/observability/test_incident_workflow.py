"""Production-incident diagnosability (Area 11 of the coverage audit).

RUNBOOK.md and the individual observability unit tests each cover one signal in
isolation (a log line, a span, a metric sample). None of them prove that, when a
tool genuinely misbehaves in production, an operator can actually reconstruct
*what happened* by following the correlation IDs across those three signals.

This test simulates the textbook incident — a connector starts failing, the
circuit breaker trips after enough consecutive failures, and further calls are
short-circuited — using the platform's real observability primitives
(``app.observability.logging``, OpenTelemetry spans, ``app.observability.metrics``,
and ``app.reliability.circuit_breaker.CircuitBreaker``, wired together the same
way ``app/agent/nodes/executor_mixin.py`` and ``app/mcp/client.py`` wire them:
one span per step/tool-call attempt, structlog with bound goal/tenant context
plus OTel trace-correlation, and ``record_tool_call`` metrics per attempt).

It then asserts the emitted telemetry is actually *diagnosable*: every failure
log line carries the goal_id and a trace_id; every trace_id traces back to the
same incident's span tree; each attempt's log line points at the exact span for
that attempt (not just "some span in the trace"); the span tree records the
real exception text and an ERROR status; the circuit breaker's own state
explains *why* later attempts were short-circuited instead of retried; and the
Prometheus counters an operator would see on a dashboard (``failed`` vs.
``circuit_open`` tool-call counts) match the incident exactly.
"""
from __future__ import annotations

import io
import json
import time

import pytest
import structlog
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode

from app.observability import metrics
from app.observability.logging import add_trace_correlation

from app.reliability.circuit_breaker import CircuitBreaker, CircuitState

GOAL_ID = "goal-inc-7f3a2b"
TENANT_ID = "tenant-acme"
SERVER_ID = "jira-prod"
TOOL_NAME = "jira_create_issue"
CONNECTOR_NAME = "jira"
FAILURE_MESSAGE = "Jira API 503: upstream connector timeout"


@pytest.fixture
def tracer_and_exporter():
    """A real, isolated OTel tracer (own provider + in-memory exporter) — mirrors
    the setup in tests/observability/test_log_trace_correlation.py so span context
    propagation (trace.get_current_span()) works without touching global OTel
    process state."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("agentverse.incident_test")
    return tracer, exporter


@pytest.fixture
def captured_logs():
    """Configure structlog exactly like app.observability.logging.configure_logging
    (contextvars + trace correlation + JSON rendering), but writing to an in-memory
    buffer we can parse, and restore whatever structlog config was active before."""
    stream = io.StringIO()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_trace_correlation,
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=stream),
        cache_logger_on_first_use=False,
    )
    try:
        yield stream
    finally:
        structlog.contextvars.clear_contextvars()
        structlog.reset_defaults()


def _log_lines(stream: io.StringIO) -> list[dict]:
    stream.seek(0)
    return [json.loads(line) for line in stream.read().splitlines() if line.strip()]


def _metric_delta(name: str, labels: dict[str, str], before: float | None) -> float:
    after = metrics.REGISTRY.get_sample_value(name, labels) or 0.0
    return after - (before or 0.0)


def test_repeated_tool_failure_trips_circuit_and_is_fully_diagnosable(
    tracer_and_exporter, captured_logs
):
    tracer, exporter = tracer_and_exporter
    logger = structlog.get_logger("app.mcp.client")
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=9999)

    failed_labels = {"tool": "jira", "connector": "jira", "status": "failed"}
    blocked_labels = {"tool": "jira", "connector": "jira", "status": "circuit_open"}
    failed_count_before = metrics.REGISTRY.get_sample_value(
        "agentverse_tool_call_total", failed_labels
    )
    blocked_count_before = metrics.REGISTRY.get_sample_value(
        "agentverse_tool_call_total", blocked_labels
    )

    # ── Simulate the incident ────────────────────────────────────────────────
    structlog.contextvars.bind_contextvars(goal_id=GOAL_ID, tenant_id=TENANT_ID)
    with tracer.start_as_current_span("agentverse.goal.run") as root_span:
        root_trace_id = format(root_span.get_span_context().trace_id, "032x")

        for attempt in range(1, 5):
            if not breaker.can_call():
                logger.warning(
                    "tool_call_blocked_circuit_open",
                    tool=TOOL_NAME,
                    server_id=SERVER_ID,
                    attempt=attempt,
                    circuit_state=breaker.state.value,
                )
                metrics.record_tool_call(TOOL_NAME, CONNECTOR_NAME, "circuit_open", 0.0)
                continue

            with tracer.start_as_current_span("agentverse.tool.call") as span:
                span.set_attribute("agentverse.tool.name", TOOL_NAME)
                span.set_attribute("agentverse.tool.server_id", SERVER_ID)
                span.set_attribute("agentverse.tool.attempt", attempt)
                start = time.monotonic()
                try:
                    raise RuntimeError(FAILURE_MESSAGE)
                except RuntimeError as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    elapsed = time.monotonic() - start
                    breaker.record_failure()
                    metrics.record_tool_call(TOOL_NAME, CONNECTOR_NAME, "failed", elapsed)
                    logger.error(
                        "tool_call_failed",
                        tool=TOOL_NAME,
                        server_id=SERVER_ID,
                        attempt=attempt,
                        error=str(exc),
                        circuit_state=breaker.state.value,
                    )

    # ── An operator's first question: did the circuit trip, and why? ───────────
    assert breaker.state == CircuitState.OPEN
    assert not breaker.can_call()

    # ── Logs: every incident log line is tagged with the goal and a trace ──────
    lines = _log_lines(captured_logs)
    failure_lines = [line for line in lines if line["event"] == "tool_call_failed"]
    blocked_lines = [line for line in lines if line["event"] == "tool_call_blocked_circuit_open"]

    assert len(failure_lines) == 3  # attempts 1-3 (threshold=3)
    assert len(blocked_lines) == 1  # attempt 4, short-circuited

    for line in failure_lines + blocked_lines:
        assert line["goal_id"] == GOAL_ID
        assert line["tenant_id"] == TENANT_ID
        assert line["level"] == ("error" if line["event"] == "tool_call_failed" else "warning")
        # Every log line in this incident belongs to the same trace — an operator
        # can pivot from any one line straight to the full span tree.
        assert line["trace_id"] == root_trace_id
        assert len(line["trace_id"]) == 32

    # The failure reason is preserved verbatim in the logs, not swallowed.
    assert all(line["error"] == FAILURE_MESSAGE for line in failure_lines)
    # The blocked attempt's log line explains *why* no call was attempted — the
    # open circuit — which is exactly what an operator needs to avoid chasing a
    # phantom 4th failure that never actually hit the network.
    assert blocked_lines[0]["circuit_state"] == "open"
    assert [line["attempt"] for line in failure_lines] == [1, 2, 3]
    assert blocked_lines[0]["attempt"] == 4

    # ── Spans: one root + one per real attempt (never for the blocked call) ────
    finished = exporter.get_finished_spans()
    root_spans = [s for s in finished if s.name == "agentverse.goal.run"]
    tool_spans = [s for s in finished if s.name == "agentverse.tool.call"]
    assert len(root_spans) == 1
    assert len(tool_spans) == 3  # not 4 — the blocked attempt made no call, no span

    for span in tool_spans:
        assert format(span.context.trace_id, "032x") == root_trace_id
        assert span.status.status_code == StatusCode.ERROR
        assert span.status.description == FAILURE_MESSAGE
        assert span.attributes["agentverse.tool.name"] == TOOL_NAME
        assert len(span.events) == 1
        exc_event = span.events[0]
        assert exc_event.name == "exception"
        assert exc_event.attributes["exception.message"] == FAILURE_MESSAGE
        assert exc_event.attributes["exception.type"] == "RuntimeError"

    # ── Cross-signal correlation: each failure log line's span_id resolves to
    # the exact span for *that* attempt, not merely "a span in the trace" ──────
    spans_by_attempt = {s.attributes["agentverse.tool.attempt"]: s for s in tool_spans}
    for line in failure_lines:
        matching_span = spans_by_attempt[line["attempt"]]
        assert line["span_id"] == format(matching_span.context.span_id, "016x")

    # ── Metrics: the dashboard counters an operator would actually look at ─────
    assert _metric_delta("agentverse_tool_call_total", failed_labels, failed_count_before) == 3
    assert _metric_delta("agentverse_tool_call_total", blocked_labels, blocked_count_before) == 1
