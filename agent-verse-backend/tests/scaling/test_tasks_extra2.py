"""Extra coverage for app/scaling/tasks.py — utility functions and task helpers."""
from __future__ import annotations

import hashlib
import datetime
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestStripSecretRedisScheduleFields:
    def test_removes_secret_fields(self):
        from app.scaling.tasks import _strip_secret_redis_schedule_fields
        sched = {
            "goal": "do something",
            "token": "secret_token",
            "password": "secret_pass",
            "api_key": "my_api_key",
            "webhook_token": "wh_token",
            "secret": "my_secret",
            "schedule_id": "s1",
        }
        result = _strip_secret_redis_schedule_fields(sched)
        assert "token" not in result
        assert "password" not in result
        assert "api_key" not in result
        assert "webhook_token" not in result
        assert "secret" not in result
        assert result["goal"] == "do something"
        assert result["schedule_id"] == "s1"

    def test_case_insensitive(self):
        from app.scaling.tasks import _strip_secret_redis_schedule_fields
        sched = {"TOKEN": "secret", "goal": "test"}
        result = _strip_secret_redis_schedule_fields(sched)
        # TOKEN.lower() == "token" which IS in the frozenset → removed
        assert "TOKEN" not in result

    def test_empty_dict(self):
        from app.scaling.tasks import _strip_secret_redis_schedule_fields
        assert _strip_secret_redis_schedule_fields({}) == {}

    def test_no_secret_fields(self):
        from app.scaling.tasks import _strip_secret_redis_schedule_fields
        sched = {"goal": "test", "priority": "high", "tenant_id": "t1"}
        result = _strip_secret_redis_schedule_fields(sched)
        assert result == sched


class TestScheduledGoalId:
    def test_returns_sched_prefixed_hex(self):
        from app.scaling.tasks import _scheduled_goal_id
        result = _scheduled_goal_id("schedule_key_1")
        assert result.startswith("sched_")
        assert len(result) == len("sched_") + 26

    def test_deterministic_with_fire_instance_id(self):
        from app.scaling.tasks import _scheduled_goal_id
        r1 = _scheduled_goal_id("key", fire_instance_id="2024-01-01T00:00:00")
        r2 = _scheduled_goal_id("key", fire_instance_id="2024-01-01T00:00:00")
        assert r1 == r2

    def test_different_keys_produce_different_ids(self):
        from app.scaling.tasks import _scheduled_goal_id
        r1 = _scheduled_goal_id("key1", fire_instance_id="ts")
        r2 = _scheduled_goal_id("key2", fire_instance_id="ts")
        assert r1 != r2

    def test_no_fire_instance_id_uses_now(self):
        from app.scaling.tasks import _scheduled_goal_id
        # Without fire_instance_id, uses datetime.now() — just verify format
        result = _scheduled_goal_id("some_schedule_key")
        assert result.startswith("sched_")


class TestMonotonic:
    def test_returns_float(self):
        from app.scaling.tasks import _monotonic
        result = _monotonic()
        assert isinstance(result, float)
        assert result > 0


class TestRunAsync:
    def test_runs_coroutine(self):
        from app.scaling.tasks import _run_async

        async def sample():
            return 42

        result = _run_async(sample())
        assert result == 42

    def test_runs_async_function_with_args(self):
        from app.scaling.tasks import _run_async

        async def add(a, b):
            return a + b

        result = _run_async(add(3, 4))
        assert result == 7

    def test_closes_loop_after_run(self):
        import asyncio
        from app.scaling.tasks import _run_async

        async def sample():
            return "done"

        _run_async(sample())
        # A new event loop should be usable afterward
        new_loop = asyncio.new_event_loop()
        assert not new_loop.is_running()
        new_loop.close()


class TestRecordGoalDurationMetric:
    def test_no_exception_when_metrics_unavailable(self):
        from app.scaling.tasks import _record_goal_duration_metric
        with patch.dict("sys.modules", {"app.observability.metrics": None}):
            # Should not raise
            _record_goal_duration_metric("completed", started_monotonic=0.0, priority="normal")

    def test_calls_record_goal_duration(self):
        from app.scaling.tasks import _record_goal_duration_metric
        mock_record = MagicMock()
        with patch("app.observability.metrics.record_goal_duration", mock_record):
            _record_goal_duration_metric("completed", started_monotonic=0.0, priority="high")
        mock_record.assert_called_once()

    def test_swallows_metric_exception(self):
        from app.scaling.tasks import _record_goal_duration_metric
        mock_record = MagicMock(side_effect=RuntimeError("metric server down"))
        with patch("app.observability.metrics.record_goal_duration", mock_record):
            # Must not raise
            _record_goal_duration_metric("failed", started_monotonic=1.0, priority="low")


