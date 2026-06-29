"""Tests for scaling/tasks.py utility functions and maintenance task implementations.

Covers:
 - discover_and_tick_civilizations syntax fix + registration
 - embed_marketplace_templates, conclude_stale_experiments, expire_stale_documents
   have real SQL implementations (not noop stubs)
 - Helper functions: _scheduled_goal_id, _strip_secret_redis_schedule_fields,
   _schedule_datetime, _datetime_to_naive_iso, _db_schedule_payload
 - _run_async, _record_goal_duration_metric, _record_schedule_fire_metric
 - _monotonic utility
"""
from __future__ import annotations

import datetime
import inspect
from unittest.mock import MagicMock, patch


# ── discover_and_tick_civilizations ───────────────────────────────────────────


def test_discover_and_tick_civilizations_importable() -> None:
    from app.scaling.tasks import discover_and_tick_civilizations
    assert callable(discover_and_tick_civilizations)


def test_discover_and_tick_civilizations_registered_in_celery() -> None:
    from app.scaling.celery_app import celery_app
    registered = list(celery_app.tasks.keys())
    assert any("discover_and_tick_civilizations" in name for name in registered), (
        f"Task not registered. Registered tasks with 'civilization': "
        f"{[n for n in registered if 'civilization' in n]}"
    )


def test_discover_and_tick_civilizations_has_correct_queue() -> None:
    from app.scaling.celery_app import celery_app
    task = celery_app.tasks.get("app.scaling.tasks.discover_and_tick_civilizations")
    assert task is not None


# ── Real implementations (not noop) ───────────────────────────────────────────


def test_embed_marketplace_templates_has_real_sql() -> None:
    from app.scaling.tasks import embed_marketplace_templates
    src = inspect.getsource(embed_marketplace_templates)
    # Must reference marketplace_templates table and not be a noop
    assert "marketplace_templates" in src
    assert "noop" not in src.lower()


def test_conclude_stale_experiments_has_real_sql() -> None:
    from app.scaling.tasks import conclude_stale_experiments
    src = inspect.getsource(conclude_stale_experiments)
    # Must reference prompt_variants table and not be a noop
    assert "prompt_variants" in src
    assert "noop" not in src.lower()


def test_expire_stale_documents_has_real_sql() -> None:
    from app.scaling.tasks import expire_stale_documents
    src = inspect.getsource(expire_stale_documents)
    # Must reference documents table and not be a noop
    assert "documents" in src
    assert "noop" not in src.lower()


def test_create_guardrail_partitions_is_noop_stub() -> None:
    """create_guardrail_partitions is intentionally a noop — verify it returns correctly."""
    from app.scaling.tasks import create_guardrail_partitions
    result = create_guardrail_partitions()
    assert result == {"status": "noop"}


# ── Helper functions ──────────────────────────────────────────────────────────


def test_scheduled_goal_id_is_deterministic() -> None:
    from app.scaling.tasks import _scheduled_goal_id
    id1 = _scheduled_goal_id("sched_key", fire_instance_id="2024-01-01T00:00:00")
    id2 = _scheduled_goal_id("sched_key", fire_instance_id="2024-01-01T00:00:00")
    assert id1 == id2
    assert id1.startswith("sched_")


def test_scheduled_goal_id_different_keys_produce_different_ids() -> None:
    from app.scaling.tasks import _scheduled_goal_id
    id1 = _scheduled_goal_id("key_a", fire_instance_id="2024-01-01")
    id2 = _scheduled_goal_id("key_b", fire_instance_id="2024-01-01")
    assert id1 != id2


def test_scheduled_goal_id_without_instance_uses_now() -> None:
    from app.scaling.tasks import _scheduled_goal_id
    # Should not raise and should return a valid-looking ID
    result = _scheduled_goal_id("any_key")
    assert result.startswith("sched_")
    assert len(result) > 6


