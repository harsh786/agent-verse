"""Coverage for the less-common ``fire_due_schedules`` trigger types.

The main ``fire_due_schedules`` task (app/scaling/tasks.py) dispatches on
``trigger_type`` inside a big per-schedule loop. Cron/interval/once are
already covered by ``tests/scaling/test_tasks_extra3.py``; this file adds the
remaining branches, which were entirely uncovered:

 - deadline, business_calendar (date-math helpers are stubbed out so the
   branch is exercised without reproducing real cron/deadline arithmetic)
 - file_drop, rss_feed, api_poll, db_row_change (each polls an external
   source and dispatches a ``run_goal`` alert goal)
 - alertmanager / datadog / pagerduty (external alert webhooks)

All external I/O (filesystem, RSS/HTTP polling, DB row counts, the alert
builder, and ``run_goal.apply_async``) is mocked; only ``fire_due_schedules``
itself and its real (encapsulated) helpers run.
"""

from __future__ import annotations

import datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

NOW = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def _redis_url_env(monkeypatch):
    """``fire_due_schedules`` only attempts Redis discovery when REDIS_URL is
    set — all tests in this file drive it through a mocked ``redis.from_url``."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")


def _redis_mock(payloads: dict[str, dict], *, extra_get: dict[str, str] | None = None):
    """A ``redis.from_url`` stand-in whose ``get`` resolves both the schedule
    payload keys (``schedule:...``) and any per-trigger dedupe keys."""
    extra_get = extra_get or {}
    raw_payloads = {k: json.dumps(v) for k, v in payloads.items()}

    def _get(key, *a, **kw):
        if key in raw_payloads:
            return raw_payloads[key]
        return extra_get.get(key)

    mock_r = MagicMock()
    mock_r.scan_iter = MagicMock(return_value=list(payloads.keys()))
    mock_r.get = MagicMock(side_effect=_get)
    mock_r.set = MagicMock()
    mock_r.delete = MagicMock()
    return mock_r


class TestDeadlineAndBusinessCalendar:
    def test_deadline_trigger_fires(self):
        from app.scaling.tasks import fire_due_schedules

        mock_r = _redis_mock(
            {
                "schedule:t1:deadline1": {
                    "trigger_type": "deadline",
                    "tenant_id": "t1",
                    "goal_template": "finish the report",
                    "fire_at_iso": NOW.isoformat(),
                    "deadline_warning_seconds": 3600,
                }
            }
        )
        with (
            patch("redis.from_url", return_value=mock_r),
            patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
            patch("app.scaling.tasks._deadline_due_utc", return_value=NOW),
            patch("app.scaling.tasks.run_scheduled_goal.apply_async") as mock_apply,
        ):
            result = fire_due_schedules.run()

        assert result["schedules_fired"] == 1
        mock_apply.assert_called_once()

    def test_business_calendar_trigger_fires(self):
        from app.scaling.tasks import fire_due_schedules

        mock_r = _redis_mock(
            {
                "schedule:t1:bizcal1": {
                    "trigger_type": "business_calendar",
                    "tenant_id": "t1",
                    "goal_template": "daily standup summary",
                    "cron_expression": "0 9 * * 1-5",
                    "timezone": "UTC",
                }
            }
        )
        with (
            patch("redis.from_url", return_value=mock_r),
            patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
            patch("app.scaling.tasks._business_calendar_slots", return_value=[NOW]),
            patch("app.scaling.tasks.run_scheduled_goal.apply_async") as mock_apply,
        ):
            result = fire_due_schedules.run()

        assert result["schedules_fired"] == 1
        mock_apply.assert_called_once()

    def test_business_calendar_parse_error_is_skipped(self):
        from app.scaling.tasks import fire_due_schedules

        mock_r = _redis_mock(
            {
                "schedule:t1:bizcal_bad": {
                    "trigger_type": "business_calendar",
                    "tenant_id": "t1",
                    "goal_template": "x",
                    "cron_expression": "bad cron",
                    "timezone": "UTC",
                }
            }
        )
        with (
            patch("redis.from_url", return_value=mock_r),
            patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
            patch(
                "app.scaling.tasks._business_calendar_slots",
                side_effect=ValueError("bad cron expr"),
            ),
        ):
            result = fire_due_schedules.run()

        assert result["schedules_fired"] == 0


class TestFileDropTrigger:
    def test_new_file_dispatches_goal(self):
        from app.scaling.tasks import fire_due_schedules

        mock_r = _redis_mock(
            {
                "schedule:t1:filedrop1": {
                    "trigger_type": "file_drop",
                    "tenant_id": "t1",
                    "file_watch_path": "/tmp/watch",
                    "file_pattern": "*.txt",
                }
            }
        )
        with (
            patch("redis.from_url", return_value=mock_r),
            patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
            patch("os.path.isdir", return_value=True),
            patch("os.listdir", return_value=["report.txt"]),
            patch(
                "app.scaling.tasks._build_goal_kwargs_for_alert",
                new=AsyncMock(
                    return_value={"goal": "handle new file", "priority": "medium", "agent_id": ""}
                ),
            ),
            patch("app.scaling.tasks.run_goal.apply_async") as mock_apply,
        ):
            result = fire_due_schedules.run()

        assert result["schedules_fired"] == 1
        mock_apply.assert_called_once()

    def test_no_new_files_does_not_fire(self):
        from app.scaling.tasks import fire_due_schedules

        mock_r = _redis_mock(
            {
                "schedule:t1:filedrop2": {
                    "trigger_type": "file_drop",
                    "tenant_id": "t1",
                    "file_watch_path": "/tmp/watch",
                    "file_pattern": "*.txt",
                }
            }
        )
        with (
            patch("redis.from_url", return_value=mock_r),
            patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
            patch("os.path.isdir", return_value=True),
            patch("os.listdir", return_value=[]),
        ):
            result = fire_due_schedules.run()

        assert result["schedules_fired"] == 0


class TestRssFeedTrigger:
    def test_new_entry_dispatches_goal(self):
        from app.scaling.tasks import fire_due_schedules

        mock_r = _redis_mock(
            {
                "schedule:t1:rss1": {
                    "trigger_type": "rss_feed",
                    "tenant_id": "t1",
                    "rss_url": "https://example.com/feed.xml",
                }
            }
        )
        entry = SimpleNamespace(entry_id="e1", title="New post", link="https://example.com/e1")
        with (
            patch("redis.from_url", return_value=mock_r),
            patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
            patch("app.triggers.rss.fetch_rss_entries", return_value=[entry]),
            patch("app.triggers.rss.new_entries", return_value=[entry]),
            patch(
                "app.scaling.tasks._build_goal_kwargs_for_alert",
                new=AsyncMock(
                    return_value={"goal": "summarize new post", "priority": "medium", "agent_id": ""}
                ),
            ),
            patch("app.scaling.tasks.run_goal.apply_async") as mock_apply,
        ):
            result = fire_due_schedules.run()

        assert result["schedules_fired"] == 1
        mock_apply.assert_called_once()

    def test_fetch_error_is_caught(self):
        from app.scaling.tasks import fire_due_schedules

        mock_r = _redis_mock(
            {
                "schedule:t1:rss_err": {
                    "trigger_type": "rss_feed",
                    "tenant_id": "t1",
                    "rss_url": "https://example.com/feed.xml",
                }
            }
        )
        with (
            patch("redis.from_url", return_value=mock_r),
            patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
            patch("app.triggers.rss.fetch_rss_entries", side_effect=RuntimeError("network down")),
            patch("app.triggers.rss.new_entries", return_value=[]),
        ):
            result = fire_due_schedules.run()

        assert result["schedules_fired"] == 0


class TestApiPollTrigger:
    def test_value_change_dispatches_goal(self):
        from app.scaling.tasks import fire_due_schedules

        mock_r = _redis_mock(
            {
                "schedule:t1:apipoll1": {
                    "trigger_type": "api_poll",
                    "tenant_id": "t1",
                    "poll_url": "https://example.com/status",
                    "poll_jsonpath": "$.status",
                }
            }
        )
        with (
            patch("redis.from_url", return_value=mock_r),
            patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
            patch("app.triggers.polling.fetch_json", return_value={"status": "changed"}),
            patch("app.triggers.polling.extract_path", return_value="changed"),
            patch("app.triggers.polling.poll_should_fire", return_value=True),
            patch(
                "app.scaling.tasks._build_goal_kwargs_for_alert",
                new=AsyncMock(
                    return_value={"goal": "investigate change", "priority": "medium", "agent_id": ""}
                ),
            ),
            patch("app.scaling.tasks.run_goal.apply_async") as mock_apply,
        ):
            result = fire_due_schedules.run()

        assert result["schedules_fired"] == 1
        mock_apply.assert_called_once()

    def test_fetch_error_is_caught(self):
        from app.scaling.tasks import fire_due_schedules

        mock_r = _redis_mock(
            {
                "schedule:t1:apipoll_err": {
                    "trigger_type": "api_poll",
                    "tenant_id": "t1",
                    "poll_url": "https://example.com/status",
                }
            }
        )
        with (
            patch("redis.from_url", return_value=mock_r),
            patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
            patch("app.triggers.polling.fetch_json", side_effect=RuntimeError("timeout")),
        ):
            result = fire_due_schedules.run()

        assert result["schedules_fired"] == 0


class TestDbRowChangeTrigger:
    def test_row_count_growth_dispatches_goal(self):
        from app.scaling.tasks import fire_due_schedules

        mock_r = _redis_mock(
            {
                "schedule:t1:dbrow1": {
                    "trigger_type": "db_row_change",
                    "tenant_id": "t1",
                    "db_table": "goal_events",
                }
            },
            extra_get={"db_row_count:schedule:t1:dbrow1": "5"},
        )
        with (
            patch("redis.from_url", return_value=mock_r),
            patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
            patch(
                "app.scaling.tasks._db_row_change_allowlist",
                return_value=frozenset({"goal_events"}),
            ),
            patch("app.scaling.tasks._count_tenant_rows", new=AsyncMock(return_value=10)),
            patch(
                "app.scaling.tasks._build_goal_kwargs_for_alert",
                new=AsyncMock(
                    return_value={"goal": "row count grew", "priority": "medium", "agent_id": ""}
                ),
            ),
            patch("app.scaling.tasks.run_goal.apply_async") as mock_apply,
        ):
            result = fire_due_schedules.run()

        assert result["schedules_fired"] == 1
        mock_apply.assert_called_once()

    def test_disallowed_table_never_fires(self):
        from app.scaling.tasks import fire_due_schedules

        mock_r = _redis_mock(
            {
                "schedule:t1:dbrow_bad": {
                    "trigger_type": "db_row_change",
                    "tenant_id": "t1",
                    "db_table": "not_allowlisted",
                }
            }
        )
        with (
            patch("redis.from_url", return_value=mock_r),
            patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
            patch(
                "app.scaling.tasks._db_row_change_allowlist",
                return_value=frozenset({"goal_events"}),
            ),
            patch("app.scaling.tasks.run_goal.apply_async") as mock_apply,
        ):
            result = fire_due_schedules.run()

        assert result["schedules_fired"] == 0
        mock_apply.assert_not_called()

    def test_no_growth_does_not_fire(self):
        from app.scaling.tasks import fire_due_schedules

        mock_r = _redis_mock(
            {
                "schedule:t1:dbrow_flat": {
                    "trigger_type": "db_row_change",
                    "tenant_id": "t1",
                    "db_table": "goal_events",
                }
            },
            extra_get={"db_row_count:schedule:t1:dbrow_flat": "10"},
        )
        with (
            patch("redis.from_url", return_value=mock_r),
            patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
            patch(
                "app.scaling.tasks._db_row_change_allowlist",
                return_value=frozenset({"goal_events"}),
            ),
            patch("app.scaling.tasks._count_tenant_rows", new=AsyncMock(return_value=10)),
            patch("app.scaling.tasks.run_goal.apply_async") as mock_apply,
        ):
            result = fire_due_schedules.run()

        assert result["schedules_fired"] == 0
        mock_apply.assert_not_called()


class TestExternalAlertTriggers:
    def test_alertmanager_fallback_payload_fires(self):
        from app.scaling.tasks import fire_due_schedules

        mock_r = _redis_mock(
            {
                "schedule:t1:alert1": {
                    "trigger_type": "alertmanager",
                    "tenant_id": "t1",
                    "schedule_id": "alert1",
                }
            }
        )
        with (
            patch("redis.from_url", return_value=mock_r),
            patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
            patch(
                "app.scaling.tasks._build_goal_kwargs_for_alert",
                new=AsyncMock(
                    return_value={"goal": "investigate alert", "priority": "high", "agent_id": ""}
                ),
            ),
            patch("app.scaling.tasks.run_goal.apply_async") as mock_apply,
        ):
            result = fire_due_schedules.run()

        assert result["schedules_fired"] == 1
        mock_apply.assert_called_once()

    def test_datadog_cached_payload_fires(self):
        from app.scaling.tasks import fire_due_schedules

        mock_r = _redis_mock(
            {
                "schedule:t1:alert2": {
                    "trigger_type": "datadog",
                    "tenant_id": "t1",
                    "schedule_id": "alert2",
                }
            },
            extra_get={"alert_payload:datadog:alert2": json.dumps({"monitor_name": "cpu-high"})},
        )
        with (
            patch("redis.from_url", return_value=mock_r),
            patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
            patch(
                "app.scaling.tasks._build_goal_kwargs_for_alert",
                new=AsyncMock(
                    return_value={"goal": "investigate cpu", "priority": "high", "agent_id": ""}
                ),
            ),
            patch("app.scaling.tasks.run_goal.apply_async") as mock_apply,
        ):
            result = fire_due_schedules.run()

        assert result["schedules_fired"] == 1
        mock_apply.assert_called_once()

    def test_pagerduty_missing_tenant_id_is_skipped(self):
        from app.scaling.tasks import fire_due_schedules

        mock_r = _redis_mock(
            {
                "schedule:t1:alert3": {
                    "trigger_type": "pagerduty",
                    "schedule_id": "alert3",
                }
            }
        )
        with (
            patch("redis.from_url", return_value=mock_r),
            patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
            patch("app.scaling.tasks.run_goal.apply_async") as mock_apply,
        ):
            result = fire_due_schedules.run()

        assert result["schedules_fired"] == 0
        mock_apply.assert_not_called()
