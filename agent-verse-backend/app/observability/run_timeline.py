"""Per-goal step timeline — a platform-owned, queryable view of how a goal ran.

An OTel ``SpanProcessor`` captures every goal-scoped span (identified by the
``agentverse.goal_id`` attribute the run baggage stamps) into a compact,
per-goal, tenant-scoped timeline: model calls with tokens/cost/latency, tool
calls, and LangGraph nodes, each carrying its trace/span id for deep-linking into
Jaeger/Langfuse. This lets the frontend Run Inspector render "how this goal
worked" from the platform itself, independent of Jaeger/Langfuse retention.

The store is an interface: an in-memory implementation (default, per-process) and
a Redis-backed one (durable, multi-worker) can be swapped in at wiring time. The
processor's ``on_end`` is best-effort and never raises into the export path.
"""

from __future__ import annotations

import contextlib
import threading
from collections import defaultdict, deque
from typing import Any, Protocol

from opentelemetry.sdk.trace import SpanProcessor

_MAX_ENTRIES = 500


class RunTimelineStore(Protocol):
    def append(self, tenant_id: str, goal_id: str, entry: dict[str, Any]) -> None: ...
    def get(self, tenant_id: str, goal_id: str) -> list[dict[str, Any]]: ...


class InMemoryRunTimelineStore:
    """Process-local timeline store (default). Capped per goal; thread-safe."""

    def __init__(self, max_entries: int = _MAX_ENTRIES) -> None:
        self._max = max_entries
        self._data: dict[tuple[str, str], deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=self._max)
        )
        self._lock = threading.Lock()

    def append(self, tenant_id: str, goal_id: str, entry: dict[str, Any]) -> None:
        with self._lock:
            self._data[(tenant_id, goal_id)].append(entry)

    def get(self, tenant_id: str, goal_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._data.get((tenant_id, goal_id), ()))


def _num(value: Any) -> Any:
    return value if isinstance(value, (int, float)) else None


class RunTimelineSpanProcessor(SpanProcessor):
    """OTel SpanProcessor that records goal-scoped spans into a RunTimelineStore.

    Only spans stamped with ``agentverse.goal_id`` (i.e. created inside a goal run)
    are captured; everything else is ignored. Never raises.
    """

    def __init__(self, store: RunTimelineStore) -> None:
        self._store = store

    # -- SpanProcessor interface ----------------------------------------------

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        return None

    def on_end(self, span: Any) -> None:
        with contextlib.suppress(Exception):
            attrs = dict(getattr(span, "attributes", None) or {})
            goal_id = attrs.get("agentverse.goal_id")
            if not goal_id:
                return
            tenant_id = str(attrs.get("agentverse.tenant_id") or "")
            ctx = span.get_span_context()
            start = getattr(span, "start_time", 0) or 0
            end = getattr(span, "end_time", 0) or 0
            entry: dict[str, Any] = {
                "name": getattr(span, "name", ""),
                "start_ns": start,
                "duration_ms": max(0.0, (end - start) / 1_000_000.0),
                "role": attrs.get("agentverse.role"),
                "model": attrs.get("gen_ai.request.model"),
                "input_tokens": _num(attrs.get("gen_ai.usage.input_tokens")),
                "output_tokens": _num(attrs.get("gen_ai.usage.output_tokens")),
                "cost_usd": _num(attrs.get("gen_ai.usage.cost_usd")),
                "tool": attrs.get("tool") or attrs.get("tool.name"),
                "status": getattr(getattr(span, "status", None), "status_code", None)
                and span.status.status_code.name,
                "trace_id": format(ctx.trace_id, "032x"),
                "span_id": format(ctx.span_id, "016x"),
            }
            self._store.append(tenant_id, str(goal_id), entry)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True
