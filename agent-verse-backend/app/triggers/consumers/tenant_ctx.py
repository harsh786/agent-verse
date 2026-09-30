"""Tenant context for event-bus trigger firings (TRG-05).

Event payloads are data: parts of them come from clients (``POST
/triggers/events/{channel}`` bodies, chat messages, webhook fields). The plan
tier drives the dispatcher's rate cap, bulkhead, payload limit and queue, so it
is resolved from the tenant record at fire time — never read from the event.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

from app.tenancy.context import PlanTier

# Keys a publisher stamps server-side; a client-supplied value is dropped.
RESERVED_EVENT_KEYS: frozenset[str] = frozenset({"tenant_id", "tenant_plan", "event_channel"})


def strip_reserved(payload: dict[str, Any] | None) -> dict[str, Any]:
    """A copy of a client payload without the server-stamped keys."""
    return {k: v for k, v in (payload or {}).items() if k not in RESERVED_EVENT_KEYS}


async def event_tenant_ctx(dispatcher: Any, tenant_id: str) -> SimpleNamespace:
    """``SimpleNamespace(tenant_id, plan)`` with the plan from the tenant record.

    Asks the dispatcher (``TriggerDispatcher.resolve_tenant_plan``); a dispatcher
    without a resolver, or an answer that is not a plan, yields FREE — an unknown
    plan never grants a paid tier's limits.
    """
    plan: Any = PlanTier.FREE
    resolver = getattr(dispatcher, "resolve_tenant_plan", None)
    if callable(resolver):
        result = resolver(tenant_id)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, str):
            try:
                plan = PlanTier(str(getattr(result, "value", result)))
            except ValueError:
                plan = PlanTier.FREE
    return SimpleNamespace(tenant_id=tenant_id, plan=plan)
