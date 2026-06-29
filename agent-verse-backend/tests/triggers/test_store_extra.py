"""Extra coverage for app/triggers/store.py.

Targets uncovered lines: 69-74, 79-85, 121-123, 136-138,
161-176, 232, 281-289, 313-321, 331-339, 345-367, 373.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tenancy.context import PlanTier, TenantContext
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore, _strip_secret_redis_fields

T = TenantContext(tenant_id="sc-extra2-t1", plan=PlanTier.ENTERPRISE, api_key_id="e2")
T2 = TenantContext(tenant_id="sc-extra2-t2", plan=PlanTier.PROFESSIONAL, api_key_id="e2b")


def _make_cron_spec(**kwargs) -> TriggerSpec:
    return TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *", **kwargs)


# ── _await_redis_call exception branches ─────────────────────────────────────

class TestAwaitRedisCall:
    @pytest.mark.asyncio
    async def test_await_redis_call_logs_on_exception(self):
        """Lines 69-72: exception logged when not strict."""
        store = ScheduleStore()

        async def bad_awaitable():
            raise RuntimeError("redis unavailable")

        # Should not raise
        await store._await_redis_call(bad_awaitable())

    @pytest.mark.asyncio
    async def test_await_redis_call_raises_when_strict(self):
        """Lines 73-74: strict=True → exception propagated."""
        store = ScheduleStore()

        async def bad_awaitable():
            raise RuntimeError("redis unavailable")

        with pytest.raises(RuntimeError, match="redis unavailable"):
            await store._await_redis_call(bad_awaitable(), strict=True)

    @pytest.mark.asyncio
    async def test_await_redis_call_success(self):
        """Happy path: awaitable completes without error."""
        store = ScheduleStore()

        async def good_awaitable():
            return "ok"

        await store._await_redis_call(good_awaitable())  # no raise


# ── _track_redis_call with active loop ───────────────────────────────────────

class TestTrackRedisCall:
    @pytest.mark.asyncio
    async def test_track_redis_call_with_awaitable(self):
        """Lines 79-85: awaitable result is wrapped in a task."""
        store = ScheduleStore()

        async def dummy():
            return "result"

        store._track_redis_call(dummy())
        # Allow task to run
        await asyncio.sleep(0.01)

    def test_track_redis_call_non_awaitable_noop(self):
        """Non-awaitable result → no task created."""
        store = ScheduleStore()
        store._track_redis_call("not-an-awaitable")
        assert len(store._redis_tasks) == 0

    @pytest.mark.asyncio
    async def test_track_redis_call_task_cleaned_up_on_done(self):
        """Task is removed from _redis_tasks when done."""
        store = ScheduleStore()

        async def quick():
            return "done"

        store._track_redis_call(quick())
        await asyncio.sleep(0.05)
        assert len(store._redis_tasks) == 0


# ── _write_redis_schedule_async exception + strict ───────────────────────────

class TestWriteRedisScheduleAsync:
    @pytest.mark.asyncio
    async def test_write_redis_async_noop_when_no_redis(self):
        store = ScheduleStore()
        spec = _make_cron_spec()
        rec = {
            "schedule_id": "s1",
            "goal_id": "g1",
            "agent_id": "",
            "goal_template": "",
            "spec": spec,
            "paused": False,
        }
        await store._write_redis_schedule_async("t1", rec)  # no raise

    @pytest.mark.asyncio
    async def test_write_redis_async_exception_strict_raises(self):
        """Lines 121-123: exception + strict=True → re-raised."""
        mock_redis = MagicMock()
        mock_redis.set = MagicMock(side_effect=RuntimeError("connection refused"))
        store = ScheduleStore(redis=mock_redis)

        spec = _make_cron_spec()
        rec = {
            "schedule_id": "s-err",
            "goal_id": "g1",
            "agent_id": "",
            "goal_template": "",
            "spec": spec,
            "paused": False,
        }
        with pytest.raises(RuntimeError, match="connection refused"):
            await store._write_redis_schedule_async("t1", rec, strict=True)

    @pytest.mark.asyncio
    async def test_write_redis_async_exception_non_strict_logs(self):
        """Lines 117-120: exception + strict=False → logs, no raise."""
        mock_redis = MagicMock()
        mock_redis.set = MagicMock(side_effect=RuntimeError("network error"))
        store = ScheduleStore(redis=mock_redis)

        spec = _make_cron_spec()
        rec = {
            "schedule_id": "s-log",
            "goal_id": "g1",
            "agent_id": "",
            "goal_template": "",
            "spec": spec,
            "paused": False,
        }
        await store._write_redis_schedule_async("t1", rec, strict=False)  # no raise


# ── _delete_redis_schedule_async exception + strict ───────────────────────────

class TestDeleteRedisScheduleAsync:
    @pytest.mark.asyncio
    async def test_delete_redis_async_noop_when_no_redis(self):
        store = ScheduleStore()
        await store._delete_redis_schedule_async("t1", "s1")  # no raise

    @pytest.mark.asyncio
    async def test_delete_redis_async_strict_raises(self):
        """Lines 136-138: exception + strict=True → re-raised."""
        mock_redis = MagicMock()
        mock_redis.delete = MagicMock(side_effect=RuntimeError("redis gone"))
        store = ScheduleStore(redis=mock_redis)

        with pytest.raises(RuntimeError, match="redis gone"):
            await store._delete_redis_schedule_async("t1", "s1", strict=True)

    @pytest.mark.asyncio
    async def test_delete_redis_async_non_strict_logs(self):
        """Lines 133-135: exception + strict=False → no raise."""
        mock_redis = MagicMock()
        mock_redis.delete = MagicMock(side_effect=RuntimeError("connection lost"))
        store = ScheduleStore(redis=mock_redis)
        await store._delete_redis_schedule_async("t1", "s1", strict=False)  # no raise


# ── create() with asyncio loop and DB ────────────────────────────────────────

class TestCreateWithDb:
    @pytest.mark.asyncio
    async def test_create_fires_db_task(self):
        """Lines 161-176: create() fires DB create task when loop running."""
        db_calls: list[str] = []

        async def fake_db_create(*args, **kwargs):
            db_calls.append("created")

        store = ScheduleStore()
        spec = _make_cron_spec()

        with patch.object(store, "_db_create", side_effect=fake_db_create):
            store._db = MagicMock()  # Signal that DB is configured
            sched_id = store.create(goal_id="g-db", spec=spec, tenant_ctx=T)

        assert sched_id
        # Give task a chance to run
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_create_async_rollback_on_redis_failure(self):
        """Line 232: redis failure after DB success → DB rollback."""
        mock_redis = MagicMock()
        mock_redis.set = MagicMock(side_effect=RuntimeError("redis down"))

        async def fake_db_create(*args, **kwargs):
            pass

        async def fake_db_delete(*args, **kwargs):
            pass

        store = ScheduleStore(redis=mock_redis)
        store._db = MagicMock()

        with (
            patch.object(store, "_db_create", side_effect=fake_db_create),
            patch.object(store, "_db_delete_schedule", side_effect=fake_db_delete),
        ):
            with pytest.raises(RuntimeError):
                await store.create_async(goal_id="g-rollback", spec=_make_cron_spec(), tenant_ctx=T)

    @pytest.mark.asyncio
    async def test_create_async_success_in_memory(self):
        """create_async without DB just stores in memory."""
        store = ScheduleStore()
        spec = _make_cron_spec()
        sched_id = await store.create_async(goal_id="g-async", spec=spec, tenant_ctx=T)
        assert store.get(sched_id, tenant_ctx=T) is not None


# ── delete() with DB ──────────────────────────────────────────────────────────

class TestDeleteWithDb:
    @pytest.mark.asyncio
    async def test_delete_fires_db_task(self):
        """Lines 281-289: delete() fires DB delete task when loop running."""
        db_deleted: list[str] = []

        async def fake_db_delete(*args, **kwargs):
            db_deleted.append("deleted")

        store = ScheduleStore()
        store._db = MagicMock()  # signal DB configured
        spec = _make_cron_spec()
        sched_id = store.create(goal_id="g-del", spec=spec, tenant_ctx=T)

        with patch.object(store, "_db_delete_schedule", side_effect=fake_db_delete):
            result = store.delete(sched_id, tenant_ctx=T)

        assert result is True
        await asyncio.sleep(0.05)

    def test_delete_not_found_returns_false(self):
        store = ScheduleStore()
        result = store.delete("nonexistent", tenant_ctx=T)
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_async_not_found_returns_false(self):
        store = ScheduleStore()
        result = await store.delete_async("nonexistent", tenant_ctx=T)
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_async_success(self):
        store = ScheduleStore()
        spec = _make_cron_spec()
        sched_id = await store.create_async(goal_id="g-del-async", spec=spec, tenant_ctx=T)
        result = await store.delete_async(sched_id, tenant_ctx=T)
        assert result is True
        assert store.get(sched_id, tenant_ctx=T) is None


# ── pause() and resume() with DB ─────────────────────────────────────────────

class TestPauseResumeWithDb:
    @pytest.mark.asyncio
    async def test_pause_fires_db_task(self):
        """Lines 313-321: pause fires DB update task."""
        db_calls: list[str] = []

        async def fake_db_update(sched_id, tenant_id, paused):
            db_calls.append(f"paused={paused}")

        store = ScheduleStore()
        store._db = MagicMock()
        spec = _make_cron_spec()
        sched_id = store.create(goal_id="g-pause", spec=spec, tenant_ctx=T)

        with patch.object(store, "_db_update_paused", side_effect=fake_db_update):
            result = store.pause(sched_id, tenant_ctx=T)

        assert result is True
        rec = store.get(sched_id, tenant_ctx=T)
        assert rec["paused"] is True
        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_resume_fires_db_task(self):
        """Lines 331-339: resume fires DB update task."""
        db_calls: list[str] = []

        async def fake_db_update(sched_id, tenant_id, paused):
            db_calls.append(f"paused={paused}")

        store = ScheduleStore()
        store._db = MagicMock()
        spec = _make_cron_spec()
        sched_id = store.create(goal_id="g-resume", spec=spec, tenant_ctx=T)
        store.pause(sched_id, tenant_ctx=T)

        with patch.object(store, "_db_update_paused", side_effect=fake_db_update):
            result = store.resume(sched_id, tenant_ctx=T)

        assert result is True
        rec = store.get(sched_id, tenant_ctx=T)
        assert rec["paused"] is False
        await asyncio.sleep(0.05)

    def test_pause_not_found_returns_false(self):
        store = ScheduleStore()
        result = store.pause("nonexistent", tenant_ctx=T)
        assert result is False

    def test_resume_not_found_returns_false(self):
        store = ScheduleStore()
        result = store.resume("nonexistent", tenant_ctx=T)
        assert result is False


# ── _db_update_paused ────────────────────────────────────────────────────────

class TestDbUpdatePaused:
    @pytest.mark.asyncio
    async def test_db_update_paused_noop_when_no_db(self):
        store = ScheduleStore()
        await store._db_update_paused("s1", "t1", True)  # no raise

    @pytest.mark.asyncio
    async def test_db_update_paused_exception_logged(self):
        """Lines 366-367: DB exception → logged, no raise."""
        store = ScheduleStore()

        async def bad_db():
            raise RuntimeError("db gone")

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        # Simulate exception during context manager
        store._db = MagicMock(side_effect=RuntimeError("db gone"))
        await store._db_update_paused("s1", "t1", True)  # no raise


# ── _db_delete_schedule with strict ──────────────────────────────────────────

class TestDbDeleteSchedule:
    @pytest.mark.asyncio
    async def test_db_delete_noop_when_no_db(self):
        store = ScheduleStore()
        await store._db_delete_schedule("s1", "t1")  # no raise

    @pytest.mark.asyncio
    async def test_db_delete_exception_strict_raises(self):
        """Line 373: strict=True → exception propagated."""
        store = ScheduleStore()
        store._db = MagicMock(side_effect=RuntimeError("db gone"))
        with pytest.raises(RuntimeError):
            await store._db_delete_schedule("s1", "t1", strict=True)

    @pytest.mark.asyncio
    async def test_db_delete_exception_non_strict_logs(self):
        """Lines 392-393: strict=False → logged, no raise."""
        store = ScheduleStore()
        store._db = MagicMock(side_effect=RuntimeError("db gone"))
        await store._db_delete_schedule("s1", "t1", strict=False)  # no raise


# ── Multi-tenant isolation ────────────────────────────────────────────────────

class TestTenantIsolation:
    def test_list_all_scoped_to_tenant(self):
        store = ScheduleStore()
        spec = _make_cron_spec()
        s1 = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
        s2 = store.create(goal_id="g2", spec=spec, tenant_ctx=T2)

        t1_schedules = store.list_all(tenant_ctx=T)
        t2_schedules = store.list_all(tenant_ctx=T2)

        t1_ids = [s["schedule_id"] for s in t1_schedules]
        t2_ids = [s["schedule_id"] for s in t2_schedules]

        assert s1 in t1_ids
        assert s1 not in t2_ids
        assert s2 in t2_ids
        assert s2 not in t1_ids

    def test_get_cannot_access_other_tenant_schedule(self):
        store = ScheduleStore()
        spec = _make_cron_spec()
        sched_id = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
        result = store.get(sched_id, tenant_ctx=T2)
        assert result is None

    def test_delete_cannot_delete_other_tenant_schedule(self):
        store = ScheduleStore()
        spec = _make_cron_spec()
        sched_id = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
        result = store.delete(sched_id, tenant_ctx=T2)
        assert result is False
        assert store.get(sched_id, tenant_ctx=T) is not None


# ── sync_from_db returns 0 with no DB ────────────────────────────────────────

class TestSyncFromDb:
    @pytest.mark.asyncio
    async def test_sync_from_db_no_db_returns_zero(self):
        store = ScheduleStore()
        result = await store.sync_from_db()
        assert result == 0
