"""OpenTelemetry tracing bootstrap.

Instruments the FastAPI app and configures an OTLP exporter when an endpoint is set.
When no endpoint is configured (local dev / tests) an in-process InMemorySpanExporter
is used so spans are always recorded — useful for the replay API and local debugging.
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger


# Module-level in-memory exporter; populated by _add_console_span_processor
_in_memory_exporter: Any = None


def configure_tracing(service_name: str, otlp_endpoint: str | None = None) -> None:
    """Configure OpenTelemetry tracing.

    When OTLP endpoint is set: exports to Jaeger/Collector.
    Otherwise: uses in-process SimpleSpanProcessor for local debugging.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name})
    )

    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            get_logger(__name__).info("otlp_tracing_enabled", endpoint=otlp_endpoint)
        except Exception as exc:
            get_logger(__name__).warning("otlp_exporter_failed", error=str(exc))
            _add_console_span_processor(provider, service_name)
    else:
        # Always have in-process span tracking (useful in dev for replay/debugging)
        _add_console_span_processor(provider, service_name)
        get_logger(__name__).info("in_process_tracing_enabled_no_otlp")

    trace.set_tracer_provider(provider)


def _add_console_span_processor(provider: Any, service_name: str) -> None:
    """Add an in-memory span store for local tracing without OTLP."""
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    global _in_memory_exporter
    _in_memory_exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(_in_memory_exporter))


def get_recent_spans(limit: int = 100) -> list[dict]:
    """Get recently recorded in-process spans for debugging."""
    if _in_memory_exporter is None:
        return []
    try:
        spans = _in_memory_exporter.get_finished_spans()
        return [
            {
                "name": s.name,
                "trace_id": format(s.context.trace_id, "032x"),
                "span_id": format(s.context.span_id, "016x"),
                "start_time": s.start_time,
                "end_time": s.end_time,
                "attributes": dict(s.attributes or {}),
                "status": s.status.status_code.name,
            }
            for s in spans[-limit:]
        ]
    except Exception:
        return []
