"""OpenTelemetry tracing bootstrap.

Instruments the FastAPI app and configures an OTLP exporter when an endpoint is set.
Tenant-stamped spans are additionally batched into a bounded, per-tenant Redis store
(``app.observability.span_store``) behind the in-app span view. No process-memory
span exporter is used.
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


# Tenant-scoped recent-span store (Redis) backing GET /analytics/observability/spans.
# None when no Redis is configured — the route then answers 503.
_span_store: Any = None

# Process-wide per-goal step-timeline store, fed by the RunTimelineSpanProcessor and
# read by the Run Inspector API. Swappable for a Redis-backed store in prod wiring.
_run_timeline_store: Any = None

# The provider installed by configure_tracing (OTel allows setting it only once).
_installed_provider: Any = None


def get_run_timeline_store() -> Any:
    """Return the process-wide run-timeline store (created lazily)."""
    global _run_timeline_store
    if _run_timeline_store is None:
        from app.observability.run_timeline import InMemoryRunTimelineStore

        _run_timeline_store = InMemoryRunTimelineStore()
    return _run_timeline_store


def get_span_store() -> Any:
    """Return the tenant span store wired by configure_tracing (None if no Redis)."""
    return _span_store


def build_tracer_provider(
    service_name: str,
    otlp_endpoint: str | None = None,
    *,
    span_store: Any = None,
    production: bool = False,
) -> Any:
    """Build a TracerProvider.

    * OTLP endpoint set: spans export to the collector. If the exporter cannot be
      built in production this raises — a production process must not silently
      run with no trace export.
    * ``span_store`` given: tenant-stamped spans are also batched into the
      bounded per-tenant Redis store behind the in-app span view.
    No process-memory span exporter is ever installed.
    """
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    log = get_logger(__name__)

    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            log.info("otlp_tracing_enabled", endpoint=otlp_endpoint)
        except Exception as exc:
            if production:
                raise RuntimeError(
                    f"OTLP trace exporter could not be initialised for {otlp_endpoint}"
                ) from exc
            log.warning("otlp_exporter_failed", error=str(exc))

    if span_store is not None:
        from app.observability.span_store import TenantSpanExporter

        provider.add_span_processor(BatchSpanProcessor(TenantSpanExporter(span_store)))

    # Per-goal step timeline: capture goal-scoped spans into a queryable store so
    # the Run Inspector can render how a goal ran without depending on Jaeger/
    # Langfuse retention. Independent of whether OTLP export is on.
    from app.observability.run_timeline import RunTimelineSpanProcessor

    provider.add_span_processor(RunTimelineSpanProcessor(get_run_timeline_store()))
    return provider


def configure_tracing(
    service_name: str,
    otlp_endpoint: str | None = None,
    *,
    redis_url: str | None = None,
    production: bool = False,
) -> None:
    """Configure process-wide OpenTelemetry tracing (idempotent).

    OTel lets a process install its global TracerProvider only once, so later
    calls (e.g. a second ``create_app`` in the same process) are no-ops rather
    than building providers whose background export threads would never be used.
    """
    global _installed_provider, _span_store
    if _installed_provider is not None:
        return
    from opentelemetry import trace

    store = None
    if redis_url:
        from app.observability.span_store import RedisSpanStore

        store = RedisSpanStore(redis_url=redis_url)
    provider = build_tracer_provider(
        service_name, otlp_endpoint, span_store=store, production=production
    )
    trace.set_tracer_provider(provider)
    _installed_provider = provider
    _span_store = store
    if not otlp_endpoint:
        get_logger(__name__).info("tracing_enabled_no_otlp", span_store=store is not None)


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
