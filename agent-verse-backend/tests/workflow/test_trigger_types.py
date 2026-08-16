"""Tests for NLTriggerResolver and extra trigger type handling."""
from __future__ import annotations

import pytest

from app.workflow.dsl import TriggerDefinition
from app.workflow.nl_trigger import NLTriggerParseError, NLTriggerResolver


@pytest.fixture
def resolver() -> NLTriggerResolver:
    return NLTriggerResolver()


# ── Schedule trigger patterns ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nl_every_minute_schedule(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("run every minute")
    assert t.type == "schedule"


@pytest.mark.asyncio
async def test_nl_weekday_schedule(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("every weekday")
    assert t.type == "schedule"


@pytest.mark.asyncio
async def test_nl_monthly_schedule(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("monthly digest")
    assert t.type == "schedule"


@pytest.mark.asyncio
async def test_nl_friday_schedule(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("every Friday")
    assert t.type == "schedule"


# ── Webhook trigger patterns ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nl_webhook_trigger(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("when a webhook is received")
    assert t.type == "webhook"


@pytest.mark.asyncio
async def test_nl_api_trigger(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("trigger via API call")
    assert t.type == "webhook"


@pytest.mark.asyncio
async def test_nl_post_trigger(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("on POST request")
    assert t.type == "webhook"


# ── Error cases ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nl_empty_raises(resolver: NLTriggerResolver) -> None:
    with pytest.raises(NLTriggerParseError):
        await resolver.resolve("")


@pytest.mark.asyncio
async def test_nl_whitespace_raises(resolver: NLTriggerResolver) -> None:
    with pytest.raises(NLTriggerParseError):
        await resolver.resolve("   ")


@pytest.mark.asyncio
async def test_nl_unknown_no_llm_raises(resolver: NLTriggerResolver) -> None:
    with pytest.raises(NLTriggerParseError, match="No LLM"):
        await resolver.resolve("when a Slack DM arrives")


# ── TriggerDefinition validation ──────────────────────────────────────────────


def test_trigger_type_schedule_valid() -> None:
    t = TriggerDefinition(type="schedule")
    assert t.type == "schedule"


def test_trigger_type_webhook_valid() -> None:
    t = TriggerDefinition(type="webhook")
    assert t.type == "webhook"


def test_trigger_type_event_valid() -> None:
    t = TriggerDefinition(type="event")
    assert t.type == "event"


def test_trigger_type_file_drop_valid() -> None:
    t = TriggerDefinition(type="file_drop")
    assert t.type == "file_drop"


def test_trigger_type_alertmanager_valid() -> None:
    t = TriggerDefinition(type="alertmanager")
    assert t.type == "alertmanager"


def test_trigger_type_pagerduty_valid() -> None:
    t = TriggerDefinition(type="pagerduty")
    assert t.type == "pagerduty"


def test_trigger_default_type_is_api() -> None:
    t = TriggerDefinition()
    assert t.type == "api"
