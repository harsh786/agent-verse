"""2.W-9 (timezone slice): cron schedules must honour their stored timezone.

``fire_due_schedules`` evaluated the cron expression against naive UTC, ignoring
the schedule's ``timezone`` field — so ``"0 9 * * *"`` with
``timezone="Asia/Kolkata"`` fired at 09:00 UTC instead of the correct 03:30 UTC.
This exercises the pure helper that computes the previous fire instant in the
schedule's timezone and returns it as naive UTC for comparison.
"""

from __future__ import annotations

import datetime

import pytest

pytest.importorskip("croniter")

from app.scaling.tasks import _cron_missed_runs_utc


def test_cron_ist_schedule_maps_to_correct_utc_instant() -> None:
    # 2026-01-01 12:00 UTC. Previous 09:00 Asia/Kolkata (UTC+5:30) fire is
    # 2026-01-01 09:00 IST == 2026-01-01 03:30 UTC.
    now = datetime.datetime(2026, 1, 1, 12, 0, 0)
    prev = _cron_missed_runs_utc("0 9 * * *", None, now, "Asia/Kolkata")[0]
    assert prev == datetime.datetime(2026, 1, 1, 3, 30, 0)


def test_cron_utc_schedule_unchanged() -> None:
    now = datetime.datetime(2026, 1, 1, 12, 0, 0)
    prev = _cron_missed_runs_utc("0 9 * * *", None, now, "UTC")[0]
    assert prev == datetime.datetime(2026, 1, 1, 9, 0, 0)


def test_cron_unknown_timezone_falls_back_to_utc() -> None:
    now = datetime.datetime(2026, 1, 1, 12, 0, 0)
    prev = _cron_missed_runs_utc("0 9 * * *", None, now, "Not/AZone")[0]
    assert prev == datetime.datetime(2026, 1, 1, 9, 0, 0)


def test_cron_empty_timezone_defaults_utc() -> None:
    now = datetime.datetime(2026, 1, 1, 12, 0, 0)
    prev = _cron_missed_runs_utc("0 9 * * *", None, now, "")[0]
    assert prev == datetime.datetime(2026, 1, 1, 9, 0, 0)
