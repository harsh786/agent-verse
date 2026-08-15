"""TriggerEvent and TriggerAuditEvent dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TriggerEvent:
    """Immutable record of a single trigger firing."""

    event_id:        str
    tenant_id:       str
    trigger_id:      str
    trigger_type:    str
    idempotency_key: str
    fired_at:        datetime
    payload:         dict = field(default_factory=dict)
    goal_created:    bool = False
    goal_id:         str | None = None
    skip_reason:     str | None = None   # dedup | rate_limit | condition_false | circuit_open | bulkhead_full
    processing_ms:   int | None = None


@dataclass
class TriggerAuditEvent:
    """Append-only audit record for trigger CRUD / lifecycle changes."""

    event_id:     str
    tenant_id:    str
    trigger_id:   str | None
    actor_id:     str
    actor_role:   str
    action:       str   # create | update | enable | disable | delete | fire_manual | rotate_secret
    before_state: dict | None = None
    after_state:  dict | None = None
    occurred_at:  datetime = field(default_factory=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    ip_address:   str | None = None
    request_id:   str | None = None


@dataclass
class SimulatedTriggerResult:
    """Result of a simulation-mode dispatch (no real goals created)."""

    trigger_id:              str
    trigger_type:            str
    would_have_fired:        bool
    skip_reason:             str | None
    goal_template_rendered:  str
    condition_evaluated:     bool | None
    estimated_cost_usd:      float | None
    simulated_at:            datetime = field(default_factory=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