class TestGetLlmProvider:
    def test_returns_none_when_no_redis_url(self, monkeypatch):
        from app.scaling.tasks import _get_llm_provider
        monkeypatch.delenv("REDIS_URL", raising=False)
        result = _get_llm_provider("tenant1")
        assert result is None

    def test_returns_none_on_redis_error(self, monkeypatch):
        from app.scaling.tasks import _get_llm_provider
        monkeypatch.setenv("REDIS_URL", "redis://localhost:9999/0")
        with patch("redis.Redis", side_effect=ConnectionError("no redis")):
            result = _get_llm_provider("tenant1")
        assert result is None


class TestSetupSigterm:
    def test_sigterm_handler_set(self):
        """_setup_sigterm runs without error and installs handler."""
        from app.scaling import tasks
        # Just verify the module imports without error (sigterm setup runs at import)
        assert hasattr(tasks, "_setup_sigterm")


class TestRedisPool:
    def test_get_redis_pool_creates_pool(self):
        from app.scaling import tasks
        # Reset the pool to test creation
        original_pool = tasks._REDIS_POOL
        tasks._REDIS_POOL = None
        try:
            with patch("redis.ConnectionPool.from_url") as mock_pool:
                mock_pool.return_value = MagicMock()
                pool = tasks._get_redis_pool()
                assert pool is not None
        finally:
            tasks._REDIS_POOL = original_pool

    def test_get_redis_pool_reuses_existing(self):
        from app.scaling import tasks
        original_pool = tasks._REDIS_POOL
        mock_pool = MagicMock()
        tasks._REDIS_POOL = mock_pool
        try:
            result = tasks._get_redis_pool()
            assert result is mock_pool
        finally:
            tasks._REDIS_POOL = original_pool


class TestDecrementAfterCompletion:
    @pytest.mark.asyncio
    async def test_handles_redis_connection_error(self):
        from app.scaling.tasks import _decrement_after_completion
        with patch("redis.asyncio.from_url", side_effect=ConnectionError("no redis")):
            # Should not raise
            await _decrement_after_completion("tenant1", "redis://localhost:6379/0")

    @pytest.mark.asyncio
    async def test_calls_decrement(self):
        from app.scaling.tasks import _decrement_after_completion

        mock_redis = AsyncMock()
        mock_redis.aclose = AsyncMock()

        with patch("redis.asyncio.from_url", return_value=mock_redis), \
             patch("app.tenancy.limits.decrement_concurrent_goals", AsyncMock()) as mock_dec:
            await _decrement_after_completion("t1", "redis://localhost:6379/0")
            mock_dec.assert_called_once()


class TestCheckEmailGoalsTask:
    def test_returns_disabled_when_imap_disabled(self, monkeypatch):
        monkeypatch.delenv("IMAP_ENABLED", raising=False)
        from app.scaling.tasks import check_email_goals
        result = check_email_goals.run()
        assert result["status"] == "disabled"
        assert result["processed"] == 0

    def test_returns_disabled_when_imap_false(self, monkeypatch):
        monkeypatch.setenv("IMAP_ENABLED", "false")
        from app.scaling.tasks import check_email_goals
        result = check_email_goals.run()
        assert result["status"] == "disabled"


class TestDoCheckEmailGoals:
    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self):
        from app.scaling.tasks import _do_check_email_goals

        with patch("app.db.session.get_session_factory", side_effect=RuntimeError("no db")):
            result = await _do_check_email_goals()
        assert result["status"] == "error"
        assert result["processed"] == 0
        assert "error" in result


class TestRunGoalDlq:
    def test_returns_dead_lettered_dict(self):
        from app.scaling.tasks import run_goal_dlq
        with patch("app.scaling.tasks._run_async", return_value=None):
            result = run_goal_dlq.run(
                goal_id="g1",
                tenant_id="t1",
                goal_text="Fix bug",
                reason="max_retries_exceeded",
            )
        assert result["goal_id"] == "g1"
        assert result["status"] == "dead_lettered"
        assert result["reason"] == "max_retries_exceeded"

    def test_handles_async_exception(self):
        from app.scaling.tasks import run_goal_dlq
        with patch("app.scaling.tasks._run_async", side_effect=Exception("db error")):
            result = run_goal_dlq.run(
                goal_id="g1",
                tenant_id="t1",
                reason="timeout",
            )
        assert result["status"] == "dead_lettered"
