"""OpenTelemetry tracing bootstrap.

Instruments the FastAPI app and configures an OTLP exporter when an endpoint is set.
When no endpoint is configured (local dev / tests) tracing is a no-op so nothing is
exported and startup stays fast.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import Settings

if TYPE_CHECKING:
    from fastapi import FastAPI


def configure_tracing(app: FastAPI, settings: Settings) -> None:
    """Wire OTel tracing for the app if an OTLP endpoint is configured."""
    if not settings.otel_exporter_otlp_endpoint:
        return

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": settings.service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
