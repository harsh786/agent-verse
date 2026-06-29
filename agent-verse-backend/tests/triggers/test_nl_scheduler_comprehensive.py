"""Comprehensive tests for app/triggers/nl_scheduler.py — targets the 48% baseline."""
from __future__ import annotations

import json

import pytest

from app.providers.fake import FakeProvider
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.nl_scheduler import NLScheduler, _parse_single


# ── _parse_single helper ──────────────────────────────────────────────────────

def test_parse_single_cron() -> None:
    obj = {"trigger_type": "cron", "cron_expression": "0 9 * * 1-5", "timezone": "UTC"}
    spec = _parse_single(obj)
    assert spec.trigger_type == TriggerType.CRON
    assert spec.cron_expression == "0 9 * * 1-5"
    assert spec.timezone == "UTC"


def test_parse_single_interval() -> None:
    obj = {"trigger_type": "interval", "interval_seconds": 1800}
    spec = _parse_single(obj)
    assert spec.trigger_type == TriggerType.INTERVAL
    assert spec.interval_seconds == 1800


def test_parse_single_once_with_fire_at() -> None:
    obj = {"trigger_type": "once", "fire_at_iso": "2026-12-31T23:59:00Z"}
    spec = _parse_single(obj)
    assert spec.trigger_type == TriggerType.ONCE
    assert spec.fire_at_iso == "2026-12-31T23:59:00Z"


def test_parse_single_with_condition() -> None:
    obj = {
        "trigger_type": "cron",
        "cron_expression": "0 0 * * *",
        "condition": "env == 'prod'",
        "description": "Midnight cron",
    }
    spec = _parse_single(obj)
    assert spec.condition == "env == 'prod'"
    assert spec.description == "Midnight cron"


def test_parse_single_defaults_to_once_on_unknown_type() -> None:
    """Unknown trigger_type string should raise ValueError from TriggerType."""
    with pytest.raises(ValueError):
        _parse_single({"trigger_type": "unknown_type"})


def test_parse_single_empty_object_defaults_to_once() -> None:
    spec = _parse_single({})
    assert spec.trigger_type == TriggerType.ONCE


# ── NLScheduler.parse — single schedules ─────────────────────────────────────

async def test_parse_weekday_cron() -> None:
    resp = json.dumps({"trigger_type": "cron", "cron_expression": "0 9 * * 1-5", "timezone": "UTC"})
    provider = FakeProvider(responses=[resp])
    scheduler = NLScheduler(provider=provider)

    specs = await scheduler.parse("Every weekday at 9am UTC")
    assert len(specs) == 1
    assert specs[0].trigger_type == TriggerType.CRON
    assert specs[0].cron_expression == "0 9 * * 1-5"


async def test_parse_hourly_interval() -> None:
    resp = json.dumps({"trigger_type": "interval", "interval_seconds": 3600})
    provider = FakeProvider(responses=[resp])
    scheduler = NLScheduler(provider=provider)

    specs = await scheduler.parse("Every hour")
    assert len(specs) == 1
    assert specs[0].trigger_type == TriggerType.INTERVAL
    assert specs[0].interval_seconds == 3600


async def test_parse_once() -> None:
    resp = json.dumps({"trigger_type": "once", "fire_at_iso": "2026-07-01T08:00:00Z"})
    provider = FakeProvider(responses=[resp])
    scheduler = NLScheduler(provider=provider)

    specs = await scheduler.parse("At 8am on July 1st 2026")
    assert len(specs) == 1
    assert specs[0].trigger_type == TriggerType.ONCE
    assert specs[0].fire_at_iso == "2026-07-01T08:00:00Z"


async def test_parse_compound_schedules() -> None:
    resp = json.dumps({
        "schedules": [
            {"trigger_type": "cron", "cron_expression": "0 8 * * *", "timezone": "UTC"},
            {"trigger_type": "cron", "cron_expression": "0 14 * * *", "timezone": "UTC"},
            {"trigger_type": "cron", "cron_expression": "0 20 * * *", "timezone": "UTC"},
        ]
    })
    provider = FakeProvider(responses=[resp])
    scheduler = NLScheduler(provider=provider)

    specs = await scheduler.parse("At 8 AM, 2 PM, 8 PM daily")
    assert len(specs) == 3
    assert all(s.trigger_type == TriggerType.CRON for s in specs)
    expressions = [s.cron_expression for s in specs]
    assert "0 8 * * *" in expressions
    assert "0 14 * * *" in expressions
    assert "0 20 * * *" in expressions


async def test_parse_invalid_json_fallback() -> None:
    """Invalid JSON response should fall back to a ONCE TriggerSpec."""
    provider = FakeProvider(responses=["Not JSON at all!!!"])
    scheduler = NLScheduler(provider=provider)

    specs = await scheduler.parse("something unclear")
    assert len(specs) == 1
    assert specs[0].trigger_type == TriggerType.ONCE
    assert specs[0].description == "something unclear"


async def test_parse_markdown_code_block_stripped() -> None:
    """LLM sometimes wraps JSON in markdown code blocks."""
    raw = json.dumps({"trigger_type": "interval", "interval_seconds": 60})
    resp = f"```json\n{raw}\n```"
    provider = FakeProvider(responses=[resp])
    scheduler = NLScheduler(provider=provider)

    specs = await scheduler.parse("Every minute")
    assert len(specs) == 1
    assert specs[0].trigger_type == TriggerType.INTERVAL
    assert specs[0].interval_seconds == 60


async def test_parse_empty_schedules_list() -> None:
    """Empty schedules array should produce empty list."""
    resp = json.dumps({"schedules": []})
    provider = FakeProvider(responses=[resp])
    scheduler = NLScheduler(provider=provider)

    specs = await scheduler.parse("compound with nothing")
    assert specs == []


async def test_parse_with_timezone() -> None:
    resp = json.dumps({
        "trigger_type": "cron",
        "cron_expression": "30 6 * * 1-5",
        "timezone": "America/Los_Angeles",
    })
    provider = FakeProvider(responses=[resp])
    scheduler = NLScheduler(provider=provider)

    specs = await scheduler.parse("Every weekday at 6:30 AM Pacific")
    assert specs[0].timezone == "America/Los_Angeles"


async def test_scheduler_calls_provider_once() -> None:
    """Provider.complete should be called exactly once per parse()."""
    resp = json.dumps({"trigger_type": "once"})
    provider = FakeProvider(responses=[resp])
    scheduler = NLScheduler(provider=provider)

    await scheduler.parse("fire once")
    assert len(provider.call_history) == 1


async def test_scheduler_passes_description_to_provider() -> None:
    """The parse description must appear in the LLM request messages."""
    resp = json.dumps({"trigger_type": "once"})
    provider = FakeProvider(responses=[resp])
    scheduler = NLScheduler(provider=provider)

    description = "every Monday at noon"
    await scheduler.parse(description)

    request = provider.call_history[0]
    messages_content = " ".join(m.content for m in request.messages)
    assert description in messages_content
