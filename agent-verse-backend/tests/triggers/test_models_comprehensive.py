"""Comprehensive tests for app/triggers/models.py — targets the 0% baseline."""
from __future__ import annotations

import pytest

from app.triggers.models import TriggerSpec, TriggerType


# ── TriggerType enum ──────────────────────────────────────────────────────────

def test_trigger_type_all_values() -> None:
    assert TriggerType.CRON == "cron"
    assert TriggerType.INTERVAL == "interval"
    assert TriggerType.WEBHOOK == "webhook"
    assert TriggerType.EVENT == "event"
    assert TriggerType.REST == "rest"
    assert TriggerType.ONCE == "once"
    assert TriggerType.FILE_DROP == "file_drop"
    assert TriggerType.ALERTMANAGER == "alertmanager"
    assert TriggerType.DATADOG == "datadog"
    assert TriggerType.PAGERDUTY == "pagerduty"


def test_trigger_type_is_str_enum() -> None:
    """TriggerType must inherit from str so it can be compared directly."""
    import enum
    assert issubclass(TriggerType, str)
    assert issubclass(TriggerType, enum.Enum)


def test_trigger_type_from_string() -> None:
    assert TriggerType("cron") == TriggerType.CRON
    assert TriggerType("interval") == TriggerType.INTERVAL
    assert TriggerType("webhook") == TriggerType.WEBHOOK
    assert TriggerType("event") == TriggerType.EVENT
    assert TriggerType("rest") == TriggerType.REST
    assert TriggerType("once") == TriggerType.ONCE
    assert TriggerType("file_drop") == TriggerType.FILE_DROP
    assert TriggerType("alertmanager") == TriggerType.ALERTMANAGER
    assert TriggerType("datadog") == TriggerType.DATADOG
    assert TriggerType("pagerduty") == TriggerType.PAGERDUTY


def test_trigger_type_invalid_raises() -> None:
    with pytest.raises(ValueError):
        TriggerType("notexist")


def test_trigger_type_members_count() -> None:
    assert len(TriggerType) == 10


def test_trigger_type_values_are_lowercase() -> None:
    for t in TriggerType:
        assert t.value == t.value.lower()


# ── TriggerSpec dataclass ─────────────────────────────────────────────────────

def test_trigger_spec_defaults() -> None:
    spec = TriggerSpec()
    assert spec.trigger_type == TriggerType.ONCE
    assert spec.cron_expression == ""
    assert spec.timezone == "UTC"
    assert spec.interval_seconds == 0
    assert spec.webhook_token == ""
    assert spec.event_channel == ""
    assert spec.event_filter == ""
    assert spec.fire_at_iso == ""
    assert spec.condition == ""
    assert spec.description == ""


def test_trigger_spec_cron() -> None:
    spec = TriggerSpec(
        trigger_type=TriggerType.CRON,
        cron_expression="0 9 * * 1-5",
        timezone="America/New_York",
        description="Weekday 9am run",
    )
    assert spec.trigger_type == TriggerType.CRON
    assert spec.cron_expression == "0 9 * * 1-5"
    assert spec.timezone == "America/New_York"
    assert spec.description == "Weekday 9am run"


def test_trigger_spec_interval() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=3600)
    assert spec.trigger_type == TriggerType.INTERVAL
    assert spec.interval_seconds == 3600


def test_trigger_spec_webhook() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK, webhook_token="tok-abc-123")
    assert spec.trigger_type == TriggerType.WEBHOOK
    assert spec.webhook_token == "tok-abc-123"


def test_trigger_spec_event() -> None:
    spec = TriggerSpec(
        trigger_type=TriggerType.EVENT,
        event_channel="deployments",
        event_filter="severity=critical",
    )
    assert spec.trigger_type == TriggerType.EVENT
    assert spec.event_channel == "deployments"
    assert spec.event_filter == "severity=critical"


def test_trigger_spec_once() -> None:
    spec = TriggerSpec(
        trigger_type=TriggerType.ONCE,
        fire_at_iso="2026-07-04T09:00:00Z",
    )
    assert spec.trigger_type == TriggerType.ONCE
    assert spec.fire_at_iso == "2026-07-04T09:00:00Z"


def test_trigger_spec_with_condition() -> None:
    spec = TriggerSpec(
        trigger_type=TriggerType.REST,
        condition="env == 'production'",
    )
    assert spec.condition == "env == 'production'"


def test_trigger_spec_all_special_types() -> None:
    for ttype in (TriggerType.FILE_DROP, TriggerType.ALERTMANAGER, TriggerType.DATADOG, TriggerType.PAGERDUTY):
        spec = TriggerSpec(trigger_type=ttype)
        assert spec.trigger_type == ttype


def test_trigger_spec_is_dataclass() -> None:
    import dataclasses
    assert dataclasses.is_dataclass(TriggerSpec)


def test_trigger_spec_equality() -> None:
    s1 = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 * * * *")
    s2 = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 * * * *")
    assert s1 == s2


def test_trigger_spec_inequality() -> None:
    s1 = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 * * * *")
    s2 = TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=60)
    assert s1 != s2
