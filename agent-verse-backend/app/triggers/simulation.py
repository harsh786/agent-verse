"""Trigger simulation mode and chaos testing harness."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime

from app.triggers.events import SimulatedTriggerResult
from app.triggers.models import TriggerSpec, TriggerType


# ── Test payload factory ──────────────────────────────────────────────────────

_SAMPLE_PAYLOADS: dict[str, dict] = {
    "cron":               {"scheduled_at": "2026-08-16T09:00:00Z"},
    "interval":           {"interval_seconds": 3600, "fired_at": "2026-08-16T09:00:00Z"},
    "once":               {"fire_at_iso": "2026-08-16T09:00:00Z"},
    "goal_completed":     {"goal_id": "gol-abc123", "agent_id": "agt-001", "score": 0.95,
                           "output": {"invoice_id": "INV-001"}},
    "goal_failed":        {"goal_id": "gol-abc123", "error": "Provider timeout"},
    "goal_score_below":   {"goal_id": "gol-abc123", "overall_score": 0.45},
    "hitl_approved":      {"approval_id": "appr-001", "approver": "user@example.com"},
    "hitl_rejected":      {"approval_id": "appr-001", "reason": "Not authorized"},
    "chat_command":       {"command": "/run-report", "user_id": "U12345",
                           "channel_id": "C04ABC", "user_name": "alice"},
    "chat_keyword":       {"message_text": "urgent alert detected", "user_name": "bob"},
    "webhook":            {"headers": {}, "body": {"event": "push"}},
    "github_webhook":     {"action": "push", "repository": {"full_name": "acme/backend"},
                           "ref": "refs/heads/main",
                           "head_commit": {"message": "fix: resolve issue"}},
    "stripe_webhook":     {"type": "payment_intent.succeeded",
                           "data": {"object": {"amount": 5000, "currency": "usd"}}},
    "db_row_change":      {"table": "orders", "operation": "INSERT",
                           "primary_key": "42", "new_row": {"status": "pending"}},
    "pagerduty":          {"event_type": "trigger", "service": {"name": "API"},
                           "urgency": "high"},
    "mqtt":               {"topic": "sensors/temp/01", "payload": "87.5",
                           "device_id": "sensor-01"},
}


def get_sample_payload(trigger_type: str | TriggerType) -> dict:
    """Return a realistic sample payload for the given trigger type."""
    type_str = trigger_type.value if hasattr(trigger_type, "value") else str(trigger_type)
    return _SAMPLE_PAYLOADS.get(type_str, {"trigger_type": type_str, "test": True})


# Backward-compat alias
test_payload_factory = get_sample_payload


# ── Chaos harness ─────────────────────────────────────────────────────────────

@dataclass
class ChaosStats:
    total_dispatched:     int = 0
    dlq_writes:           int = 0
    circuit_breaker_opens: int = 0
    signature_failures:   int = 0
    condition_timeouts:   int = 0


@dataclass
class TriggerChaosHarness:
    """Injects controlled failures for testing the dispatch pipeline."""

    inject_signature_failure_pct: int = 0    # 0–100
    inject_condition_timeout_pct: int = 0    # 0–100
    inject_goal_service_down_for: int = 0    # seconds (0 = never)
    stats: ChaosStats = field(default_factory=ChaosStats)
    _active: bool = False

    def __enter__(self) -> "TriggerChaosHarness":
        self._active = True
        return self

    def __exit__(self, *_: object) -> None:
        self._active = False

    def should_fail_signature(self) -> bool:
        if not self._active:
            return False
        if random.randint(1, 100) <= self.inject_signature_failure_pct:
            self.stats.signature_failures += 1
            return True
        return False

    def should_timeout_condition(self) -> bool:
        if not self._active:
            return False
        if random.randint(1, 100) <= self.inject_condition_timeout_pct:
            self.stats.condition_timeouts += 1
            return True
        return False
