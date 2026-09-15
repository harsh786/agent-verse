"""OpenTelemetry tracing bootstrap.

Instruments the FastAPI app and configures an OTLP exporter when an endpoint is set.
When no endpoint is configured (local dev / tests) an in-process InMemorySpanExporter
is used so spans are always recorded — useful for the replay API and local debugging.
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

_SAFE_PATTERN_ATTRIBUTE_KEYS = frozenset(
    {
        "event",
        "family",
        "strategy",
        "phase",
        "status",
        "correlation_id",
        "causation_id",
        "classification",
        "limit_type",
        "fallback_reason",
    }
)


# Module-level in-memory exporter; populated by _add_console_span_processor
_in_memory_exporter: Any = None

# Process-wide per-goal step-timeline store, fed by the RunTimelineSpanProcessor and
# read by the Run Inspector API. Swappable for a Redis-backed store in prod wiring.
_run_timeline_store: Any = None


def get_run_timeline_store() -> Any:
    """Return the process-wide run-timeline store (created lazily)."""
    global _run_timeline_store
    if _run_timeline_store is None:
        from app.observability.run_timeline import InMemoryRunTimelineStore

        _run_timeline_store = InMemoryRunTimelineStore()
    return _run_timeline_store


def configure_tracing(service_name: str, otlp_endpoint: str | None = None) -> None:
    """Configure OpenTelemetry tracing.

    When OTLP endpoint is set: exports to Jaeger/Collector.
    Otherwise: uses in-process SimpleSpanProcessor for local debugging.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))

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

    # Per-goal step timeline: capture goal-scoped spans into a queryable store so
    # the Run Inspector can render how a goal ran without depending on Jaeger/
    # Langfuse retention. Independent of whether OTLP export is on.
    from app.observability.run_timeline import RunTimelineSpanProcessor

    provider.add_span_processor(RunTimelineSpanProcessor(get_run_timeline_store()))

    trace.set_tracer_provider(provider)


_libraries_instrumented = False


def instrument_libraries() -> None:
    """Idempotently apply process-global OTel instrumentors (HTTPX/SQLAlchemy/
    AsyncPG/Redis/Celery). Each is isolated so one failure can't block the rest,
    and never raises into startup. Gives outbound-HTTP, DB, cache and Celery spans
    so a goal's trace tree is complete end-to-end."""
    global _libraries_instrumented
    if _libraries_instrumented:
        return
    _libraries_instrumented = True
    log = get_logger(__name__)

    def _try(name: str, fn: Any) -> None:
        try:
            fn()
        except Exception as exc:  # already-instrumented or missing dep — non-fatal
            log.debug("instrumentor_skipped", instrumentor=name, error=str(exc)[:120])

    def _httpx() -> None:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()

    def _sqlalchemy() -> None:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument(enable_commenter=True)

    def _asyncpg() -> None:
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor

        AsyncPGInstrumentor().instrument()

    def _redis() -> None:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()

    def _celery() -> None:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        CeleryInstrumentor().instrument()

    _try("httpx", _httpx)
    _try("sqlalchemy", _sqlalchemy)
    _try("asyncpg", _asyncpg)
    _try("redis", _redis)
    _try("celery", _celery)


def instrument_app(app: Any) -> None:
    """Instrument the app's libraries, and (opt-in) FastAPI server spans.

    FastAPI ASGI instrumentation is OPT-IN via ``otel_instrument_fastapi``
    (default False): opentelemetry-instrumentation-fastapi's per-request
    span-detail resolver crashes on this app's nested/included router structure
    (``'_IncludedRouter' object has no attribute 'path'``), which 500s EVERY
    request — including the CORS preflight, breaking the browser with a "network
    error". The library instrumentors (HTTPX/DB/Redis/Celery) and our own manual
    spans (goal.run, gen_ai, …) are unaffected and always on, so the trace tree
    stays rich without the fragile server-span layer. Fail-safe throughout.
    """
    try:
        from app.core.config import get_settings

        fastapi_enabled = bool(getattr(get_settings(), "otel_instrument_fastapi", False))
    except Exception:
        fastapi_enabled = False

    if fastapi_enabled:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            if not getattr(app, "_is_instrumented_by_opentelemetry", False):
                FastAPIInstrumentor.instrument_app(app)
        except Exception as exc:
            get_logger(__name__).warning("fastapi_instrumentation_failed", error=str(exc)[:120])
    instrument_libraries()


def _add_console_span_processor(provider: Any, service_name: str) -> None:
    """Add an in-memory span store for local tracing without OTLP."""
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    global _in_memory_exporter
    _in_memory_exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(_in_memory_exporter))


def get_tracer(name: str) -> Any:
    """Get a named OTel tracer.  No-ops gracefully when OTel is not installed."""
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except Exception:
        return _NoOpTracer()


def safe_pattern_attributes(**attributes: object) -> dict[str, str | int | float | bool]:
    """Return a bounded trace-attribute map that cannot contain tenant or content data."""
    safe: dict[str, str | int | float | bool] = {}
    for key, value in attributes.items():
        if key not in _SAFE_PATTERN_ATTRIBUTE_KEYS or value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[f"agentverse.{key}"] = value
    return safe


class _NoOpTracer:
    """Fallback tracer when opentelemetry is unavailable (tests, minimal envs)."""

    def start_as_current_span(self, name: str, **_kwargs: Any) -> Any:
        return _NoOpSpanContext()


class _NoOpSpanContext:
    def __enter__(self) -> _NoOpSpanContext:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def set_attribute(self, key: str, value: object) -> None:
        pass

    def record_exception(self, exc: Exception) -> None:
        pass


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