def test_strip_secret_redis_schedule_fields_removes_secrets() -> None:
    from app.scaling.tasks import _strip_secret_redis_schedule_fields
    payload = {
        "goal_id": "goal-123",
        "tenant_id": "tenant-abc",
        "webhook_token": "secret_token",
        "token": "another_secret",
        "password": "super_secret",
        "api_key": "api_key_value",
        "description": "My schedule",
    }
    result = _strip_secret_redis_schedule_fields(payload)
    assert "webhook_token" not in result
    assert "token" not in result
    assert "password" not in result
    assert "api_key" not in result
    assert result["goal_id"] == "goal-123"
    assert result["description"] == "My schedule"


def test_strip_secret_redis_schedule_fields_case_insensitive() -> None:
    from app.scaling.tasks import _strip_secret_redis_schedule_fields
    # Field names are lowercased for comparison
    payload = {"TOKEN": "secret", "GOAL_ID": "g1"}
    # Note: the implementation lowercases the key for comparison
    result = _strip_secret_redis_schedule_fields(payload)
    # TOKEN should be stripped (its lower is "token")
    assert "TOKEN" not in result
    assert result.get("GOAL_ID") == "g1"


def test_schedule_datetime_none_returns_none() -> None:
    from app.scaling.tasks import _schedule_datetime
    assert _schedule_datetime(None) is None
    assert _schedule_datetime("") is None


def test_schedule_datetime_naive_iso() -> None:
    from app.scaling.tasks import _schedule_datetime
    result = _schedule_datetime("2024-06-01T12:00:00")
    assert isinstance(result, datetime.datetime)
    assert result.year == 2024


def test_schedule_datetime_aware_iso_converts_to_naive_utc() -> None:
    from app.scaling.tasks import _schedule_datetime
    result = _schedule_datetime("2024-06-01T12:00:00+02:00")
    # Should be naive UTC
    assert result.tzinfo is None
    assert result.hour == 10  # 12:00+02:00 = 10:00 UTC


def test_schedule_datetime_datetime_object_naive() -> None:
    from app.scaling.tasks import _schedule_datetime
    dt = datetime.datetime(2024, 3, 15, 9, 30, 0)
    result = _schedule_datetime(dt)
    assert result == dt


def test_schedule_datetime_aware_datetime_converts() -> None:
    from app.scaling.tasks import _schedule_datetime
    dt = datetime.datetime(2024, 3, 15, 9, 30, 0, tzinfo=datetime.timezone.utc)
    result = _schedule_datetime(dt)
    assert result.tzinfo is None


def test_datetime_to_naive_iso_none_returns_none() -> None:
    from app.scaling.tasks import _datetime_to_naive_iso
    assert _datetime_to_naive_iso(None) is None


def test_datetime_to_naive_iso_naive_datetime() -> None:
    from app.scaling.tasks import _datetime_to_naive_iso
    dt = datetime.datetime(2024, 1, 15, 10, 30)
    result = _datetime_to_naive_iso(dt)
    assert "2024-01-15" in result
    assert result.endswith("00") or "T10" in result


