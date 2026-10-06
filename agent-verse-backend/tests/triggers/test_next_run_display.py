"""B1-13: the API's ``next_fire_at`` is the trigger's real next run.

Live (SCHED-CRUD, 2026-10-06): a new weekday-9am cron answered
``next_fire_at = 01:40:00`` (the beat's 10-minute claim lease) instead of
09:00: the column is the beat's internal "evaluate again at" hint, which also
holds the lease while a tick processes the row, the next cron slot for a
business_calendar even on a holiday or at night, and 9999-01-01 for a fired
one-shot. The API now computes the next run from the trigger itself.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.next_run import next_run_at

NOW = dt.datetime(2026, 10, 6, 1, 30, 20, tzinfo=dt.UTC)  # a Tuesday


def _rec(spec: TriggerSpec, **kw: Any) -> dict[str, Any]:
    return {"spec": spec, "paused": False, "next_fire_at": NOW + dt.timedelta(minutes=10), **kw}


def test_cron_next_run_ignores_the_beats_lease() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * 1-5")
    assert next_run_at(_rec(spec), NOW) == dt.datetime(2026, 10, 6, 9, 0, tzinfo=dt.UTC)


def test_cron_next_run_is_in_its_timezone() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="30 9 * * *",
                       timezone="Asia/Kolkata")
    assert next_run_at(_rec(spec), NOW) == dt.datetime(2026, 10, 6, 4, 0, tzinfo=dt.UTC)


def test_business_calendar_next_run_skips_nights_weekends_and_holidays() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.BUSINESS_CALENDAR, cron_expression="0 * * * *",
                       holidays=["2026-10-06"])
    # Tuesday is a holiday -> Wednesday 09:00 UTC, not 02:00 tonight.
    assert next_run_at(_rec(spec), NOW) == dt.datetime(2026, 10, 7, 9, 0, tzinfo=dt.UTC)


def test_interval_next_run() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=3600)
    last = dt.datetime(2026, 10, 6, 1, 0, tzinfo=dt.UTC)
    assert next_run_at(_rec(spec, last_fired_at=last), NOW) == last + dt.timedelta(hours=1)
    # Never fired: the next beat tick (on the minute).
    assert next_run_at(_rec(spec), NOW) == dt.datetime(2026, 10, 6, 1, 31, tzinfo=dt.UTC)


def test_one_shots() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.DEADLINE, fire_at_iso="2026-10-06T12:00:00Z",
                       deadline_warning_seconds=3600)
    assert next_run_at(_rec(spec), NOW) == dt.datetime(2026, 10, 6, 11, 0, tzinfo=dt.UTC)
    # Fired: no next run (the column holds 9999-01-01).
    assert next_run_at(_rec(spec, last_fired_at=NOW), NOW) is None
    once = TriggerSpec(trigger_type=TriggerType.ONCE, fire_at_iso="2026-10-05T12:00:00")
    assert next_run_at(_rec(once), NOW) == dt.datetime(2026, 10, 6, 1, 31, tzinfo=dt.UTC)


def test_paused_and_event_armed_have_no_scheduled_next_run() -> None:
    cron = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="* * * * *")
    assert next_run_at({**_rec(cron), "paused": True}, NOW) is None
    rel = TriggerSpec(trigger_type=TriggerType.RELATIVE_DELAY, event_channel="c",
                      relative_offset_seconds=60)
    assert next_run_at(_rec(rel), NOW) is None


@pytest.mark.parametrize("ttype", [TriggerType.API_POLL, TriggerType.WEBHOOK])
def test_other_types_keep_the_stored_value(ttype: TriggerType) -> None:
    rec = _rec(TriggerSpec(trigger_type=ttype))
    assert next_run_at(rec, NOW) == rec["next_fire_at"]


def test_business_calendar_search_is_bounded_and_fast() -> None:
    import time

    spec = TriggerSpec(trigger_type=TriggerType.BUSINESS_CALENDAR, cron_expression="* * * * *",
                       business_days=[0], holidays=["2026-10-12", "2026-10-19"])
    started = time.monotonic()
    # Mondays only, the next two are holidays -> Monday 2026-10-26 09:00 UTC.
    assert next_run_at(_rec(spec), NOW) == dt.datetime(2026, 10, 26, 9, 0, tzinfo=dt.UTC)
    assert time.monotonic() - started < 0.5
