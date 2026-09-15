"""Proactive signals + a lightweight in-process signal bus (Phase 9).

A *signal* is an observation that might warrant the assistant reaching out:
a calendar change, an inbound email, a stalled thread, a memory-derived
follow-up, or a trigger fire. The bus fans signals out to registered async
handlers (the engine subscribes). It is deliberately transport-agnostic — a
Redis/Celery-backed bus can implement the same ``publish`` contract later.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


class SignalKind:
    """Well-known signal kinds (open set — arbitrary strings are allowed)."""

    CALENDAR_EVENT = "calendar_event"
    EVENT_CANCELLED = "event_cancelled"
    FLIGHT_DELAYED = "flight_delayed"
    INBOUND_EMAIL = "inbound_email"
    STALLED_THREAD = "stalled_thread"
    MEMORY_FOLLOWUP = "memory_followup"
    TRIGGER_FIRE = "trigger_fire"


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ProactiveSignal:
    kind: str
    tenant_id: str
    # Principal to reach (defaults to the tenant when individual identity is absent).
    principal_id: str
    # Preferred delivery channel for any resulting outreach.
    channel: str = "web"
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=_now)


Handler = Callable[[ProactiveSignal], Awaitable[Any]]


class SignalBus:
    """In-process fan-out of signals to async handlers."""

    def __init__(self) -> None:
        self._handlers: list[Handler] = []

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    async def publish(self, signal: ProactiveSignal) -> list[Any]:
        """Deliver a signal to every handler; returns each handler's result."""
        results: list[Any] = []
        for handler in self._handlers:
            results.append(await handler(signal))
        return results