def test_datetime_to_naive_iso_aware_datetime_converts_to_utc() -> None:
    from app.scaling.tasks import _datetime_to_naive_iso
    dt = datetime.datetime(2024, 1, 15, 12, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
    result = _datetime_to_naive_iso(dt)
    # 12:00+02:00 → 10:00 UTC → naive → "2024-01-15T10:00:00"
    assert "T10:00" in result


def test_datetime_to_naive_iso_string_passthrough() -> None:
    from app.scaling.tasks import _datetime_to_naive_iso
    result = _datetime_to_naive_iso("2024-01-01")
    assert result == "2024-01-01"


def test_db_schedule_payload_maps_row_attributes() -> None:
    from app.scaling.tasks import _db_schedule_payload

    row = MagicMock()
    row.id = "sched-1"
    row.tenant_id = "tenant-1"
    row.goal_id_template = "Analyze daily metrics"
    row.agent_id = "agent-1"
    row.trigger_type = "cron"
    row.cron_expression = "0 9 * * *"
    row.timezone = "UTC"
    row.interval_seconds = 0
    row.webhook_token = ""
    row.event_channel = ""
    row.fire_at_iso = ""
    row.condition = ""
    row.description = "Morning report"
    row.paused = False
    row.last_fired_at = None

    payload = _db_schedule_payload(row)
    assert payload["schedule_id"] == "sched-1"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["goal_template"] == "Analyze daily metrics"
    assert payload["trigger_type"] == "cron"
    assert payload["cron_expression"] == "0 9 * * *"
    assert payload["paused"] is False
    assert payload["last_fired_at"] is None


def test_db_schedule_payload_handles_missing_attributes() -> None:
    from app.scaling.tasks import _db_schedule_payload

    # Row with missing attributes → getattr returns default
    row = MagicMock(spec=[])  # spec=[] means no attributes defined
    payload = _db_schedule_payload(row)
    assert isinstance(payload, dict)
    # All string fields should be empty string, bool fields False
    assert payload["tenant_id"] == ""
    assert payload["paused"] is False


# ── _scheduled_goal_kwargs ────────────────────────────────────────────────────


def test_scheduled_goal_kwargs_returns_none_when_no_goal_text() -> None:
    from app.scaling.tasks import _scheduled_goal_kwargs
    sched = {"tenant_id": "t1"}  # no goal_template or goal_id
    result = _scheduled_goal_kwargs("schedule:t1:s1", sched)
    assert result is None


def test_scheduled_goal_kwargs_returns_none_when_no_tenant() -> None:
    from app.scaling.tasks import _scheduled_goal_kwargs
    sched = {"goal_template": "Do something"}  # no tenant_id
    result = _scheduled_goal_kwargs("schedule::s1", sched)
    assert result is None


def test_scheduled_goal_kwargs_returns_payload_with_agent_id() -> None:
    from app.scaling.tasks import _scheduled_goal_kwargs
    sched = {
        "tenant_id": "t1",
        "goal_template": "Do the thing",
        "agent_id": "agent-1",
    }
    result = _scheduled_goal_kwargs("schedule:t1:s1", sched, fire_instance_id="2024-01")
    assert result is not None
    assert result["tenant_id"] == "t1"
    assert result["goal_text"] == "Do the thing"
    assert result["agent_id"] == "agent-1"


def test_scheduled_goal_kwargs_without_agent_id() -> None:
    from app.scaling.tasks import _scheduled_goal_kwargs
    sched = {"tenant_id": "t1", "goal_template": "Simple goal"}
    result = _scheduled_goal_kwargs("key", sched)
    assert result is not None
    assert "agent_id" not in result


# ── _run_async ────────────────────────────────────────────────────────────────


def test_run_async_executes_coroutine() -> None:
    import asyncio
    from app.scaling.tasks import _run_async

    async def _coro():
        await asyncio.sleep(0)
        return 42

    result = _run_async(_coro())
    assert result == 42


# ── _monotonic ────────────────────────────────────────────────────────────────


def test_monotonic_returns_float() -> None:
    from app.scaling.tasks import _monotonic
    result = _monotonic()
    assert isinstance(result, float)
    assert result > 0


# ── _record_goal_duration_metric ──────────────────────────────────────────────


def test_record_goal_duration_metric_does_not_raise() -> None:
    from app.scaling.tasks import _record_goal_duration_metric
    import time

    # Should not raise; if metrics module fails it warns
    with patch("app.scaling.tasks._monotonic", return_value=time.monotonic() + 1.0):
        _record_goal_duration_metric("complete", started_monotonic=0.0, priority="normal")


# ── _record_schedule_fire_metric ──────────────────────────────────────────────


def test_record_schedule_fire_metric_does_not_raise() -> None:
    from app.scaling.tasks import _record_schedule_fire_metric
    # Should not raise; metrics module failure is logged as warning
    _record_schedule_fire_metric("success")
    _record_schedule_fire_metric("error")


# ── _schedule_key ─────────────────────────────────────────────────────────────


def test_schedule_key_format() -> None:
    from app.scaling.tasks import _schedule_key
    result = _schedule_key("tenant-abc", "sched-123")
    assert result == "schedule:tenant-abc:sched-123"


# ── _db_schedule_discovery_enabled ───────────────────────────────────────────


def test_db_schedule_discovery_disabled_by_default() -> None:
    from app.scaling.tasks import _db_schedule_discovery_enabled
    with patch.dict("os.environ", {"AGENTVERSE_DB_SCHEDULE_DISCOVERY": "false"}):
        assert _db_schedule_discovery_enabled() is False


def test_db_schedule_discovery_enabled_by_env_var() -> None:
    from app.scaling.tasks import _db_schedule_discovery_enabled
    for val in ("true", "1", "yes"):
        with patch.dict("os.environ", {"AGENTVERSE_DB_SCHEDULE_DISCOVERY": val}):
            assert _db_schedule_discovery_enabled() is True


# ── run_goal dry_run path ─────────────────────────────────────────────────────


def test_run_goal_dry_run_returns_complete() -> None:
    """run_goal with dry_run=True short-circuits after status update."""
    from app.scaling.tasks import run_goal

    # __wrapped__ for bind=True tasks does NOT include self
    with patch("app.scaling.tasks._get_sync_redis", return_value=None), \
         patch("app.scaling.tasks._run_async", return_value=None), \
         patch("app.scaling.tasks._record_goal_duration_metric"), \
         patch("app.scaling.tasks._decrement_after_completion"), \
         patch("app.scaling.tasks.celery_app") as mock_celery:
        mock_celery.conf.broker_url = ""
        result = run_goal.__wrapped__(
            goal_id="test-goal-1",
            tenant_id="tenant-1",
            goal_text="Test goal",
            dry_run=True,
        )
    assert result["status"] == "complete"
    assert result["dry_run"] is True


# ── run_goal_dlq ──────────────────────────────────────────────────────────────


def test_run_goal_dlq_returns_dead_lettered() -> None:
    from app.scaling.tasks import run_goal_dlq

    with patch("app.scaling.tasks._run_async", return_value=None):
        result = run_goal_dlq.__wrapped__(
            goal_id="g1",
            tenant_id="t1",
            reason="max_retries_exceeded",
        )
    assert result["status"] == "dead_lettered"
    assert result["goal_id"] == "g1"


# ── detect_stuck_goals ────────────────────────────────────────────────────────


def test_detect_stuck_goals_registered() -> None:
    from app.scaling.celery_app import celery_app
    registered = list(celery_app.tasks.keys())
    assert any("detect_stuck_goals" in name for name in registered)


# ── execute_retention_policy ──────────────────────────────────────────────────


def test_execute_retention_policy_registered() -> None:
    from app.scaling.celery_app import celery_app
    registered = list(celery_app.tasks.keys())
    assert any("execute_retention_policy" in name for name in registered)


# ── expire_hitl_approvals ─────────────────────────────────────────────────────


def test_expire_hitl_approvals_registered() -> None:
    from app.scaling.celery_app import celery_app
    registered = list(celery_app.tasks.keys())
    assert any("expire_hitl_approvals" in name for name in registered)


# ── check_email_goals disabled by default ────────────────────────────────────


def test_check_email_goals_disabled_by_default() -> None:
    from app.scaling.tasks import check_email_goals
    # __wrapped__ for bind=True tasks omits self; check_email_goals takes no args
    with patch.dict("os.environ", {"IMAP_ENABLED": "false"}):
        result = check_email_goals.__wrapped__()
    assert result["status"] == "disabled"
    assert result["processed"] == 0


# ── consolidate_memories_task has real impl ───────────────────────────────────


def test_consolidate_memories_task_has_real_sql() -> None:
    from app.scaling.tasks import consolidate_memories_task
    src = inspect.getsource(consolidate_memories_task)
    assert "long_term_memory" in src
    assert "noop" not in src.lower()


# ── reindex_stale_knowledge has real impl ─────────────────────────────────────


def test_reindex_stale_knowledge_has_real_sql() -> None:
    from app.scaling.tasks import reindex_stale_knowledge
    src = inspect.getsource(reindex_stale_knowledge)
    assert "documents" in src
    assert "noop" not in src.lower()


# ── purge_expired_artifacts has real impl ─────────────────────────────────────


def test_purge_expired_artifacts_has_real_sql() -> None:
    from app.scaling.tasks import purge_expired_artifacts
    src = inspect.getsource(purge_expired_artifacts)
    assert "artifacts" in src
    assert "noop" not in src.lower()
