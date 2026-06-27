"""Trigger type definitions — 6 trigger types for agent scheduling.

Trigger types:
  CRON      → Celery Beat periodic task (cron expression + timezone)
  INTERVAL  → Celery Beat interval task (every N seconds)
  WEBHOOK   → HTTP POST to /webhooks/{token} fires the trigger
  EVENT     → Redis pub/sub channel event fires the trigger
  REST      → Manual HTTP call to /triggers/{id}/fire
  ONCE      → Celery ETA task (fires once at a specific time)
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class TriggerType(enum.StrEnum):
    CRON = "cron"
    INTERVAL = "interval"
    WEBHOOK = "webhook"
    EVENT = "event"
    REST = "rest"
    ONCE = "once"
    FILE_DROP = "file_drop"
    ALERTMANAGER = "alertmanager"
    DATADOG = "datadog"
    PAGERDUTY = "pagerduty"


@dataclass
class TriggerSpec:
    trigger_type: TriggerType = TriggerType.ONCE
    # CRON fields
    cron_expression: str = ""
    timezone: str = "UTC"
    # INTERVAL fields
    interval_seconds: int = 0
    # WEBHOOK fields
    webhook_token: str = ""
    # EVENT fields
    event_channel: str = ""
    event_filter: str = ""
    # ONCE fields
    fire_at_iso: str = ""
    # Condition (all types)
    condition: str = ""
    # Metadata
    description: str = ""
