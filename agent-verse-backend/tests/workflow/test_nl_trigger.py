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
    with pytest.raises(NLTriggerParseError, match="no LLM provider is configured") as exc:
        await resolver.resolve("when a new Jira ticket is created with priority=critical")
    # The 422 detail tells the user what phrasing works without the LLM.
    assert "every day at midnight" in str(exc.value)


@pytest.mark.asyncio
async def test_fast_path_returns_trigger_definition(resolver: NLTriggerResolver) -> None:
    """Fast path returns a proper TriggerDefinition model."""
    t = await resolver.resolve("every minute")
    assert isinstance(t, TriggerDefinition)
    assert t.type in ("schedule", "webhook", "event", "api", "nl", "file_drop", "alertmanager", "datadog", "pagerduty")


# ── WF-09: non-regex phrases go to the LLM, charged to the tenant ────────────


@pytest.fixture
def _platform() -> object:
    from app.providers import guarded_completion as gc

    saved = gc._platform_services
    yield gc
    gc.set_platform_cost_services(saved)


@pytest.mark.asyncio
async def test_non_regex_phrase_resolves_via_llm_and_is_charged(_platform: object) -> None:
    from types import SimpleNamespace

    from app.providers import guarded_completion as gc
    from app.providers.fake import FakeProvider
    from tests.providers._decision_fakes import RecordingController

    ctrl = RecordingController()
    gc.set_platform_cost_services(lambda: (ctrl, None))
    llm = FakeProvider(responses=['{"type": "cron", "cron": "30 7 * * 2"}'])
    resolver = NLTriggerResolver(llm_provider=llm)

    trigger = await resolver.resolve(
        "on tuesdays at half past seven", tenant_ctx=SimpleNamespace(tenant_id="t-42")
    )

    assert trigger.type == "schedule"
    assert trigger.schedule is not None and trigger.schedule.cron == "30 7 * * 2"
    assert [t for _, t in ctrl.recorded] == ["t-42"]


@pytest.mark.asyncio
async def test_llm_path_refused_when_the_tenant_is_over_budget(_platform: object) -> None:
    from types import SimpleNamespace

    from app.providers import guarded_completion as gc
    from app.providers.fake import FakeProvider
    from tests.providers._decision_fakes import RecordingController

    gc.set_platform_cost_services(lambda: (RecordingController(remaining=False), None))
    resolver = NLTriggerResolver(llm_provider=FakeProvider(responses=['{"type": "manual"}']))
    with pytest.raises(NLTriggerParseError, match="budget"):
        await resolver.resolve("whenever finance asks", tenant_ctx=SimpleNamespace(tenant_id="t"))


def test_preview_route_passes_the_callers_tenant() -> None:
    from typing import Any
    from unittest.mock import AsyncMock

    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from app.tenancy.context import PlanTier, TenantContext
    from app.workflow.router import router

    resolver = AsyncMock()
    resolver.resolve.return_value = TriggerDefinition(type="api")
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = TenantContext(tenant_id="t-7", plan=PlanTier.FREE, api_key_id="k")
        request.app.state.nl_trigger_resolver = resolver
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    resp = TestClient(app).post(
        "/api/v1/workflows/nl-trigger-preview", json={"description": "whenever"}
    )
    assert resp.status_code == 200, resp.text
    assert resolver.resolve.await_args.kwargs["tenant_ctx"].tenant_id == "t-7"
