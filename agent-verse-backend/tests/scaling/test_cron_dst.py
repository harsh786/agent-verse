"""B1-11: cron schedules across daylight-saving transitions fire once per day.

Simulating the beat minute by minute in America/New_York showed that on the
fall-back day (2026-11-01, 01:00-01:59 happens twice) a "30 1 * * *" schedule
fired twice (05:30 and 06:30 UTC). A fixed-hour job runs once in the repeated
hour (as cron does); a job whose hour field is a wildcard keeps running on
real time through both occurrences. Spring-forward: a slot in the skipped hour
fires once, right after the jump; local times are kept across the change.
"""

from __future__ import annotations

import datetime as dt

from app.scaling import tasks

NY = "America/New_York"


def _simulate(expr: str, tz: str, start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    """Run the beat's slot evaluation once a minute; returns every fired slot."""
    last: dt.datetime | None = None
    fired: list[dt.datetime] = []
    t = start
    while t <= end:
        sched = {
            "trigger_type": "cron",
            "cron_expression": expr,
            "timezone": tz,
            "armed_at": start.isoformat(),
            "last_fired_at": last.isoformat() if last else None,
        }
        for slot in tasks._time_trigger_slots(sched, t):
            fired.append(slot)
            last = slot
        t += dt.timedelta(minutes=1)
    return fired


def test_a_fixed_time_in_the_repeated_hour_fires_once_on_fall_back() -> None:
    fired = _simulate("30 1 * * *", NY, dt.datetime(2026, 10, 31, 0, 0), dt.datetime(2026, 11, 2, 12))
    assert fired == [
        dt.datetime(2026, 10, 31, 5, 30),  # 01:30 EDT
        dt.datetime(2026, 11, 1, 5, 30),  # 01:30 EDT, the first 01:30 of the day
        dt.datetime(2026, 11, 2, 6, 30),  # 01:30 EST
    ]


def test_a_wildcard_hour_keeps_running_through_the_repeated_hour() -> None:
    fired = _simulate("*/30 * * * *", NY, dt.datetime(2026, 11, 1, 4, 1), dt.datetime(2026, 11, 1, 7, 1))
    # Every 30 real minutes: 00:30 EDT ... 01:30 EDT, 01:00 EST, 01:30 EST, 02:00 EST.
    assert fired == [dt.datetime(2026, 11, 1, 4, 30) + dt.timedelta(minutes=30 * i) for i in range(6)]


def test_a_time_in_the_skipped_hour_fires_once_after_spring_forward() -> None:
    fired = _simulate("30 2 * * *", NY, dt.datetime(2026, 3, 7, 0, 0), dt.datetime(2026, 3, 9, 12))
    assert fired == [
        dt.datetime(2026, 3, 7, 7, 30),  # 02:30 EST
        dt.datetime(2026, 3, 8, 7, 0),  # 02:30 does not exist: 03:00 EDT, once
        dt.datetime(2026, 3, 9, 6, 30),  # 02:30 EDT
    ]


def test_local_time_is_kept_across_the_change() -> None:
    fired = _simulate("0 9 * * *", NY, dt.datetime(2026, 10, 30, 0, 0), dt.datetime(2026, 11, 3, 0, 0))
    assert fired == [
        dt.datetime(2026, 10, 30, 13, 0),
        dt.datetime(2026, 10, 31, 13, 0),
        dt.datetime(2026, 11, 1, 14, 0),  # 09:00 EST from the change on
        dt.datetime(2026, 11, 2, 14, 0),
    ]


def test_a_southern_hemisphere_change_is_handled_too() -> None:
    # Australia/Sydney falls back on 2026-04-05 (03:00 AEDT -> 02:00 AEST).
    fired = _simulate(
        "30 2 * * *", "Australia/Sydney", dt.datetime(2026, 4, 3, 12, 0), dt.datetime(2026, 4, 6, 12)
    )
    assert len(fired) == 3  # once per local day, not twice on the 5th
