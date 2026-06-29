"""Comprehensive tests for app/scaling/tasks.py — targeting 70%+ coverage.

Focuses on testable pure functions and the run_goal_dlq task.
The core run_goal task requires heavy mocking of the Celery/AgentLoop machinery.
"""
from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── _scheduled_goal_id ────────────────────────────────────────────────────────

class TestScheduledGoalId:
    def test_returns_string_with_prefix(self) -> None:
        from app.scaling.tasks import _scheduled_goal_id
        gid = _scheduled_goal_id("schedule_key_1")
        assert gid.startswith("sched_")

    def test_deterministic_with_instance_id(self) -> None:
        from app.scaling.tasks import _scheduled_goal_id
        gid1 = _scheduled_goal_id("key", fire_instance_id="instance-123")
        gid2 = _scheduled_goal_id("key", fire_instance_id="instance-123")
        assert gid1 == gid2

    def test_different_keys_different_ids(self) -> None:
        from app.scaling.tasks import _scheduled_goal_id
        gid1 = _scheduled_goal_id("key_a", fire_instance_id="same")
        gid2 = _scheduled_goal_id("key_b", fire_instance_id="same")
        assert gid1 != gid2

    def test_different_instances_different_ids(self) -> None:
        from app.scaling.tasks import _scheduled_goal_id
        gid1 = _scheduled_goal_id("key", fire_instance_id="instance-1")
        gid2 = _scheduled_goal_id("key", fire_instance_id="instance-2")
        assert gid1 != gid2

    def test_length_26_after_prefix(self) -> None:
        from app.scaling.tasks import _scheduled_goal_id
        gid = _scheduled_goal_id("key", fire_instance_id="x")
        # "sched_" + 26 chars
        assert len(gid) == 6 + 26


# ── _strip_secret_redis_schedule_fields ──────────────────────────────────────

class TestStripSecretFields:
    def test_removes_secret_fields(self) -> None:
        from app.scaling.tasks import _strip_secret_redis_schedule_fields
        sched = {
            "name": "My Schedule",
            "goal": "Deploy",
            "webhook_token": "secret_token",
            "password": "hunter2",
            "api_key": "my_api_key",
        }
        cleaned = _strip_secret_redis_schedule_fields(sched)
        assert "webhook_token" not in cleaned
        assert "password" not in cleaned
        assert "api_key" not in cleaned
        assert cleaned["name"] == "My Schedule"

    def test_preserves_non_secret_fields(self) -> None:
        from app.scaling.tasks import _strip_secret_redis_schedule_fields
        sched = {"name": "Schedule", "goal": "Do task", "cron": "0 * * * *"}
        cleaned = _strip_secret_redis_schedule_fields(sched)
        assert cleaned == sched

    def test_empty_dict(self) -> None:
        from app.scaling.tasks import _strip_secret_redis_schedule_fields
        assert _strip_secret_redis_schedule_fields({}) == {}

    def test_case_insensitive_field_removal(self) -> None:
        from app.scaling.tasks import _strip_secret_redis_schedule_fields
        sched = {"TOKEN": "val1", "Secret": "val2", "name": "ok"}
        cleaned = _strip_secret_redis_schedule_fields(sched)
        assert "TOKEN" not in cleaned
        assert "Secret" not in cleaned
        assert cleaned["name"] == "ok"


# ── _run_async ────────────────────────────────────────────────────────────────

class TestRunAsync:
    def test_runs_simple_coroutine(self) -> None:
        from app.scaling.tasks import _run_async

        async def coro() -> int:
            return 42

        result = _run_async(coro())
        assert result == 42

    def test_runs_async_with_computation(self) -> None:
        from app.scaling.tasks import _run_async

        async def coro() -> str:
            return "hello"

        assert _run_async(coro()) == "hello"

    def test_propagates_exception(self) -> None:
        from app.scaling.tasks import _run_async

        async def bad_coro() -> None:
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            _run_async(bad_coro())


# ── _monotonic ────────────────────────────────────────────────────────────────

class TestMonotonic:
    def test_returns_float(self) -> None:
        from app.scaling.tasks import _monotonic
        result = _monotonic()
        assert isinstance(result, float)
        assert result > 0

    def test_monotonically_increasing(self) -> None:
        from app.scaling.tasks import _monotonic
        t1 = _monotonic()
        t2 = _monotonic()
        assert t2 >= t1


# ── _record_goal_duration_metric ─────────────────────────────────────────────

class TestRecordGoalDurationMetric:
    def test_records_metric(self) -> None:
        from app.scaling.tasks import _record_goal_duration_metric
        started = time.monotonic() - 1.5
        with patch("app.observability.metrics.record_goal_duration") as mock_metric:
            _record_goal_duration_metric("complete", started_monotonic=started, priority="normal")
        mock_metric.assert_called_once()
        args = mock_metric.call_args[0]
        assert args[0] == "complete"

    def test_suppresses_metric_error(self) -> None:
        from app.scaling.tasks import _record_goal_duration_metric
        started = time.monotonic() - 1.0
        with patch("app.observability.metrics.record_goal_duration", side_effect=Exception("err")):
            _record_goal_duration_metric("complete", started_monotonic=started, priority="high")


