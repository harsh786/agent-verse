"""2.W-1 / Family A time triggers: relative_delay, deadline, business_calendar."""

from __future__ import annotations

import datetime

import pytest

from app.scaling.tasks import (
    _business_calendar_slots,
    _deadline_due_utc,
    _is_business_time,
    _relative_delay_due_utc,
)


def test_relative_delay_fires_after_base_plus_offset() -> None:
    base = "2026-01-01T10:00:00"
    now = datetime.datetime(2026, 1, 1, 10, 5, 0)  # 5 min later
    # offset 300s → target 10:05 → due at now.
    due = _relative_delay_due_utc(base, 300, now, None)
    assert due == datetime.datetime(2026, 1, 1, 10, 5, 0)


def test_relative_delay_not_due_yet() -> None:
    base = "2026-01-01T10:00:00"
    now = datetime.datetime(2026, 1, 1, 10, 1, 0)
    assert _relative_delay_due_utc(base, 300, now, None) is None


def test_relative_delay_only_fires_once() -> None:
    base = "2026-01-01T10:00:00"
    now = datetime.datetime(2026, 1, 1, 11, 0, 0)
    already = datetime.datetime(2026, 1, 1, 10, 5, 0)
    assert _relative_delay_due_utc(base, 300, now, already) is None


def test_deadline_fires_warning_before() -> None:
    deadline = "2026-01-01T12:00:00"
    # warning 3600s → fire at 11:00.
    now = datetime.datetime(2026, 1, 1, 11, 0, 0)
    due = _deadline_due_utc(deadline, 3600, now, None)
    assert due == datetime.datetime(2026, 1, 1, 11, 0, 0)


def test_deadline_not_due_before_warning_window() -> None:
    deadline = "2026-01-01T12:00:00"
    now = datetime.datetime(2026, 1, 1, 10, 30, 0)  # before 11:00
    assert _deadline_due_utc(deadline, 3600, now, None) is None


def test_is_business_time_utc() -> None:
    # 2026-01-01 is a Thursday. 10:00 UTC is business time; 20:00 is not.
    assert _is_business_time(datetime.datetime(2026, 1, 1, 10, 0), "UTC") is True
    assert _is_business_time(datetime.datetime(2026, 1, 1, 20, 0), "UTC") is False
    # 2026-01-03 is a Saturday.
    assert _is_business_time(datetime.datetime(2026, 1, 3, 10, 0), "UTC") is False


def test_business_calendar_filters_to_business_hours() -> None:
    pytest.importorskip("croniter")
    # Daily-at-20:00 cron: the 20:00 slot is outside business hours → filtered out.
    now = datetime.datetime(2026, 1, 1, 21, 0, 0)  # Thursday 21:00
    slots = _business_calendar_slots("0 20 * * *", None, now, "UTC")
    assert slots == []
    # Daily-at-10:00: within business hours on a weekday → kept.
    now2 = datetime.datetime(2026, 1, 1, 11, 0, 0)
    slots2 = _business_calendar_slots("0 10 * * *", None, now2, "UTC")
    assert slots2 == [datetime.datetime(2026, 1, 1, 10, 0, 0)]
