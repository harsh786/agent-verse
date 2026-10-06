"""B1-5: the missed-run (catch-up) policy of a time trigger is settable and honoured.

The beat replayed every missed cron slot after an outage (at most 60) and the
only switch, ``coalesce_missed_runs``, was not a TriggerSpec field, so no API
or UI could set it. ``catch_up`` is now part of the spec:

* ``all`` (default): every slot missed since the last fire, oldest first, at
  most the 60 most recent; a one-shot fires late once.
* ``latest``: only the most recent missed slot; a one-shot fires late once.
* ``none``: a slot more than ``_ON_TIME_GRACE_SECONDS`` late is skipped; a
  one-shot whose instant passed by more than that never fires.

Interval triggers never replay (they fire their current slot), so the policy
does not change them.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.scaling import tasks
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import apply_config_to_spec, spec_config
from app.triggers.validation import validate_spec

NOW = dt.datetime(2026, 10, 6, 10, 3, 20)  # the beat came back after ~4 minutes


def _cron(policy: str | None, **extra: Any) -> dict[str, Any]:
    sched: dict[str, Any] = {
        "trigger_type": "cron",
        "cron_expression": "* * * * *",
        "last_fired_at": "2026-10-06T09:59:00",
        "armed_at": "2026-10-01T00:00:00",
        **extra,
    }
    if policy is not None:
        sched["catch_up"] = policy
    return sched


def _due(sched: dict[str, Any], now: dt.datetime = NOW) -> list[dt.datetime]:
    return tasks._apply_catch_up(sched, tasks._time_trigger_slots(sched, now), now)[0]


def _d(*args: int) -> dt.datetime:
    return dt.datetime(*args)


def test_all_replays_every_missed_slot_by_default() -> None:
    expected = [_d(2026, 10, 6, 10, m) for m in range(4)]
    assert _due(_cron(None)) == expected
    assert _due(_cron("all")) == expected


def test_latest_fires_only_the_most_recent_missed_slot() -> None:
    assert _due(_cron("latest")) == [_d(2026, 10, 6, 10, 3)]
    # The legacy flag means the same thing.
    assert _due(_cron(None, coalesce_missed_runs=True)) == [_d(2026, 10, 6, 10, 3)]


def test_none_skips_late_slots_and_reports_them() -> None:
    sched = _cron("none")
    now = _d(2026, 10, 6, 10, 3, 40)
    slots = tasks._time_trigger_slots(sched, now)
    fire, skipped = tasks._apply_catch_up(sched, slots, now)
    assert fire == [_d(2026, 10, 6, 10, 3)]  # 40 s late: on time
    assert skipped == [_d(2026, 10, 6, 10, m) for m in range(3)]  # >= 100 s late


def test_none_on_a_normal_tick_fires_the_slot() -> None:
    sched = _cron("none", last_fired_at="2026-10-06T10:02:00")
    assert _due(sched, _d(2026, 10, 6, 10, 3, 55)) == [_d(2026, 10, 6, 10, 3)]


@pytest.mark.parametrize("policy", ["all", "latest"])
def test_a_one_shot_missed_during_an_outage_fires_late_once(policy: str) -> None:
    once = {"trigger_type": "once", "fire_at_iso": "2026-10-06T09:30:00", "catch_up": policy}
    assert _due(once) == [_d(2026, 10, 6, 9, 30)]


def test_a_one_shot_with_none_never_fires_late_and_is_not_reloaded() -> None:
    once = {"trigger_type": "once", "fire_at_iso": "2026-10-06T09:30:00", "catch_up": "none"}
    assert _due(once) == []
    assert tasks._next_evaluation_at(once, NOW) == tasks._NEVER
    on_time = {"trigger_type": "once", "fire_at_iso": "2026-10-06T10:02:30", "catch_up": "none"}
    assert _due(on_time) == [_d(2026, 10, 6, 10, 2, 30)]


def test_interval_is_not_changed_by_the_policy() -> None:
    sched = {
        "trigger_type": "interval",
        "interval_seconds": 3600,
        "last_fired_at": "2026-10-06T08:00:00",
        "catch_up": "none",
    }
    assert _due(sched) == [_d(2026, 10, 6, 10, 0)]


@pytest.mark.parametrize("policy", ["all", "latest", "none"])
def test_valid_policies_are_accepted_and_persisted(policy: str) -> None:
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *",
                       catch_up=policy)
    validate_spec(spec, plan="enterprise")
    restored = TriggerSpec(trigger_type=TriggerType.CRON)
    apply_config_to_spec(restored, spec_config(spec))
    assert restored.catch_up == policy


def test_an_unknown_policy_is_refused() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *",
                       catch_up="sometimes")
    with pytest.raises(ValueError, match="catch_up"):
        validate_spec(spec, plan="enterprise")
