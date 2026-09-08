"""Unit tests for the pure missed-fire catch-up helpers in app.scaling.tasks.

These exercise ``_cron_missed_runs_utc`` and ``_rrule_missed_runs_utc`` directly
as pure functions — no Celery task, no fake Redis, no monkeypatching of the whole
``fire_due_schedules.run()``. That full-task mocking approach is brittle and was
the cause of a previously reverted attempt; here we test the maths in isolation.
"""

from __future__ import annotations

import datetime

import pytest

from app.scaling.tasks import (
    _MISSED_FIRE_CAP,
    _cron_missed_runs_utc,
    _rrule_missed_runs_utc,
)


def _dt(y: int, mo: int, d: int, h: int, mi: int, s: int = 0) -> datetime.datetime:
    return datetime.datetime(y, mo, d, h, mi, s)


# ── _cron_missed_runs_utc ─────────────────────────────────────────────────────


def test_cron_new_schedule_fires_only_most_recent_slot() -> None:
    """A schedule that has never fired (last_fired=None) backfills a single slot."""
    runs = _cron_missed_runs_utc("*/5 * * * *", None, _dt(2026, 1, 1, 0, 7))
    assert runs == [_dt(2026, 1, 1, 0, 5)]


def test_cron_fires_every_missed_slot_since_last_fired() -> None:
    """Every intermediate slot in (last_fired, now] fires, oldest first."""
    runs = _cron_missed_runs_utc(
        "*/5 * * * *",
        _dt(2026, 1, 1, 0, 0),
        _dt(2026, 1, 1, 0, 16),
    )
    assert runs == [
        _dt(2026, 1, 1, 0, 5),
        _dt(2026, 1, 1, 0, 10),
        _dt(2026, 1, 1, 0, 15),
    ]


def test_cron_nothing_due_returns_empty() -> None:
    """When the most-recent slot equals last_fired, nothing is due."""
    runs = _cron_missed_runs_utc(
        "*/5 * * * *",
        _dt(2026, 1, 1, 0, 5),
        _dt(2026, 1, 1, 0, 7),
    )
    assert runs == []


def test_cron_missed_runs_are_bounded_by_cap() -> None:
    """A long outage is capped to the most-recent _MISSED_FIRE_CAP slots."""
    runs = _cron_missed_runs_utc(
        "* * * * *",
        _dt(2026, 1, 1, 0, 0),
        _dt(2026, 1, 1, 5, 0),  # 5h gap ⇒ 300 minute-slots, far past the cap
    )
    assert len(runs) == _MISSED_FIRE_CAP
    assert runs == sorted(runs)  # oldest first
    assert len(set(runs)) == _MISSED_FIRE_CAP  # distinct slots
    assert runs[-1] == _dt(2026, 1, 1, 5, 0)  # keeps the most-recent slots


def test_cron_evaluated_in_schedule_timezone() -> None:
    """The cron expression fires on wall-clock time in the given timezone."""
    # "0 9 * * *" = 09:00 daily. In America/New_York in January (EST, UTC-5),
    # that is 14:00 UTC — not 09:00 UTC.
    runs = _cron_missed_runs_utc(
        "0 9 * * *",
        None,
        _dt(2026, 1, 15, 14, 30),  # 09:30 EST
        tz_name="America/New_York",
    )
    assert runs == [_dt(2026, 1, 15, 14, 0)]


def test_cron_invalid_expression_raises() -> None:
    """An unparseable cron expression raises (caller catches and skips)."""
    # croniter raises CroniterBadCronError, a ValueError subclass.
    with pytest.raises(ValueError):
        _cron_missed_runs_utc("NOT-A-CRON", None, _dt(2026, 1, 1, 0, 0))


# ── _rrule_missed_runs_utc ────────────────────────────────────────────────────


def test_rrule_new_schedule_fires_only_most_recent_occurrence() -> None:
    pytest.importorskip("dateutil")
    runs = _rrule_missed_runs_utc(
        "DTSTART:20260101T000000\nRRULE:FREQ=HOURLY",
        None,
        _dt(2026, 1, 1, 3, 30),
    )
    assert runs == [_dt(2026, 1, 1, 3, 0)]


def test_rrule_fires_every_missed_occurrence_since_last_fired() -> None:
    pytest.importorskip("dateutil")
    runs = _rrule_missed_runs_utc(
        "DTSTART:20260101T000000\nRRULE:FREQ=HOURLY",
        _dt(2026, 1, 1, 0, 0),
        _dt(2026, 1, 1, 3, 30),
    )
    assert runs == [
        _dt(2026, 1, 1, 1, 0),
        _dt(2026, 1, 1, 2, 0),
        _dt(2026, 1, 1, 3, 0),
    ]


def test_rrule_nothing_due_returns_empty() -> None:
    pytest.importorskip("dateutil")
    runs = _rrule_missed_runs_utc(
        "DTSTART:20260101T000000\nRRULE:FREQ=HOURLY",
        _dt(2026, 1, 1, 3, 0),
        _dt(2026, 1, 1, 3, 30),
    )
    assert runs == []


def test_rrule_missed_runs_are_bounded_by_cap() -> None:
    pytest.importorskip("dateutil")
    runs = _rrule_missed_runs_utc(
        "DTSTART:20260101T000000\nRRULE:FREQ=MINUTELY",
        _dt(2026, 1, 1, 0, 0),
        _dt(2026, 1, 1, 5, 0),
    )
    assert len(runs) == _MISSED_FIRE_CAP
    assert runs == sorted(runs)
    assert runs[-1] == _dt(2026, 1, 1, 5, 0)
