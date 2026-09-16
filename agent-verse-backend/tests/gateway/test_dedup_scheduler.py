"""Tests for app/gateway/dedup_scheduler.py — CommandDeduplicator + CommandScheduler.

Covers:
  - CommandDeduplicator._make_key (deterministic / distinguishing)
  - check_and_reserve — in-memory fallback and Redis-backed paths
  - _expire_key cleanup
  - ScheduledCommand defaults
  - CommandScheduler schedule / cancel / list_pending / get_due / mark_executed (incl. repeat)
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.gateway.dedup_scheduler import (
    CommandDeduplicator,
    CommandScheduler,
    ScheduledCommand,
)


def _make_dedup() -> CommandDeduplicator:
    """A memory-backed deduplicator with TTL zeroed so the fire-and-forget
    expiry task (asyncio.sleep(TTL_SECONDS)) completes near-instantly instead
    of parking a 300s-sleeping task for the rest of the test session."""
    dedup = CommandDeduplicator(redis_client=None)
    dedup.TTL_SECONDS = 0
    return dedup


# ── _make_key ────────────────────────────────────────────────────────────


class TestMakeKey:
    def test_key_has_prefix(self) -> None:
        dedup = CommandDeduplicator()
        key = dedup._make_key("t1", "o1", "rest", "u1", "hello")
        assert key.startswith("cmd_dedup:")

    def test_same_inputs_same_bucket_same_key(self) -> None:
        dedup = CommandDeduplicator()
        k1 = dedup._make_key("t1", "o1", "rest", "u1", "hello")
        k2 = dedup._make_key("t1", "o1", "rest", "u1", "hello")
        assert k1 == k2

    def test_different_text_different_key(self) -> None:
        dedup = CommandDeduplicator()
        k1 = dedup._make_key("t1", "o1", "rest", "u1", "hello")
        k2 = dedup._make_key("t1", "o1", "rest", "u1", "goodbye")
        assert k1 != k2

    def test_different_actor_different_key(self) -> None:
        dedup = CommandDeduplicator()
        k1 = dedup._make_key("t1", "o1", "rest", "u1", "hello")
        k2 = dedup._make_key("t1", "o1", "rest", "u2", "hello")
        assert k1 != k2


# ── check_and_reserve — in-memory ────────────────────────────────────────


class TestCheckAndReserveMemory:
    @pytest.mark.asyncio
    async def test_first_call_is_new(self) -> None:
        dedup = _make_dedup()
        result = await dedup.check_and_reserve("cmd-1", "t1", "o1", "rest", "u1", "hello world")
        assert result is True
        await asyncio.sleep(0)  # let the fire-and-forget expiry task run to completion

    @pytest.mark.asyncio
    async def test_duplicate_call_is_not_new(self) -> None:
        dedup = _make_dedup()
        first = await dedup.check_and_reserve("cmd-1", "t1", "o1", "rest", "u1", "hello world")
        second = await dedup.check_and_reserve("cmd-2", "t1", "o1", "rest", "u1", "hello world")
        assert first is True
        assert second is False
        await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_different_actor_is_new(self) -> None:
        dedup = _make_dedup()
        await dedup.check_and_reserve("cmd-1", "t1", "o1", "rest", "u1", "hello world")
        result = await dedup.check_and_reserve("cmd-2", "t1", "o1", "rest", "u2", "hello world")
        assert result is True
        await asyncio.sleep(0)


# ── check_and_reserve — Redis-backed ──────────────────────────────────────


class TestCheckAndReserveRedis:
    @pytest.mark.asyncio
    async def test_redis_new_key(self) -> None:
        redis_mock = AsyncMock()
        redis_mock.set = AsyncMock(return_value=True)
        dedup = CommandDeduplicator(redis_client=redis_mock)
        result = await dedup.check_and_reserve("cmd-1", "t1", "o1", "rest", "u1", "hi")
        assert result is True
        redis_mock.set.assert_awaited_once()
        _, kwargs = redis_mock.set.call_args
        assert kwargs["nx"] is True
        assert kwargs["ex"] == CommandDeduplicator.TTL_SECONDS

    @pytest.mark.asyncio
    async def test_redis_duplicate_key(self) -> None:
        redis_mock = AsyncMock()
        redis_mock.set = AsyncMock(return_value=None)  # NX set failed => already exists
        dedup = CommandDeduplicator(redis_client=redis_mock)
        result = await dedup.check_and_reserve("cmd-1", "t1", "o1", "rest", "u1", "hi")
        assert result is False


# ── _expire_key ────────────────────────────────────────────────────────────


class TestExpireKey:
    @pytest.mark.asyncio
    async def test_expire_key_removes_from_memory(self) -> None:
        dedup = CommandDeduplicator(redis_client=None)
        dedup.TTL_SECONDS = 0
        dedup._memory["some-key"] = "cmd-1"
        await dedup._expire_key("some-key")
        assert "some-key" not in dedup._memory

    @pytest.mark.asyncio
    async def test_expire_key_missing_key_noop(self) -> None:
        dedup = CommandDeduplicator(redis_client=None)
        dedup.TTL_SECONDS = 0
        await dedup._expire_key("never-existed")  # should not raise


# ── ScheduledCommand ────────────────────────────────────────────────────────


class TestScheduledCommand:
    def test_created_at_auto_set(self) -> None:
        cmd = ScheduledCommand(
            scheduled_id="s1",
            org_id="o1",
            tenant_id="t1",
            command_text="do x",
            execute_at=datetime.now(UTC),
        )
        assert cmd.created_at is not None
        assert cmd.status == "pending"

    def test_explicit_created_at_preserved(self) -> None:
        ts = datetime(2024, 1, 1, tzinfo=UTC)
        cmd = ScheduledCommand(
            scheduled_id="s1",
            org_id="o1",
            tenant_id="t1",
            command_text="do x",
            execute_at=datetime.now(UTC),
            created_at=ts,
        )
        assert cmd.created_at == ts


# ── CommandScheduler ─────────────────────────────────────────────────────


class TestCommandScheduler:
    @pytest.mark.asyncio
    async def test_schedule_stores_command(self) -> None:
        scheduler = CommandScheduler()
        cmd = ScheduledCommand(
            scheduled_id="s1",
            org_id="o1",
            tenant_id="t1",
            command_text="ping",
            execute_at=datetime.now(UTC) + timedelta(hours=1),
        )
        result_id = await scheduler.schedule(cmd)
        assert result_id == "s1"
        assert scheduler._scheduled["s1"] is cmd

    @pytest.mark.asyncio
    async def test_cancel_existing(self) -> None:
        scheduler = CommandScheduler()
        cmd = ScheduledCommand(
            scheduled_id="s1",
            org_id="o1",
            tenant_id="t1",
            command_text="ping",
            execute_at=datetime.now(UTC),
        )
        await scheduler.schedule(cmd)
        result = await scheduler.cancel("s1")
        assert result is True
        assert cmd.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_missing_returns_false(self) -> None:
        scheduler = CommandScheduler()
        result = await scheduler.cancel("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_list_pending_filters_by_org_and_status(self) -> None:
        scheduler = CommandScheduler()
        cmd1 = ScheduledCommand(
            scheduled_id="s1",
            org_id="org-a",
            tenant_id="t1",
            command_text="x",
            execute_at=datetime.now(UTC),
        )
        cmd2 = ScheduledCommand(
            scheduled_id="s2",
            org_id="org-b",
            tenant_id="t1",
            command_text="y",
            execute_at=datetime.now(UTC),
        )
        await scheduler.schedule(cmd1)
        await scheduler.schedule(cmd2)
        pending = await scheduler.list_pending("org-a")
        assert [c.scheduled_id for c in pending] == ["s1"]

    @pytest.mark.asyncio
    async def test_list_pending_excludes_non_pending_status(self) -> None:
        scheduler = CommandScheduler()
        cmd = ScheduledCommand(
            scheduled_id="s1",
            org_id="org-a",
            tenant_id="t1",
            command_text="x",
            execute_at=datetime.now(UTC),
            status="executed",
        )
        await scheduler.schedule(cmd)
        pending = await scheduler.list_pending("org-a")
        assert pending == []

    @pytest.mark.asyncio
    async def test_get_due_returns_past_due_commands(self) -> None:
        scheduler = CommandScheduler()
        past = ScheduledCommand(
            scheduled_id="s1",
            org_id="o1",
            tenant_id="t1",
            command_text="x",
            execute_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        future = ScheduledCommand(
            scheduled_id="s2",
            org_id="o1",
            tenant_id="t1",
            command_text="y",
            execute_at=datetime.now(UTC) + timedelta(hours=1),
        )
        await scheduler.schedule(past)
        await scheduler.schedule(future)
        due = await scheduler.get_due()
        assert [c.scheduled_id for c in due] == ["s1"]

    @pytest.mark.asyncio
    async def test_mark_executed_non_repeating(self) -> None:
        scheduler = CommandScheduler()
        cmd = ScheduledCommand(
            scheduled_id="s1",
            org_id="o1",
            tenant_id="t1",
            command_text="x",
            execute_at=datetime.now(UTC),
            repeat=None,
        )
        await scheduler.schedule(cmd)
        await scheduler.mark_executed("s1")
        assert cmd.status == "executed"

    @pytest.mark.asyncio
    async def test_mark_executed_repeating_reschedules(self) -> None:
        scheduler = CommandScheduler()
        original_time = datetime.now(UTC)
        cmd = ScheduledCommand(
            scheduled_id="s1",
            org_id="o1",
            tenant_id="t1",
            command_text="x",
            execute_at=original_time,
            repeat="daily",
        )
        await scheduler.schedule(cmd)
        await scheduler.mark_executed("s1")
        assert cmd.execute_at == original_time + timedelta(days=1)
        assert cmd.status == "pending"

    @pytest.mark.asyncio
    async def test_mark_executed_missing_id_noop(self) -> None:
        scheduler = CommandScheduler()
        await scheduler.mark_executed("nonexistent")  # should not raise

    @pytest.mark.asyncio
    async def test_mark_executed_weekly_and_monthly(self) -> None:
        scheduler = CommandScheduler()
        base = datetime.now(UTC)
        weekly = ScheduledCommand(
            scheduled_id="w1",
            org_id="o1",
            tenant_id="t1",
            command_text="x",
            execute_at=base,
            repeat="weekly",
        )
        monthly = ScheduledCommand(
            scheduled_id="m1",
            org_id="o1",
            tenant_id="t1",
            command_text="x",
            execute_at=base,
            repeat="monthly",
        )
        await scheduler.schedule(weekly)
        await scheduler.schedule(monthly)
        await scheduler.mark_executed("w1")
        await scheduler.mark_executed("m1")
        assert weekly.execute_at == base + timedelta(weeks=1)
        assert monthly.execute_at == base + timedelta(days=30)