# ── run_goal_dlq ──────────────────────────────────────────────────────────────

class TestRunGoalDlq:
    def test_returns_dead_lettered_status(self) -> None:
        from app.scaling.tasks import run_goal_dlq

        with patch("app.scaling.tasks._run_async") as mock_run_async:
            mock_run_async.return_value = None
            result = run_goal_dlq.run(
                goal_id="g1",
                tenant_id="t1",
                goal_text="Deploy",
                reason="Max retries exceeded",
            )

        assert result["goal_id"] == "g1"
        assert result["status"] == "dead_lettered"
        assert result["reason"] == "Max retries exceeded"
        assert result["tenant_id"] == "t1"

    def test_dlq_suppresses_db_error(self) -> None:
        from app.scaling.tasks import run_goal_dlq

        with patch("app.scaling.tasks._run_async", side_effect=Exception("DB error")):
            result = run_goal_dlq.run(
                goal_id="g2",
                tenant_id="t1",
                goal_text="",
                reason="error",
            )

        assert result["status"] == "dead_lettered"


# ── _get_sync_redis / _get_redis_pool ────────────────────────────────────────

class TestGetSyncRedis:
    def test_get_redis_pool_returns_pool(self) -> None:
        from app.scaling.tasks import _get_redis_pool
        import redis as sync_redis

        with patch("redis.ConnectionPool.from_url") as mock_pool:
            mock_pool.return_value = MagicMock()
            import app.scaling.tasks as tasks_mod
            tasks_mod._REDIS_POOL = None  # reset
            pool = _get_redis_pool()
        assert pool is not None

    def test_get_sync_redis_returns_redis_client(self) -> None:
        from app.scaling.tasks import _get_sync_redis

        mock_pool = MagicMock()
        mock_client = MagicMock()

        with patch("app.scaling.tasks._get_redis_pool", return_value=mock_pool):
            with patch("redis.Redis", return_value=mock_client) as mock_redis_cls:
                client = _get_sync_redis()

        assert client == mock_client

    def test_get_redis_pool_caches_pool(self) -> None:
        from app.scaling.tasks import _get_redis_pool
        import app.scaling.tasks as tasks_mod

        tasks_mod._REDIS_POOL = None  # reset

        with patch("redis.ConnectionPool.from_url") as mock_pool:
            mock_pool.return_value = MagicMock(name="pool1")
            p1 = _get_redis_pool()
            p2 = _get_redis_pool()  # second call should not re-create

        assert p1 is p2
        tasks_mod._REDIS_POOL = None  # cleanup


# ── _get_llm_provider ─────────────────────────────────────────────────────────

class TestGetLlmProvider:
    def test_returns_none_when_no_redis_url(self) -> None:
        from app.scaling.tasks import _get_llm_provider
        with patch.dict("os.environ", {"REDIS_URL": ""}):
            result = _get_llm_provider("tenant-1")
        assert result is None

    def test_returns_none_when_no_config(self) -> None:
        from app.scaling.tasks import _get_llm_provider
        mock_redis = MagicMock()
        mock_redis.get = MagicMock(return_value=None)

        with patch.dict("os.environ", {"REDIS_URL": "redis://localhost:6379"}):
            with patch("redis.from_url", return_value=mock_redis):
                result = _get_llm_provider("tenant-1")

        assert result is None

    def test_returns_none_on_redis_error(self) -> None:
        from app.scaling.tasks import _get_llm_provider
        with patch.dict("os.environ", {"REDIS_URL": "redis://localhost:6379"}):
            with patch("redis.from_url", side_effect=Exception("Redis down")):
                result = _get_llm_provider("tenant-1")
        assert result is None


# ── _run_with_signals ─────────────────────────────────────────────────────────
# NOTE: _run_with_signals is complex to unit-test in isolation due to asyncio
# task scheduling + sleep loops. Covered by integration tests instead.


# ── _CELERY_QUEUE_NAMES constant ──────────────────────────────────────────────

class TestCeleryConstants:
    def test_queue_names_defined(self) -> None:
        from app.scaling.tasks import _CELERY_QUEUE_NAMES
        assert "goals" in _CELERY_QUEUE_NAMES
        assert "schedules" in _CELERY_QUEUE_NAMES
        assert "maintenance" in _CELERY_QUEUE_NAMES

    def test_secret_fields_defined(self) -> None:
        from app.scaling.tasks import _SECRET_REDIS_SCHEDULE_FIELDS
        assert "webhook_token" in _SECRET_REDIS_SCHEDULE_FIELDS
        assert "password" in _SECRET_REDIS_SCHEDULE_FIELDS
        assert "api_key" in _SECRET_REDIS_SCHEDULE_FIELDS
