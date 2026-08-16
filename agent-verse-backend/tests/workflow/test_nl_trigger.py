"""Tests for NLTriggerResolver — fast-path regex only (no LLM needed)."""
from __future__ import annotations

import pytest

from app.workflow.dsl import TriggerDefinition
from app.workflow.nl_trigger import NLTriggerParseError, NLTriggerResolver


@pytest.fixture
def resolver() -> NLTriggerResolver:
    """Resolver with no LLM or Redis (tests fast-path only)."""
    return NLTriggerResolver(llm_provider=None, redis_client=None)


@pytest.mark.asyncio
async def test_empty_description_raises(resolver: NLTriggerResolver) -> None:
    with pytest.raises(NLTriggerParseError):
        await resolver.resolve("   ")


@pytest.mark.asyncio
async def test_every_minute_cron(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("every minute")
    assert t.type == "schedule"


@pytest.mark.asyncio
async def test_every_hour_cron(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("every hour")
    assert t.type == "schedule"


@pytest.mark.asyncio
async def test_daily_midnight_cron(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("daily at midnight")
    assert t.type == "schedule"


@pytest.mark.asyncio
async def test_every_day_cron(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("run every day")
    assert t.type == "schedule"


@pytest.mark.asyncio
async def test_weekly_cron(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("weekly report")
    assert t.type == "schedule"


@pytest.mark.asyncio
async def test_monthly_cron(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("run monthly")
    assert t.type == "schedule"


@pytest.mark.asyncio
async def test_webhook_keyword(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("trigger via webhook")
    assert t.type == "webhook"


@pytest.mark.asyncio
async def test_http_keyword(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("listen on http endpoint")
    assert t.type == "webhook"


@pytest.mark.asyncio
async def test_every_monday(resolver: NLTriggerResolver) -> None:
    t = await resolver.resolve("send report every Monday")
    assert t.type == "schedule"


@pytest.mark.asyncio
async def test_no_llm_raises_for_unknown(resolver: NLTriggerResolver) -> None:
    """Without LLM, unrecognized descriptions raise NLTriggerParseError."""
    with pytest.raises(NLTriggerParseError, match="No LLM"):
        await resolver.resolve("when a new Jira ticket is created with priority=critical")


@pytest.mark.asyncio
async def test_fast_path_returns_trigger_definition(resolver: NLTriggerResolver) -> None:
    """Fast path returns a proper TriggerDefinition model."""
    t = await resolver.resolve("every minute")
    assert isinstance(t, TriggerDefinition)
    assert t.type in ("schedule", "webhook", "event", "api", "nl", "file_drop", "alertmanager", "datadog", "pagerduty")
