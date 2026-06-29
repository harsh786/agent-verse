"""Extra coverage for app/triggers/store.py — supplements test_store_full.py."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.tenancy.context import PlanTier, TenantContext
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore, _strip_secret_redis_fields

T = TenantContext(tenant_id="sc-extra-t1", plan=PlanTier.ENTERPRISE, api_key_id="e1")


# ── _strip_secret_redis_fields ────────────────────────────────────────────────

def test_strip_removes_webhook_token() -> None:
    payload = {"schedule_id": "s1", "webhook_token": "secret123", "trigger_type": "webhook"}
    result = _strip_secret_redis_fields(payload)
    assert "webhook_token" not in result
    assert result["schedule_id"] == "s1"


def test_strip_removes_all_secret_fields() -> None:
    payload = {
        "schedule_id": "s1",
        "token": "t",
        "password": "p",
        "api_key": "k",
        "secret": "s",
        "webhook_token": "w",
        "other": "kept",
    }
    result = _strip_secret_redis_fields(payload)
    for key in ("token", "password", "api_key", "secret", "webhook_token"):
        assert key not in result
    assert result["other"] == "kept"


def test_strip_case_insensitive() -> None:
    payload = {"TOKEN": "val", "WebHook_Token": "tok", "normal": "ok"}
    # _strip checks key.lower()
    result = _strip_secret_redis_fields(payload)
    # TOKEN and WebHook_Token contain secret words in lowercase
    assert "TOKEN" not in result
    assert "WebHook_Token" not in result
    assert result["normal"] == "ok"


# ── _redis_key ────────────────────────────────────────────────────────────────

def test_redis_key_format() -> None:
    key = ScheduleStore._redis_key("tenant-1", "sched-abc")
    assert key == "schedule:tenant-1:sched-abc"


# ── All TriggerType variants ──────────────────────────────────────────────────

def test_create_cron_trigger() -> None:
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *")
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    rec = store.get(sid, tenant_ctx=T)
    assert rec["spec"].trigger_type == TriggerType.CRON


def test_create_interval_trigger() -> None:
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=1800)
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    rec = store.get(sid, tenant_ctx=T)
    assert rec["spec"].interval_seconds == 1800


def test_create_event_trigger() -> None:
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.EVENT, event_channel="deployments")
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    rec = store.get(sid, tenant_ctx=T)
    assert rec["spec"].event_channel == "deployments"


def test_create_rest_trigger() -> None:
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.REST, description="manual trigger")
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    rec = store.get(sid, tenant_ctx=T)
    assert rec["spec"].trigger_type == TriggerType.REST


def test_create_file_drop_trigger() -> None:
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.FILE_DROP)
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    assert store.get(sid, tenant_ctx=T) is not None


def test_create_alertmanager_trigger() -> None:
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.ALERTMANAGER)
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    assert store.get(sid, tenant_ctx=T) is not None


def test_create_datadog_trigger() -> None:
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.DATADOG)
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    assert store.get(sid, tenant_ctx=T) is not None


def test_create_pagerduty_trigger() -> None:
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.PAGERDUTY)
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    assert store.get(sid, tenant_ctx=T) is not None


# ── Redis payload contents ─────────────────────────────────────────────────────

class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> int:
        self.deleted.append(key)
        return int(self.values.pop(key, None) is not None)


def test_redis_payload_has_expected_fields() -> None:
    redis = _FakeRedis()
    store = ScheduleStore(redis=redis)
    spec = TriggerSpec(
        trigger_type=TriggerType.CRON,
        cron_expression="0 8 * * 1-5",
        timezone="Europe/London",
        description="Weekday run",
    )
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T, goal_template="Daily report")
    key = f"schedule:{T.tenant_id}:{sid}"
    assert key in redis.values
    payload = json.loads(redis.values[key])
    assert payload["trigger_type"] == "cron"
    assert payload["cron_expression"] == "0 8 * * 1-5"
    assert payload["timezone"] == "Europe/London"
    assert payload["description"] == "Weekday run"
    assert payload["goal_template"] == "Daily report"
    assert payload["paused"] is False


def test_redis_payload_omits_webhook_token() -> None:
    redis = _FakeRedis()
    store = ScheduleStore(redis=redis)
    spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK, webhook_token="super-secret")
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    key = f"schedule:{T.tenant_id}:{sid}"
    payload_str = redis.values[key]
    assert "super-secret" not in payload_str
    payload = json.loads(payload_str)
    assert "webhook_token" not in payload


def test_redis_deleted_on_schedule_delete() -> None:
    redis = _FakeRedis()
    store = ScheduleStore(redis=redis)
    spec = TriggerSpec(trigger_type=TriggerType.ONCE)
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    key = f"schedule:{T.tenant_id}:{sid}"
    assert key in redis.values
    store.delete(sid, tenant_ctx=T)
    assert key in redis.deleted


# ── Pause/resume with Redis ───────────────────────────────────────────────────

def test_redis_updated_on_pause() -> None:
    redis = _FakeRedis()
    store = ScheduleStore(redis=redis)
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *")
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    key = f"schedule:{T.tenant_id}:{sid}"

    store.pause(sid, tenant_ctx=T)
    payload = json.loads(redis.values[key])
    assert payload["paused"] is True


def test_redis_updated_on_resume() -> None:
    redis = _FakeRedis()
    store = ScheduleStore(redis=redis)
    spec = TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=60)
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    store.pause(sid, tenant_ctx=T)
    store.resume(sid, tenant_ctx=T)
    key = f"schedule:{T.tenant_id}:{sid}"
    assert json.loads(redis.values[key])["paused"] is False


# ── Multiple tenants in same store ────────────────────────────────────────────

def test_multiple_tenants_isolated() -> None:
    T2 = TenantContext(tenant_id="sc-extra-t2", plan=PlanTier.FREE, api_key_id="e2")
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.ONCE)
    s1 = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    s2 = store.create(goal_id="g2", spec=spec, tenant_ctx=T)
    s3 = store.create(goal_id="g3", spec=spec, tenant_ctx=T2)

    assert len(store.list_all(tenant_ctx=T)) == 2
    assert len(store.list_all(tenant_ctx=T2)) == 1
    assert store.get(s1, tenant_ctx=T2) is None
    assert store.get(s3, tenant_ctx=T) is None


# ── create_async in-memory path ───────────────────────────────────────────────

async def test_create_async_no_db_no_redis() -> None:
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 12 * * *")
    sid = await store.create_async(goal_id="g1", spec=spec, tenant_ctx=T)
    assert sid is not None
    assert store.get(sid, tenant_ctx=T) is not None


async def test_delete_async_not_found_returns_false() -> None:
    store = ScheduleStore()
    result = await store.delete_async("does-not-exist", tenant_ctx=T)
    assert result is False


# ── Redis error handling (fire-and-forget) ────────────────────────────────────

def test_redis_call_exception_does_not_propagate() -> None:
    """Even if Redis raises, create should not fail."""
    class BadRedis:
        def set(self, key: str, value: str) -> None:
            raise RuntimeError("Redis connection refused")

        def delete(self, key: str) -> int:
            raise RuntimeError("Redis connection refused")

    store = ScheduleStore(redis=BadRedis())
    spec = TriggerSpec(trigger_type=TriggerType.ONCE)
    # Should not raise
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    assert sid is not None


# ── sync_from_db with invalid trigger type ────────────────────────────────────

async def test_sync_from_db_unknown_trigger_type_defaults_to_once() -> None:
    """Unknown trigger_type in DB row should be loaded as ONCE without error."""
    import contextlib
    from types import SimpleNamespace

    row = SimpleNamespace(
        id="sched-unknown",
        tenant_id=T.tenant_id,
        agent_id="",
        goal_id_template="g1",
        trigger_type="unknown_type_xyz",  # Invalid
        cron_expression="",
        timezone="UTC",
        interval_seconds=0,
        webhook_token="",
        event_channel="",
        fire_at_iso="",
        condition="",
        description="",
        paused=False,
    )

    @contextlib.asynccontextmanager
    async def _session():
        class FakeSession:
            async def execute(self, stmt):
                return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row]))
        yield FakeSession()

    store = ScheduleStore(db_session_factory=_session)
    count = await store.sync_from_db()
    assert count == 1
    rec = store.get("sched-unknown", tenant_ctx=T)
    assert rec is not None
    assert rec["spec"].trigger_type == TriggerType.ONCE
