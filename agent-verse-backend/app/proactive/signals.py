"""Proactive signals (Phase 9).

A *signal* is an observation that might warrant the assistant reaching out:
a calendar change, an inbound email, a stalled thread, a memory-derived
follow-up, or a trigger fire. A trusted producer posts it to
``POST /v1/proactive/signals``, which hands it straight to the app's
``ProactiveEngine`` (``app.state.proactive_engine``).

There is no in-process signal bus: the one that lived here (``SignalBus``) had
no producer or subscriber outside its own tests, and a per-process fan-out would
not reach the engine on another replica anyway (a10-F227-04).
"""

from __future__ import annotations

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
