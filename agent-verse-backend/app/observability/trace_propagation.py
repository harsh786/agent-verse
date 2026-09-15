"""W3C trace-context propagation across process/message boundaries.

Goals execute in Celery workers and inbound turns arrive over channels; the OTel
active context does not cross those boundaries by itself. These helpers serialise
the current context into a header carrier (``inject_trace_headers``) on the
producing side and re-attach it on the consuming side (``use_trace_context``), so
the worker's ``goal.run`` span becomes a child of the submitting request's span.

Uses the globally-configured propagator (W3C ``traceparent`` by default) and never
raises — a missing/blank carrier is a no-op so pre-instrumentation messages and
non-traced call paths keep working.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping

from opentelemetry import context as otel_context
from opentelemetry import propagate
from opentelemetry.context import Context


def inject_trace_headers(carrier: dict[str, str] | None = None) -> dict[str, str]:
    """Return a header dict carrying the current trace context (W3C traceparent)."""
    out: dict[str, str] = {} if carrier is None else carrier
    with contextlib.suppress(Exception):
        propagate.inject(out)
    return out


def extract_trace_context(headers: Mapping[str, str] | None) -> Context:
    """Build an OTel Context from a header carrier (empty carrier → empty context)."""
    try:
        return propagate.extract(dict(headers) if headers else {})
    except Exception:
        return otel_context.get_current()


@contextlib.contextmanager
def use_trace_context(headers: Mapping[str, str] | None) -> Iterator[None]:
    """Attach the trace context carried in ``headers`` for the duration of the block."""
    ctx = extract_trace_context(headers)
    token = otel_context.attach(ctx)
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            otel_context.detach(token)
