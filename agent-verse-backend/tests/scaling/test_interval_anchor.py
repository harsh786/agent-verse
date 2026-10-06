"""B1-17: interval fires are at least one interval apart.

Live (TIME-INTERVAL with the 15 s tick): an every-60 s interval fired 29.9 s
and then 60.2 s apart. Slots were epoch-aligned: a schedule created at
04:42:28 fired its 04:42:00 slot at once and the 04:43:00 slot 30 s later; an
hourly schedule created at 10:40 would fire at 10:40 and again at 11:00, and
a free tenant's 15-minute floor could be undercut the same way. Slots are now
anchored at the schedule's arming time (created / resumed / edited): the
first fire is on the next tick, then exactly every interval from it.
"""

from __future__ import annotations

import datetime as dt

from app.scaling import tasks


def _d(*a: int) -> dt.datetime:
    return dt.datetime(*a)


def _slots(last: str | None, now: dt.datetime) -> list[dt.datetime]:
    sched = {"trigger_type": "interval", "interval_seconds": 3600,
             "armed_at": "2026-10-06T10:40:12", "last_fired_at": last}
    return tasks._time_trigger_slots(sched, now)


def test_the_first_fire_is_at_arming_then_every_interval_from_it() -> None:
    assert _slots(None, _d(2026, 10, 6, 10, 40, 15)) == [_d(2026, 10, 6, 10, 40, 12)]
    # Not at the top of the hour: one full interval after the first fire.
    assert _slots("2026-10-06T10:40:12", _d(2026, 10, 6, 11, 0, 30)) == []
    assert _slots("2026-10-06T10:40:12", _d(2026, 10, 6, 11, 40, 20)) == [
        _d(2026, 10, 6, 11, 40, 12)
    ]


def test_an_outage_fires_the_current_slot_once_not_a_backlog() -> None:
    assert _slots("2026-10-06T10:40:12", _d(2026, 10, 6, 15, 5, 0)) == [
        _d(2026, 10, 6, 14, 40, 12)
    ]


def test_two_concurrent_evaluations_pick_the_same_slot() -> None:
    a = _slots("2026-10-06T10:40:12", _d(2026, 10, 6, 11, 40, 13))
    b = _slots("2026-10-06T10:40:12", _d(2026, 10, 6, 11, 40, 59))
    assert a == b == [_d(2026, 10, 6, 11, 40, 12)]


def test_without_an_anchor_the_epoch_slot_is_kept() -> None:
    sched = {"trigger_type": "interval", "interval_seconds": 60}
    assert tasks._time_trigger_slots(sched, _d(2026, 10, 6, 0, 0, 30)) == [_d(2026, 10, 6, 0, 0)]
