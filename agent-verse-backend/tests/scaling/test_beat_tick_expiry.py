"""GAP-WORKER: a beat tick expires before the next one is sent.

Live (2026-10-06): the goal worker's two slots were held by goals for ~2 h while
the beat kept enqueueing its periodic ticks (~2,500/h on ``maintenance``: the
coordination outbox every 5 s, audit WAL / SIEM every 10 s, MCP health, queue
depths, A2A, civilizations, event outbox every 30 s ...). ``maintenance`` grew to
~4,700 stale ticks, which then ran back to back (the same no-op sweep hundreds of
times) while goals waited behind them. A periodic tick is only useful until its
successor is sent, so every beat entry carries an ``expires`` below its period.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from celery.schedules import crontab, schedule

from app.scaling.celery_app import (
    apply_beat_tick_expiry,
    beat_period_seconds,
    beat_tick_expires,
    celery_app,
)


def _loaded_beat_schedule() -> dict[str, Any]:
    # app.scaling.tasks registers more entries (HITL expiry, memory / feedback /
    # org-intelligence crons) at import time.
    celery_app.loader.import_default_modules()
    return dict(celery_app.conf.beat_schedule)


def test_every_beat_entry_expires_before_its_next_tick() -> None:
    entries = _loaded_beat_schedule()
    assert len(entries) > 40
    for name, entry in entries.items():
        period = beat_period_seconds(entry["schedule"])
        assert period is not None and period > 0, f"{name}: period not derivable"
        expires = (entry.get("options") or {}).get("expires")
        assert expires is not None, f"{name}: a stale tick would queue behind newer ones"
        assert 0 < float(expires) < period, f"{name}: expires {expires} >= period {period}"


def test_the_high_rate_maintenance_ticks_expire_within_their_period() -> None:
    entries = _loaded_beat_schedule()
    outbox = entries["dispatch-coordination-outbox"]
    assert outbox["options"]["queue"] == "maintenance"
    assert outbox["options"]["expires"] == 4.5  # every 5 s
    assert entries["flush-audit-wal"]["options"]["expires"] == 9.0  # every 10 s
    assert entries["mcp-health-check-every-30s"]["options"]["expires"] == 27.0
    # Entries registered in app.scaling.tasks are covered too.
    assert entries["expire-hitl-approvals-every-60s"]["options"]["expires"] == 54.0


def test_explicit_expiries_are_kept() -> None:
    entries = _loaded_beat_schedule()
    assert entries["fire-due-schedules-every-60s"]["options"]["expires"] == 14
    assert entries["workflow-fire-due-schedules"]["options"]["expires"] == 55


@pytest.mark.parametrize(
    ("sched", "period"),
    [
        (5.0, 5.0),
        (3600, 3600.0),
        (timedelta(minutes=2), 120.0),
        (schedule(run_every=timedelta(seconds=45)), 45.0),
        (crontab(minute="0"), 3600.0),
        (crontab(hour=3, minute=0), 86_400.0),
        # */9 runs at :00 :09 ... :54, then :00 again - the shortest gap is 6 min.
        (crontab(minute="*/9"), 360.0),
        # minute defaults to "*": every minute of 02:00-02:59 on the 1st.
        (crontab(day_of_month="1", hour="2"), 60.0),
    ],
)
def test_period_is_the_shortest_gap_between_runs(sched: Any, period: float) -> None:
    assert beat_period_seconds(sched) == period


def test_monthly_partition_job_runs_once_not_sixty_times() -> None:
    """crontab(day_of_month="1", hour="2") fired every minute of that hour."""
    entry = _loaded_beat_schedule()["create-guardrail-partitions"]
    assert beat_period_seconds(entry["schedule"]) > 86_400


def test_unknown_schedules_get_no_expiry() -> None:
    assert beat_tick_expires(object()) is None
    assert beat_tick_expires(0) is None
    entries = {"x": {"task": "t", "schedule": object(), "options": {"queue": "q"}}}
    apply_beat_tick_expiry(entries)
    assert entries["x"]["options"] == {"queue": "q"}


def test_apply_adds_expiry_without_dropping_options() -> None:
    entries: dict[str, Any] = {
        "a": {"task": "t", "schedule": 30.0, "options": {"queue": "maintenance"}},
        "b": {"task": "t", "schedule": 60.0},
        "c": {"task": "t", "schedule": 60.0, "options": {"expires": 5}},
    }
    apply_beat_tick_expiry(entries)
    assert entries["a"]["options"] == {"queue": "maintenance", "expires": 27.0}
    assert entries["b"]["options"] == {"expires": 54.0}
    assert entries["c"]["options"] == {"expires": 5}


def test_the_beat_scheduler_adds_an_expiry_to_an_entry_without_one() -> None:
    """Safety net for an entry added at runtime (e.g. through RedBeat)."""
    from app.scaling.beat_scheduler import AgentVerseRedBeatScheduler

    entry = SimpleNamespace(options={"queue": "maintenance"}, schedule=schedule(30.0))
    sent: list[dict[str, Any]] = []
    with patch(
        "redbeat.RedBeatScheduler.apply_async",
        lambda self, e, producer=None, advance=True, **kw: sent.append(dict(e.options)),
    ):
        AgentVerseRedBeatScheduler.apply_async(object.__new__(AgentVerseRedBeatScheduler), entry)
    assert sent == [{"queue": "maintenance", "ignore_result": True, "expires": 27.0}]
