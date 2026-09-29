"""Structured (JSON) logging via structlog.

INFO-and-above structured logs in all environments except local development, where a
human-readable console renderer is used instead. Bound context (request_id, tenant_id,
goal_id) flows through every log line for distributed tracing correlation.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import re
from typing import Any, cast

import structlog
from opentelemetry import trace


def add_trace_correlation(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor: stamp the active OTel trace/span id onto every log line.

    This is what makes logs jump to their trace (and vice-versa) in Grafana/Loki/
    Jaeger. No-op when there is no active recording span. Never raises.
    """
    with contextlib.suppress(Exception):
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
            event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


# Keys already carried as first-class fields of a log-store entry, or noise.
_FEED_SKIP_KEYS = frozenset(
    {"event", "level", "timestamp", "tenant_id", "_record", "_from_structlog"}
)
_SECRET_KEY_RE = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key)",
    re.IGNORECASE,
)
_MAX_FEED_FIELDS = 32
_feeding: contextvars.ContextVar[bool] = contextvars.ContextVar("_log_feed_active", default=False)


def _bound_tenant_and_goal() -> tuple[str, str]:
    """Tenant/goal bound for this context: structlog contextvars, else the goal-run
    OTel baggage (``run_context``), which also flows into Celery workers."""
    bound = structlog.contextvars.get_contextvars()
    tenant = str(bound.get("tenant_id") or "")
    goal = str(bound.get("goal_id") or "")
    if not tenant or not goal:
        from app.observability.trace_propagation import current_run_baggage

        bag = current_run_baggage()
        tenant = tenant or bag.get("tenant_id", "")
        goal = goal or bag.get("goal_id", "")
    return tenant, goal


def feed_tenant_log_store(
    _logger: Any, method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor: record tenant-bound log lines into the tenant log store.

    Only lines emitted while a tenant is *bound* (contextvars / run baggage) are
    recorded — an explicit ``tenant_id=`` kwarg is not enough, so one tenant's code
    path cannot write into another tenant's log view. Secret-looking fields are
    redacted and values truncated. Never raises and never alters *event_dict*.
    """
    if _feeding.get():
        return event_dict
    token = _feeding.set(True)
    try:
        tenant_id, goal_id = _bound_tenant_and_goal()
        if not tenant_id:
            return event_dict
        from app.observability.log_store import StructuredLogStore, log_store

        limit = StructuredLogStore.MAX_FIELD_CHARS
        entry: dict[str, Any] = {
            "level": str(event_dict.get("level") or method or "info").lower(),
            "message": str(event_dict.get("event", ""))[:limit],
            "source": str(_logger.name) if getattr(_logger, "name", None) else "",
            "goal_id": goal_id,
        }
        if event_dict.get("timestamp"):
            entry["timestamp"] = str(event_dict["timestamp"])
        extra = 0
        for key, value in event_dict.items():
            if key in _FEED_SKIP_KEYS or key in entry:
                continue
            if extra >= _MAX_FEED_FIELDS:
                break
            extra += 1
            entry[key] = "[redacted]" if _SECRET_KEY_RE.search(key) else str(value)[:limit]
        log_store.record_nowait(tenant_id, entry)
    except Exception:
        # Observability must never break the line being logged.
        return event_dict
    finally:
        _feeding.reset(token)
    return event_dict


def configure_logging(*, level: str = "INFO", json_logs: bool = True) -> None:
    """Configure structlog + stdlib logging once at startup."""
    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper(), logging.INFO))

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        add_trace_correlation,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # After contextvars merge + timestamp/level, before rendering.
        feed_tenant_log_store,
    ]
    processors.append(
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
