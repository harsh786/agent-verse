"""TRG-15: the beat loads only due schedules from Postgres and maintains next_fire_at.

Each tick SCANned every schedule:* Redis key (a GET each) and, with DB
discovery on, read every schedule of every tenant; DB discovery was off by
default, so an evicted Redis key silently stopped a schedule firing.
(The indexed due query itself is exercised on Postgres in
tests/triggers/test_trigger_persistence_integration.py.)
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.scaling import tasks

NOW = dt.datetime(2026, 9, 30, 12, 0, 30)  # UTC-naive, as the beat uses


def _utc(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("sched", "expected"),
    [
        ({"trigger_type": "cron", "cron_expression": "*/15 * * * *"}, _utc(2026, 9, 30, 12, 15)),
        (
            {"trigger_type": "cron", "cron_expression": "0 9 * * *", "timezone": "Asia/Kolkata"},
            _utc(2026, 10, 1, 3, 30),
        ),
        (
            {"trigger_type": "business_calendar", "cron_expression": "0 13 * * *"},
            _utc(2026, 9, 30, 13, 0),
        ),
        (
            {
                "trigger_type": "interval",
                "interval_seconds": 3600,
                "last_fired_at": "2026-09-30T12:00:00",
            },
            _utc(2026, 9, 30, 13, 0),
        ),
        ({"trigger_type": "interval", "interval_seconds": 3600}, None),  # never fired: now
        ({"trigger_type": "once", "fire_at_iso": "2026-10-02T08:00:00Z"}, _utc(2026, 10, 2, 8)),
        (
            {
                "trigger_type": "once",
                "fire_at_iso": "2026-09-01T08:00:00Z",
                "last_fired_at": "2026-09-01T08:00:00",
            },
            tasks._NEVER,
        ),
        (
            {
                "trigger_type": "relative_delay",
                "fire_at_iso": "2026-10-02T08:00:00",
                "relative_offset_seconds": 600,
            },
            _utc(2026, 10, 2, 8, 10),
        ),
        (
            {
                "trigger_type": "deadline",
                "fire_at_iso": "2026-10-02T08:00:00",
                "deadline_warning_seconds": 3600,
            },
            _utc(2026, 10, 2, 7, 0),
        ),
        # TRG-54: polling types are evaluated once per poll interval, not every tick.
        ({"trigger_type": "api_poll", "poll_url": "https://x"}, _utc(2026, 9, 30, 12, 1, 30)),
        ({"trigger_type": "file_drop"}, None),
    ],
)
def test_next_evaluation_at(sched: dict[str, Any], expected: dt.datetime | None) -> None:
    assert tasks._next_evaluation_at(sched, NOW) == expected


def test_discovery_is_postgres_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    assert tasks._db_schedule_discovery_enabled() is True
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "false")
    assert tasks._db_schedule_discovery_enabled() is False


class _NoScanRedis:
    """A Redis whose schedule mirror was evicted — and must not be scanned."""

    def scan_iter(self, **_k: Any) -> Any:
        raise AssertionError("the beat must not SCAN schedule:* when Postgres answered")

    def get(self, _key: str) -> None:
        return None

    def set(self, *_a: Any, **_k: Any) -> bool:
        return True


def test_due_db_schedule_fires_without_its_redis_key_and_records_next_fire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    due = {
        "schedule:t1:s1": {
            "schedule_id": "s1",
            "tenant_id": "t1",
            "trigger_type": "cron",
            "cron_expression": "* * * * *",
            "timezone": "UTC",
            "goal_template": "Report",
            "paused": False,
            "last_fired_at": None,
            "next_fire_at": None,
        }
    }
    sent: list[dict[str, Any]] = []
    persisted: list[Any] = []

    async def fake_load(now: Any = None) -> Any:
        return {k: dict(v) for k, v in due.items()}

    async def fake_last_fired(*_a: Any, **_k: Any) -> None:
        return None

    async def fake_persist(updates: Any) -> None:
        persisted.extend(updates)

    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "true")
    monkeypatch.setenv("REDIS_URL", "redis://fake")
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: _NoScanRedis())
    monkeypatch.setattr(tasks, "_load_db_schedules", fake_load)
    monkeypatch.setattr(tasks, "_update_db_schedule_last_fired_at", fake_last_fired)
    monkeypatch.setattr(tasks, "_persist_next_evaluations", fake_persist)
    monkeypatch.setattr(
        tasks.run_scheduled_goal, "apply_async", lambda *, kwargs, queue: sent.append(kwargs)
    )

    result = tasks.fire_due_schedules()

    assert result["schedules_fired"] == 1
    assert [k["schedule_id"] for k in sent] == ["schedule:t1:s1"]
    [(tenant_id, schedule_id, nxt)] = persisted
    assert (tenant_id, schedule_id) == ("t1", "s1")
    assert nxt is not None and nxt > dt.datetime.now(dt.UTC)


def test_db_outage_falls_back_to_the_redis_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    class _Mirror(_NoScanRedis):
        def scan_iter(self, **_k: Any) -> Any:
            return ["schedule:t1:s1"]

        def get(self, key: str) -> Any:
            if key != "schedule:t1:s1":
                return None
            return json.dumps(
                {
                    "schedule_id": "s1",
                    "tenant_id": "t1",
                    "trigger_type": "interval",
                    "interval_seconds": 60,
                    "goal_template": "Report",
                    "paused": False,
                }
            )

    async def failing_load(now: Any = None) -> None:
        return None

    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "true")
    monkeypatch.setenv("REDIS_URL", "redis://fake")
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: _Mirror())
    monkeypatch.setattr(tasks, "_load_db_schedules", failing_load)
    monkeypatch.setattr(
        tasks.run_scheduled_goal, "apply_async", lambda *, kwargs, queue: sent.append(kwargs)
    )

    assert tasks.fire_due_schedules()["schedules_fired"] == 1


# ── GAP-WORKER: the mirror fallback never replays a cron's history ────────────
# Live (2026-10-06 10:11, Postgres restarting): discovery failed, the beat read
# the Redis mirror, whose payload had no last_fired_at, so every daily 09:00 cron
# fired each slot since it was created (armed_at, 18 days) - ~20 goals per
# schedule in one second.


def _daily_cron_mirror(**extra: Any) -> dict[str, Any]:
    return {
        "schedule_id": "s1",
        "tenant_id": "t1",
        "trigger_type": "cron",
        "cron_expression": "0 9 * * *",
        "timezone": "UTC",
        "goal_template": "daily standup",
        "paused": False,
        "armed_at": "2026-09-18T07:43:54+00:00",
        **extra,
    }


class _MirrorOf(_NoScanRedis):
    def __init__(self, payload: dict[str, Any]) -> None:
        import json

        self.values = {"schedule:t1:s1": json.dumps(payload)}
        self.writes: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def scan_iter(self, **_k: Any) -> Any:
        return list(self.values)

    def get(self, key: str) -> Any:
        return self.values.get(key)

    def set(self, key: str, value: str, **kwargs: Any) -> bool:
        import json

        self.writes.append((key, json.loads(value), kwargs))
        self.values[key] = value
        return True


def _fire_with_mirror(
    monkeypatch: pytest.MonkeyPatch, redis: Any, *, db: Any = None
) -> list[dict[str, Any]]:
    async def load(now: Any = None) -> Any:
        return db

    async def fake_last_fired(*_a: Any, **_k: Any) -> None:
        return None

    async def fake_persist(_updates: Any) -> None:
        return None

    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "true")
    monkeypatch.setenv("REDIS_URL", "redis://fake")
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: redis)
    monkeypatch.setattr(tasks, "_load_db_schedules", load)
    monkeypatch.setattr(tasks, "_update_db_schedule_last_fired_at", fake_last_fired)
    monkeypatch.setattr(tasks, "_persist_next_evaluations", fake_persist)
    monkeypatch.setattr(
        tasks.run_scheduled_goal, "apply_async", lambda *, kwargs, queue: sent.append(kwargs)
    )
    tasks.fire_due_schedules()
    return sent


def test_mirror_fallback_fires_only_the_latest_cron_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _MirrorOf(_daily_cron_mirror())
    sent = _fire_with_mirror(monkeypatch, redis)

    assert len(sent) == 1  # not one per day since 2026-09-18
    today_9 = dt.datetime.now(dt.UTC).replace(tzinfo=None, hour=9, minute=0, second=0,
                                               microsecond=0)
    if today_9 > dt.datetime.now(dt.UTC).replace(tzinfo=None):
        today_9 -= dt.timedelta(days=1)
    assert sent[0]["fire_instance_id"] == today_9.isoformat()


def test_cap_keeps_interval_and_one_shot_slots() -> None:
    slots = [dt.datetime(2026, 10, d, 9) for d in (4, 5, 6)]
    assert tasks._cap_mirror_fallback_slots({"trigger_type": "cron"}, slots) == (
        slots[-1:],
        slots[:-1],
    )
    assert tasks._cap_mirror_fallback_slots(
        {"trigger_type": "business_calendar"}, slots
    )[0] == slots[-1:]
    for kind in ("interval", "once"):
        assert tasks._cap_mirror_fallback_slots({"trigger_type": kind}, slots[:1]) == (
            slots[:1],
            [],
        )


def test_db_fire_keeps_the_mirror_last_fired_at_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mirror = _daily_cron_mirror(cron_expression="* * * * *")
    redis = _MirrorOf(mirror)
    db = {"schedule:t1:s1": {**mirror, "last_fired_at": None, "next_fire_at": None}}

    sent = _fire_with_mirror(monkeypatch, redis, db=db)

    assert sent  # the DB copy is authoritative: its catch-up is unchanged
    (key, payload, kwargs) = redis.writes[-1]
    assert key == "schedule:t1:s1"
    assert payload["last_fired_at"] == sent[-1]["fire_instance_id"]
    assert kwargs == {"xx": True}  # never re-creates an evicted mirror
