"""Coverage for the governed scheduled-goal dispatch path in app/scaling/tasks.py:

 - run_scheduled_goal (the Celery task) + _run_scheduled_goal_governed
 - _build_worker_goal_service, _worker_async_redis
 - _dispatch_scheduled_via_dispatcher, _build_scheduled_trigger_spec
 - _build_goal_kwargs_for_alert (direct, unmocked — the alert-trigger fallback
   goal-text branches and the outer exception handler)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── _build_goal_kwargs_for_alert ─────────────────────────────────────────────


class TestBuildGoalKwargsForAlert:
    @pytest.mark.asyncio
    async def test_uses_explicit_goal_text_when_present(self):
        from app.scaling.tasks import _build_goal_kwargs_for_alert

        result = await _build_goal_kwargs_for_alert(
            {"goal_text": "custom goal"}, "alertmanager", {}, None, SimpleNamespace()
        )
        assert result["goal"] == "custom goal"
        assert result["priority"] == "high"

    @pytest.mark.asyncio
    async def test_alertmanager_fallback_text(self):
        from app.scaling.tasks import _build_goal_kwargs_for_alert

        result = await _build_goal_kwargs_for_alert(
            {},
            "alertmanager",
            {"alertname": "HighCPU", "severity": "critical"},
            None,
            SimpleNamespace(),
        )
        assert "HighCPU" in result["goal"]
        assert "critical" in result["goal"]

    @pytest.mark.asyncio
    async def test_datadog_fallback_text(self):
        from app.scaling.tasks import _build_goal_kwargs_for_alert

        result = await _build_goal_kwargs_for_alert(
            {}, "datadog", {"monitor_name": "cpu-mon", "status": "triggered"}, None, SimpleNamespace()
        )
        assert "cpu-mon" in result["goal"]

    @pytest.mark.asyncio
    async def test_pagerduty_fallback_text(self):
        from app.scaling.tasks import _build_goal_kwargs_for_alert

        result = await _build_goal_kwargs_for_alert(
            {}, "pagerduty", {"incident_title": "db-down", "urgency": "high"}, None, SimpleNamespace()
        )
        assert "db-down" in result["goal"]

    @pytest.mark.asyncio
    async def test_generic_trigger_type_fallback_text(self):
        from app.scaling.tasks import _build_goal_kwargs_for_alert

        result = await _build_goal_kwargs_for_alert({}, "db_row_change", {}, None, SimpleNamespace())
        assert "db_row_change" in result["goal"]

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        from app.scaling.tasks import _build_goal_kwargs_for_alert

        class _BadContext(dict):
            def items(self):
                raise RuntimeError("boom")

        result = await _build_goal_kwargs_for_alert(
            {"goal_text": "x"}, "alertmanager", _BadContext(a=1), None, SimpleNamespace()
        )
        assert result is None


# ── _build_worker_goal_service / _worker_async_redis ────────────────────────


class TestBuildWorkerGoalService:
    def test_success_returns_service_and_factory(self):
        from app.scaling.tasks import _build_worker_goal_service

        mock_factory = MagicMock()
        with (
            patch("app.db.session.get_session_factory", return_value=mock_factory),
            patch("app.services.event_store.EventStore", return_value=MagicMock()),
            patch("app.services.goal_service.GoalService", return_value=MagicMock()) as mock_gs,
        ):
            goal_service, db_factory = _build_worker_goal_service()
        assert db_factory is mock_factory
        mock_gs.assert_called_once()
        assert goal_service is mock_gs.return_value

    def test_failure_returns_none_none(self):
        from app.scaling.tasks import _build_worker_goal_service

        with patch("app.db.session.get_session_factory", side_effect=RuntimeError("boom")):
            goal_service, db_factory = _build_worker_goal_service()
        assert goal_service is None
        assert db_factory is None


class TestWorkerAsyncRedis:
    def test_no_redis_url_returns_none(self, monkeypatch):
        from app.scaling.tasks import _worker_async_redis

        monkeypatch.delenv("REDIS_URL", raising=False)
        from app.scaling.celery_app import celery_app

        original_broker_url = celery_app.conf.broker_url
        celery_app.conf.broker_url = ""
        try:
            assert _worker_async_redis() is None
        finally:
            celery_app.conf.broker_url = original_broker_url

    def test_success_returns_client(self, monkeypatch):
        from app.scaling.tasks import _worker_async_redis

        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        mock_client = MagicMock()
        with patch("redis.asyncio.from_url", return_value=mock_client):
            result = _worker_async_redis()
        assert result is mock_client

    def test_error_returns_none(self, monkeypatch):
        from app.scaling.tasks import _worker_async_redis

        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        with patch("redis.asyncio.from_url", side_effect=RuntimeError("boom")):
            assert _worker_async_redis() is None


# ── _build_scheduled_trigger_spec ────────────────────────────────────────────


class TestBuildScheduledTriggerSpec:
    def test_valid_trigger_type(self):
        from app.scaling.tasks import _build_scheduled_trigger_spec

        spec = _build_scheduled_trigger_spec(
            "sched1", {"trigger_type": "cron", "goal_template": "do x"}
        )
        assert spec.goal_template == "do x"
        assert spec.trigger_id == "sched1"

    def test_invalid_trigger_type_falls_back_to_once(self):
        from app.scaling.tasks import _build_scheduled_trigger_spec

        spec = _build_scheduled_trigger_spec(
            "sched2", {"trigger_type": "not_a_real_type", "goal_template": "do y"}
        )
        assert spec.trigger_id == "sched2"


# ── _dispatch_scheduled_via_dispatcher ───────────────────────────────────────


class TestDispatchScheduledViaDispatcher:
    @pytest.mark.asyncio
    async def test_none_when_no_goal_kwargs(self):
        from app.scaling.tasks import _dispatch_scheduled_via_dispatcher

        result = await _dispatch_scheduled_via_dispatcher(
            "sched1", {"tenant_id": "", "goal_template": ""}, fire_instance_id="f1"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_dispatches_via_provided_dispatcher(self):
        from app.scaling.tasks import _dispatch_scheduled_via_dispatcher

        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(return_value=SimpleNamespace(goal_created=True))

        result = await _dispatch_scheduled_via_dispatcher(
            "sched1",
            {"tenant_id": "t1", "goal_template": "do x", "trigger_type": "cron"},
            fire_instance_id="f1",
            dispatcher=mock_dispatcher,
        )
        assert result.goal_created is True
        mock_dispatcher.dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_plan_falls_back_to_free(self):
        from app.scaling.tasks import _dispatch_scheduled_via_dispatcher

        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch = AsyncMock(return_value=SimpleNamespace(goal_created=False))

        result = await _dispatch_scheduled_via_dispatcher(
            "sched1",
            {
                "tenant_id": "t1",
                "goal_template": "do x",
                "tenant_plan": "not_a_real_plan",
            },
            fire_instance_id="f1",
            dispatcher=mock_dispatcher,
        )
        assert result.goal_created is False

    @pytest.mark.asyncio
    async def test_builds_default_dispatcher_when_none_given(self):
        from app.scaling.tasks import _dispatch_scheduled_via_dispatcher

        mock_dispatcher_instance = MagicMock()
        mock_dispatcher_instance.dispatch = AsyncMock(return_value=SimpleNamespace(goal_created=True))

        with patch(
            "app.triggers.dispatcher.TriggerDispatcher", return_value=mock_dispatcher_instance
        ) as mock_cls:
            result = await _dispatch_scheduled_via_dispatcher(
                "sched1",
                {"tenant_id": "t1", "goal_template": "do x"},
                fire_instance_id="f1",
                goal_service=MagicMock(),
                redis=MagicMock(),
                db_factory=MagicMock(),
            )
        mock_cls.assert_called_once()
        assert result.goal_created is True


# ── _run_scheduled_goal_governed ─────────────────────────────────────────────


class TestRunScheduledGoalGoverned:
    @pytest.mark.asyncio
    async def test_success_closes_redis(self):
        from app.scaling.tasks import _run_scheduled_goal_governed

        mock_redis = AsyncMock()
        mock_redis.aclose = AsyncMock(return_value=None)
        mock_event = SimpleNamespace(goal_created=True, goal_id="g1", skip_reason=None)

        with (
            patch(
                "app.scaling.tasks._build_worker_goal_service",
                return_value=(MagicMock(), MagicMock()),
            ),
            patch("app.scaling.tasks._worker_async_redis", return_value=mock_redis),
            patch(
                "app.scaling.tasks._dispatch_scheduled_via_dispatcher",
                new=AsyncMock(return_value=mock_event),
            ),
        ):
            result = await _run_scheduled_goal_governed("s1", "t1", "do x", "", "f1")

        assert result is mock_event
        mock_redis.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_redis_skips_close(self):
        from app.scaling.tasks import _run_scheduled_goal_governed

        mock_event = SimpleNamespace(goal_created=False, goal_id=None, skip_reason="deduped")

        with (
            patch(
                "app.scaling.tasks._build_worker_goal_service",
                return_value=(None, None),
            ),
            patch("app.scaling.tasks._worker_async_redis", return_value=None),
            patch(
                "app.scaling.tasks._dispatch_scheduled_via_dispatcher",
                new=AsyncMock(return_value=mock_event),
            ),
        ):
            result = await _run_scheduled_goal_governed("s1", "t1", "do x", "", "f1")

        assert result is mock_event


# ── run_scheduled_goal (the Celery task) ─────────────────────────────────────


class TestRunScheduledGoalTask:
    def test_dispatched_when_goal_created(self):
        from app.scaling.tasks import run_scheduled_goal

        mock_event = SimpleNamespace(goal_created=True, goal_id="g1", skip_reason=None)
        with patch("app.scaling.tasks._run_async", return_value=mock_event):
            result = run_scheduled_goal.run(
                schedule_id="s1", tenant_id="t1", goal_template="do x"
            )
        assert result == {
            "status": "dispatched",
            "schedule_id": "s1",
            "goal_id": "g1",
            "skip_reason": None,
        }

    def test_skipped_when_goal_not_created(self):
        from app.scaling.tasks import run_scheduled_goal

        mock_event = SimpleNamespace(goal_created=False, goal_id=None, skip_reason="rate_limited")
        with patch("app.scaling.tasks._run_async", return_value=mock_event):
            result = run_scheduled_goal.run(
                schedule_id="s1", tenant_id="t1", goal_template="do x"
            )
        assert result["status"] == "skipped"
        assert result["skip_reason"] == "rate_limited"
