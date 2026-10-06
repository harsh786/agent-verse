"""B1-6: business_calendar skips weekends AND holidays, in the trigger's timezone.

``business_calendar`` only knew a hard-coded Mon-Fri 09:00-17:00 window:
``business_calendar_id`` was refused and there was no way to declare a holiday,
so a "weekday 10:00 report" still ran on Diwali / Christmas. The spec now
carries ``holidays`` (local ``YYYY-MM-DD`` dates), ``business_days`` (Python
weekday numbers, Monday = 0) and ``business_hours_start`` / ``_end``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.scaling import tasks
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import apply_config_to_spec, spec_config
from app.triggers.validation import validate_spec


def _bc(**extra: Any) -> dict[str, Any]:
    return {
        "trigger_type": "business_calendar",
        "cron_expression": "0 10 * * *",
        "timezone": "Asia/Kolkata",
        "armed_at": "2026-11-05T00:00:00",
        **extra,
    }


def _slots(sched: dict[str, Any], now: dt.datetime) -> list[dt.datetime]:
    return tasks._time_trigger_slots(sched, now)


# 10:00 IST == 04:30 UTC.
def test_a_weekday_slot_fires() -> None:
    # Thursday 2026-11-05.
    assert _slots(_bc(), dt.datetime(2026, 11, 5, 4, 31)) == [dt.datetime(2026, 11, 5, 4, 30)]


def test_a_weekend_slot_is_skipped() -> None:
    # Saturday 2026-11-07 / Sunday 2026-11-08.
    sched = _bc(last_fired_at="2026-11-06T04:30:00")
    assert _slots(sched, dt.datetime(2026, 11, 7, 4, 31)) == []
    assert _slots(sched, dt.datetime(2026, 11, 8, 4, 31)) == []


def test_a_holiday_is_skipped_in_the_triggers_timezone() -> None:
    # Diwali, Monday 2026-11-09 (IST). The local date decides, not the UTC date.
    sched = _bc(holidays=["2026-11-09", "2026-12-25"], last_fired_at="2026-11-06T04:30:00")
    assert _slots(sched, dt.datetime(2026, 11, 9, 4, 31)) == []
    assert _slots(sched, dt.datetime(2026, 11, 10, 4, 31)) == [dt.datetime(2026, 11, 10, 4, 30)]


def test_a_local_holiday_late_evening_maps_to_the_local_date() -> None:
    # 23:30 IST on 2026-12-24 is 18:00 UTC on the 24th; 00:30 IST on the 25th is
    # 19:00 UTC on the 24th: the second one is the holiday.
    sched = _bc(cron_expression="30 23,0 * * *", holidays=["2026-12-25"],
                business_hours_start="00:00", business_hours_end="23:59")
    sched["last_fired_at"] = "2026-12-24T17:00:00"
    assert _slots(sched, dt.datetime(2026, 12, 24, 18, 1)) == [dt.datetime(2026, 12, 24, 18, 0)]
    sched["last_fired_at"] = "2026-12-24T18:00:00"
    assert _slots(sched, dt.datetime(2026, 12, 24, 19, 1)) == []


def test_custom_business_days_and_hours() -> None:
    # A Sunday-Thursday week, 08:00-14:00 local (Asia/Dubai).
    sched = {
        "trigger_type": "business_calendar",
        "cron_expression": "0 * * * *",
        "timezone": "Asia/Dubai",
        "armed_at": "2026-01-01T00:00:00",
        "business_days": [6, 0, 1, 2, 3],
        "business_hours_start": "08:00",
        "business_hours_end": "14:00",
    }
    # Sunday 2026-11-08 09:00 Dubai == 05:00 UTC: a working day there.
    sched["last_fired_at"] = "2026-11-08T04:00:00"
    assert _slots(sched, dt.datetime(2026, 11, 8, 5, 1)) == [dt.datetime(2026, 11, 8, 5, 0)]
    # Friday 2026-11-06 09:00 Dubai: weekend.
    sched["last_fired_at"] = "2026-11-06T04:00:00"
    assert _slots(sched, dt.datetime(2026, 11, 6, 5, 1)) == []
    # Sunday 15:00 Dubai (11:00 UTC): after hours.
    sched["last_fired_at"] = "2026-11-08T10:00:00"
    assert _slots(sched, dt.datetime(2026, 11, 8, 11, 1)) == []


def test_the_calendar_round_trips_through_the_schedule_config() -> None:
    spec = TriggerSpec(
        trigger_type=TriggerType.BUSINESS_CALENDAR,
        cron_expression="0 10 * * *",
        timezone="Asia/Kolkata",
        holidays=["2026-11-09"],
        business_days=[0, 1, 2, 3, 4, 5],
        business_hours_start="08:30",
        business_hours_end="18:00",
    )
    validate_spec(spec, plan="enterprise")
    cfg = spec_config(spec)
    restored = TriggerSpec(trigger_type=TriggerType.BUSINESS_CALENDAR)
    apply_config_to_spec(restored, cfg)
    assert restored.holidays == ["2026-11-09"]
    assert restored.business_days == [0, 1, 2, 3, 4, 5]
    assert (restored.business_hours_start, restored.business_hours_end) == ("08:30", "18:00")


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("holidays", ["2026-13-01"], "holidays"),
        ("holidays", ["Diwali"], "holidays"),
        ("holidays", ["2026-01-01"] * 2 + [f"2026-01-{d:02d}" for d in range(1, 32)] * 20,
         "holidays"),
        ("business_days", [], "business_days"),
        ("business_days", [7], "business_days"),
        ("business_hours_start", "9am", "business_hours"),
        ("business_hours_end", "08:00", "business_hours"),
    ],
)
def test_an_invalid_calendar_is_refused(field: str, value: Any, match: str) -> None:
    spec = TriggerSpec(trigger_type=TriggerType.BUSINESS_CALENDAR, cron_expression="0 10 * * *")
    setattr(spec, field, value)
    with pytest.raises(ValueError, match=match):
        validate_spec(spec, plan="enterprise")
