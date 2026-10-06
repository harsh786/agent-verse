"""B1-1: a time trigger never fires a slot from before it was (re)armed.

Live (2026-10-06): a weekday-9am cron created at 00:08 UTC fired at once for the
previous day's 09:00 slot, a cron for 05:41 IST created at 05:38 IST fired for
the previous day's 05:41, and a resumed schedule replayed every slot of its
pause. The beat now fires only the slots after
``max(last_fired_at, armed_at)``; ``armed_at`` is the creation time unless the
schedule was resumed or its spec edited since.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any

from app.scaling import tasks

NOW = dt.datetime(2026, 10, 6, 15, 0, 30)  # a Tuesday, UTC-naive as the beat uses


def _d(*args: int) -> dt.datetime:
    return dt.datetime(*args)


def _slots(sched: dict[str, Any], now: dt.datetime = NOW) -> list[dt.datetime]:
    return tasks._time_trigger_slots(sched, now)


def test_new_cron_never_fires_a_slot_from_before_it_was_created() -> None:
    sched = {
        "trigger_type": "cron",
        "cron_expression": "0 9 * * 1-5",
        "armed_at": "2026-10-06T14:58:00+00:00",
    }
    assert _slots(sched) == []


def test_new_cron_created_just_before_a_slot_fires_that_slot() -> None:
    sched = {
        "trigger_type": "cron",
        "cron_expression": "0 15 * * *",
        "armed_at": "2026-10-06T14:59:50",
    }
    assert _slots(sched) == [_d(2026, 10, 6, 15, 0)]


def test_new_cron_in_a_timezone_ignores_yesterdays_slot() -> None:
    # 05:41 IST == 00:11 UTC; created at 00:08 UTC: only today's slot, once due.
    sched = {
        "trigger_type": "cron",
        "cron_expression": "41 5 * * *",
        "timezone": "Asia/Kolkata",
        "armed_at": "2026-10-06T00:08:38",
    }
    assert _slots(sched, _d(2026, 10, 6, 0, 9)) == []
    assert _slots(sched, _d(2026, 10, 6, 0, 11, 20)) == [_d(2026, 10, 6, 0, 11)]


def test_resumed_cron_does_not_replay_the_slots_it_was_paused_for() -> None:
    sched = {
        "trigger_type": "cron",
        "cron_expression": "*/5 * * * *",
        "last_fired_at": "2026-10-06T10:00:00",
        "armed_at": "2026-10-06T14:58:10",  # resumed
    }
    assert _slots(sched) == [_d(2026, 10, 6, 15, 0)]


def test_a_beat_outage_after_arming_still_replays_every_missed_slot() -> None:
    sched = {
        "trigger_type": "cron",
        "cron_expression": "*/5 * * * *",
        "last_fired_at": "2026-10-06T14:40:00",
        "armed_at": "2026-10-01T08:00:00",
    }
    assert _slots(sched) == [
        _d(2026, 10, 6, 14, 45),
        _d(2026, 10, 6, 14, 50),
        _d(2026, 10, 6, 14, 55),
        _d(2026, 10, 6, 15, 0),
    ]


def test_a_schedule_without_any_floor_keeps_the_single_latest_slot() -> None:
    # Redis-mirror payloads written before armed_at existed.
    sched = {"trigger_type": "cron", "cron_expression": "0 9 * * 1-5"}
    assert _slots(sched) == [_d(2026, 10, 6, 9, 0)]


def test_business_calendar_new_schedule_has_the_same_floor() -> None:
    sched = {
        "trigger_type": "business_calendar",
        "cron_expression": "0 10 * * *",
        "armed_at": "2026-10-06T14:00:00",
    }
    assert _slots(sched) == []
    sched["armed_at"] = "2026-10-06T09:59:00"
    assert _slots(sched) == [_d(2026, 10, 6, 10, 0)]


def test_interval_fires_its_current_slot_once_then_waits() -> None:
    sched: dict[str, Any] = {
        "trigger_type": "interval",
        "interval_seconds": 60,
        "armed_at": "2026-10-06T15:00:10",
    }
    # B1-17: slots are anchored at arming time, not the epoch minute.
    assert _slots(sched) == [_d(2026, 10, 6, 15, 0, 10)]
    sched["last_fired_at"] = "2026-10-06T15:00:10"
    assert _slots(sched) == []


def test_one_shots_fire_once_at_their_time() -> None:
    once = {"trigger_type": "once", "fire_at_iso": "2026-10-06T15:00:00Z"}
    assert _slots(once) == [_d(2026, 10, 6, 15, 0)]
    assert _slots(once, _d(2026, 10, 6, 14, 59, 59)) == []
    once["last_fired_at"] = "2026-10-06T15:00:00"
    assert _slots(once) == []
    rel = {
        "trigger_type": "relative_delay",
        "fire_at_iso": "2026-10-06T14:58:00",
        "relative_offset_seconds": 120,
    }
    assert _slots(rel) == [_d(2026, 10, 6, 15, 0)]
    deadline = {
        "trigger_type": "deadline",
        "fire_at_iso": "2026-10-06T16:00:00",
        "deadline_warning_seconds": 3600,
    }
    assert _slots(deadline) == [_d(2026, 10, 6, 15, 0)]


def test_db_payload_carries_the_floor_falling_back_to_created_at() -> None:
    created = dt.datetime(2026, 10, 6, 0, 8, 38, tzinfo=dt.UTC)
    row = SimpleNamespace(
        id="s1",
        tenant_id="t1",
        goal_id_template="g",
        agent_id=None,
        trigger_type="cron",
        cron_expression="0 9 * * 1-5",
        timezone="UTC",
        interval_seconds=0,
        config={},
        paused=False,
        created_at=created,
        armed_at=None,
        last_fired_at=None,
        next_fire_at=None,
    )
    assert tasks._db_schedule_payload(row)["armed_at"] == "2026-10-06T00:08:38"
    row.armed_at = dt.datetime(2026, 10, 6, 9, 0, tzinfo=dt.UTC)
    assert tasks._db_schedule_payload(row)["armed_at"] == "2026-10-06T09:00:00"
