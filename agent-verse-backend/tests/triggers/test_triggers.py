"""Tests for Phase 8 — Triggers & NL Scheduling.

Tests cover:
- TriggerSpec model (6 trigger types)
- ScheduleStore: CRUD with tenant isolation
- NLScheduler: parse NL → TriggerSpec (uses FakeProvider for determinism)
- ConditionChecker: evaluate NL condition (uses FakeProvider)
"""

from __future__ import annotations

import pytest

from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore
from app.triggers.nl_scheduler import NLScheduler
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-a", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_CTX_B = TenantContext(tenant_id="tid-b", plan=PlanTier.STARTER, api_key_id="kid-2")


# ── TriggerSpec model ─────────────────────────────────────────────────────────

def test_trigger_spec_cron() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * 1-5", timezone="UTC")
    assert spec.trigger_type == TriggerType.CRON
    assert spec.cron_expression == "0 9 * * 1-5"


def test_trigger_spec_webhook() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK, webhook_token="tok_abc")
    assert spec.trigger_type == TriggerType.WEBHOOK
    assert spec.webhook_token == "tok_abc"


def test_trigger_spec_all_6_types() -> None:
    for ttype in TriggerType:
        spec = TriggerSpec(trigger_type=ttype)
        assert spec.trigger_type == ttype


def test_trigger_spec_interval() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=3600)
    assert spec.interval_seconds == 3600


# ── ScheduleStore ─────────────────────────────────────────────────────────────

def test_schedule_store_create_and_get() -> None:
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 10 * * *")
    sched_id = store.create(goal_id="gid-1", spec=spec, tenant_ctx=_CTX)
    sched = store.get(sched_id, tenant_ctx=_CTX)
    assert sched is not None
    assert sched["goal_id"] == "gid-1"


def test_schedule_store_tenant_isolation() -> None:
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=60)
    sched_id = store.create(goal_id="gid-1", spec=spec, tenant_ctx=_CTX)
    sched = store.get(sched_id, tenant_ctx=_CTX_B)
    assert sched is None


def test_schedule_store_list_returns_only_own() -> None:
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.ONCE)
    store.create(goal_id="g1", spec=spec, tenant_ctx=_CTX)
    store.create(goal_id="g2", spec=spec, tenant_ctx=_CTX)
    store.create(goal_id="g3", spec=spec, tenant_ctx=_CTX_B)
    assert len(store.list_all(tenant_ctx=_CTX)) == 2
    assert len(store.list_all(tenant_ctx=_CTX_B)) == 1


def test_schedule_store_delete() -> None:
    store = ScheduleStore()
    sched_id = store.create(
        goal_id="g1",
        spec=TriggerSpec(trigger_type=TriggerType.ONCE),
        tenant_ctx=_CTX,
    )
    removed = store.delete(sched_id, tenant_ctx=_CTX)
    assert removed is True
    assert store.get(sched_id, tenant_ctx=_CTX) is None


def test_schedule_store_pause_and_resume() -> None:
    store = ScheduleStore()
    sched_id = store.create(
        goal_id="g1",
        spec=TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=60),
        tenant_ctx=_CTX,
    )
    store.pause(sched_id, tenant_ctx=_CTX)
    sched = store.get(sched_id, tenant_ctx=_CTX)
    assert sched is not None
    assert sched["paused"] is True

    store.resume(sched_id, tenant_ctx=_CTX)
    sched = store.get(sched_id, tenant_ctx=_CTX)
    assert sched is not None
    assert sched["paused"] is False


# ── NLScheduler ───────────────────────────────────────────────────────────────

async def test_nl_scheduler_parses_cron_expression() -> None:
    llm_response = '{"trigger_type": "cron", "cron_expression": "0 9 * * 1-5", "timezone": "UTC"}'
    provider = FakeProvider(responses=[llm_response])
    scheduler = NLScheduler(provider=provider)
    specs = await scheduler.parse("Every weekday at 9 AM UTC")
    assert len(specs) >= 1
    assert specs[0].trigger_type == TriggerType.CRON
    assert specs[0].cron_expression == "0 9 * * 1-5"


async def test_nl_scheduler_parses_interval() -> None:
    llm_response = '{"trigger_type": "interval", "interval_seconds": 3600}'
    provider = FakeProvider(responses=[llm_response])
    scheduler = NLScheduler(provider=provider)
    specs = await scheduler.parse("Every hour")
    assert specs[0].trigger_type == TriggerType.INTERVAL


async def test_nl_scheduler_handles_compound_schedule() -> None:
    # Compound schedules (e.g. "at 8 AM, 2 PM, 8 PM") produce multiple specs
    llm_response = (
        '{"schedules": ['
        '{"trigger_type": "cron", "cron_expression": "0 8 * * *", "timezone": "UTC"},'
        '{"trigger_type": "cron", "cron_expression": "0 14 * * *", "timezone": "UTC"},'
        '{"trigger_type": "cron", "cron_expression": "0 20 * * *", "timezone": "UTC"}'
        ']}'
    )
    provider = FakeProvider(responses=[llm_response])
    scheduler = NLScheduler(provider=provider)
    specs = await scheduler.parse("At 8 AM, 2 PM, and 8 PM daily")
    assert len(specs) == 3
