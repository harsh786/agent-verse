"""Regression coverage for a duplicate-fire bug in INTERVAL trigger schedules.

``fire_due_schedules`` (app/scaling/tasks.py) derives each firing's idempotency
key from ``fire_instance_id`` (passed through to
``app.triggers.dedup.derive_idempotency_key`` as ``scheduled_fire_time``, then
checked atomically against Redis by ``TriggerDispatcher._is_duplicate``). Every
other time-based trigger type (cron, rrule, business_calendar, once,
relative_delay, deadline, solar) derives this key from a deterministic slot
computed from the schedule's own state (last_fired_at + cron/rrule math), so
two concurrent evaluations of the same due schedule compute the *same* key and
the second is deduped.

INTERVAL schedules used ``fire_instance_id=now.isoformat()`` — the wall-clock
time the *evaluating process* happened to read, not a deterministic property
of the schedule. Two overlapping executions of ``fire_due_schedules`` (the
``beat_task_guard`` Redis lock expiring under load while the previous run is
still in flight, or a Celery retry racing the original attempt) each capture
their own ``now`` a few milliseconds apart, so they derive *different*
idempotency keys and the dedup check never sees a collision — the same
interval firing is dispatched twice, double-executing the scheduled goal.

These tests drive the real ``fire_due_schedules`` task body twice against the
same (never-advanced) Redis snapshot, standing in for two racing
executions that both observe the schedule as due before either has written
back ``last_fired_at``.
"""

from __future__ import annotations

import datetime
import json
from unittest.mock import MagicMock, patch

import pytest

from app.scaling.tasks import _interval_due_slot_utc


def _dt(y: int, mo: int, d: int, h: int, mi: int, s: int = 0, us: int = 0) -> datetime.datetime:
    return datetime.datetime(y, mo, d, h, mi, s, us)


# ── _interval_due_slot_utc (pure helper) ──────────────────────────────────────


def test_interval_never_fired_is_due_and_returns_a_slot() -> None:
    slot = _interval_due_slot_utc(60, None, _dt(2026, 1, 1, 0, 0, 30))
    assert slot == _dt(2026, 1, 1, 0, 0, 0)


def test_interval_not_yet_due_returns_none() -> None:
    slot = _interval_due_slot_utc(60, _dt(2026, 1, 1, 0, 0, 0), _dt(2026, 1, 1, 0, 0, 30))
    assert slot is None


def test_interval_due_returns_epoch_aligned_slot() -> None:
    slot = _interval_due_slot_utc(60, _dt(2026, 1, 1, 0, 0, 0), _dt(2026, 1, 1, 0, 1, 5))
    assert slot == _dt(2026, 1, 1, 0, 1, 0)


def test_interval_slot_is_identical_for_two_nearby_now_values() -> None:
    """The whole point of bucketing: two evaluations a few ms apart (simulating
    two racing processes) that fall in the same interval bucket must agree on
    the slot, and therefore on the derived idempotency key."""
    now_a = _dt(2026, 1, 1, 0, 5, 0, us=100_000)
    now_b = _dt(2026, 1, 1, 0, 5, 0, us=900_000)
    slot_a = _interval_due_slot_utc(300, _dt(2026, 1, 1, 0, 0, 0), now_a)
    slot_b = _interval_due_slot_utc(300, _dt(2026, 1, 1, 0, 0, 0), now_b)
    assert slot_a == slot_b == _dt(2026, 1, 1, 0, 5, 0)


# ── End-to-end: two racing fire_due_schedules() executions ───────────────────


def _redis_mock(payload: dict) -> MagicMock:
    """A frozen Redis snapshot: ``set`` is a no-op, so calling ``.run()`` against
    this mock twice models two processes that both read the schedule as due
    before either one's write-back (mark_schedule_fired) has landed."""
    mock_r = MagicMock()
    mock_r.scan_iter = MagicMock(return_value=["schedule:t1:interval1"])
    mock_r.get = MagicMock(return_value=json.dumps(payload))
    mock_r.set = MagicMock()
    mock_r.delete = MagicMock()
    return mock_r


@pytest.fixture(autouse=True)
def _redis_url_env(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")


class TestConcurrentIntervalFiring:
    def _run_twice_and_collect_fire_instance_ids(self) -> tuple[str, str]:
        payload = {
            "trigger_type": "interval",
            "interval_seconds": 300,
            "tenant_id": "t1",
            "goal_template": "check the queue depth",
            "last_fired_at": None,
        }
        fire_instance_ids: list[str] = []

        for _ in range(2):
            mock_r = _redis_mock(payload)
            with (
                patch("redis.from_url", return_value=mock_r),
                patch("app.scaling.tasks._db_schedule_discovery_enabled", return_value=False),
                patch("app.scaling.tasks.run_scheduled_goal.apply_async") as mock_apply,
            ):
                from app.scaling.tasks import fire_due_schedules

                result = fire_due_schedules.run()
                assert result["schedules_fired"] == 1
                fire_instance_ids.append(mock_apply.call_args.kwargs["kwargs"]["fire_instance_id"])

        return fire_instance_ids[0], fire_instance_ids[1]

    def test_two_racing_fires_derive_the_same_idempotency_slot(self) -> None:
        """Two 'concurrent' evaluations of the same never-fired interval schedule
        (neither has seen the other's write-back yet) MUST derive the same
        ``fire_instance_id`` so the dispatcher's dedup check can catch the
        duplicate. Before the fix this used ``now.isoformat()`` — a fresh,
        virtually-never-repeating wall-clock string on each call — so the two
        calls in this test produced two DIFFERENT ids and the assertion below
        failed (proving the double-fire); after the fix both derive the same
        epoch-aligned slot."""
        first, second = self._run_twice_and_collect_fire_instance_ids()
        assert first == second
