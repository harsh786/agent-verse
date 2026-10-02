"""TRG-54: polling triggers are fetched by worker tasks, never inside the beat.

rss_feed / api_poll / db_row_change did blocking (10s) HTTP / DB fetches one
after another inside ``fire_due_schedules``: many or slow feeds starved every
other schedule and could outlive the beat guard lock. The beat now only claims
a due poll (atomic per-trigger SET NX) and enqueues one ``poll_trigger`` task on
the ``triggers.poll`` queue; polling types get a ``next_fire_at`` one interval
ahead so they are not loaded every tick.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import fakeredis
import pytest

from app.scaling import tasks
from app.scaling.celery_app import celery_app

NOW = dt.datetime(2026, 10, 2, 12, 0, 30)


def _sched(sid: str, trigger_type: str, **extra: Any) -> dict[str, Any]:
    return {
        "schedule_id": sid,
        "tenant_id": "t1",
        "trigger_type": trigger_type,
        "goal_template": "Check",
        "paused": False,
        **extra,
    }


def _run_beat(monkeypatch: pytest.MonkeyPatch, scheds: list[dict[str, Any]]) -> tuple[Any, list]:
    r = fakeredis.FakeRedis(decode_responses=True)
    for s in scheds:
        r.set(f"schedule:t1:{s['schedule_id']}", json.dumps(s))
    enqueued: list[dict[str, Any]] = []
    fired: list[dict[str, Any]] = []

    def _no_network(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the beat must not fetch")

    monkeypatch.setenv("REDIS_URL", "redis://fake")
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "false")
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: r)
    monkeypatch.setattr("app.triggers.polling.fetch_json", _no_network)
    monkeypatch.setattr("app.triggers.rss.fetch_rss_entries", _no_network)
    monkeypatch.setattr(tasks, "_count_tenant_rows", _no_network)
    monkeypatch.setattr(
        tasks.poll_trigger,
        "apply_async",
        lambda *, kwargs, queue: enqueued.append({"queue": queue, **kwargs}),
    )
    monkeypatch.setattr(
        tasks.run_scheduled_goal, "apply_async", lambda *, kwargs, queue: fired.append(kwargs)
    )
    r.delete("beat_guard:fire_due_schedules")
    result = tasks.fire_due_schedules()
    return result, [*enqueued, {"_cron_fired": fired, "_result": result}]


def test_beat_enqueues_one_poll_task_per_trigger_and_does_no_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheds = [
        _sched("rss", "rss_feed", rss_url="https://feeds.example/rss"),
        _sched("api", "api_poll", poll_url="https://status.example/api", poll_interval_seconds=60),
        _sched("db", "db_row_change", db_table="goal_events"),
        _sched("cron", "cron", cron_expression="* * * * *", timezone="UTC"),
    ]
    result, out = _run_beat(monkeypatch, scheds)
    polls = [o for o in out if "queue" in o]
    assert sorted(p["sched"]["schedule_id"] for p in polls) == ["api", "db", "rss"]
    assert {p["queue"] for p in polls} == {"triggers.poll"}
    assert result["polls_enqueued"] == 3
    # The cron schedule still fired in the same tick (no slow feed in the way).
    assert result["schedules_fired"] == 1


def test_a_claimed_poll_is_not_enqueued_again_by_another_beat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = fakeredis.FakeRedis(decode_responses=True)
    sent: list[str] = []
    monkeypatch.setattr(
        tasks.poll_trigger, "apply_async", lambda *, kwargs, queue: sent.append(kwargs["key"])
    )
    sched = _sched("api", "api_poll", poll_url="https://x.example", poll_interval_seconds=300)
    # Two beat replicas evaluate the same due trigger.
    assert tasks._enqueue_poll_trigger("schedule:t1:api", sched, r, NOW) is True
    assert tasks._enqueue_poll_trigger("schedule:t1:api", sched, r, NOW) is False
    assert sent == ["schedule:t1:api"]
    assert 0 < r.ttl("api_poll_claim:schedule:t1:api") <= 295


def test_failed_enqueue_releases_the_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    r = fakeredis.FakeRedis(decode_responses=True)

    def _broker_down(**_k: Any) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.poll_trigger, "apply_async", _broker_down)
    sched = _sched("rss", "rss_feed", rss_url="https://x.example/rss")
    assert tasks._enqueue_poll_trigger("schedule:t1:rss", sched, r, NOW) is False
    assert r.get("poll_claim:schedule:t1:rss") is None  # next tick retries


def test_poll_trigger_task_fetches_and_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    r = fakeredis.FakeRedis(decode_responses=True)
    dispatched: list[dict[str, Any]] = []
    monkeypatch.setenv("REDIS_URL", "redis://fake")
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: r)
    monkeypatch.setattr("app.triggers.polling.fetch_json", lambda *_a, **_k: {"status": "down"})
    monkeypatch.setattr(
        tasks.run_scheduled_goal,
        "apply_async",
        lambda *, kwargs, queue: dispatched.append(kwargs),
    )
    sched = _sched("api", "api_poll", poll_url="https://x.example", poll_jsonpath="status")
    out = tasks.poll_trigger.run(key="schedule:t1:api", sched=sched)
    assert out["fired"] == 1
    assert dispatched[0]["trigger_type"] == "api_poll"
    assert r.get("api_poll_last:schedule:t1:api") == "down"


def test_poll_task_is_routed_to_its_own_queue() -> None:
    route = celery_app.amqp.router.route({}, "app.scaling.tasks.poll_trigger")
    queue = route.get("queue")
    assert getattr(queue, "name", queue) == "triggers.poll"


@pytest.mark.parametrize(
    ("sched", "seconds"),
    [
        ({"trigger_type": "api_poll", "poll_interval_seconds": 300}, 300),
        ({"trigger_type": "api_poll", "poll_interval_seconds": 10}, 60),
        ({"trigger_type": "rss_feed"}, 60),
        ({"trigger_type": "db_row_change"}, 60),
    ],
)
def test_polling_types_are_not_loaded_every_tick(sched: dict[str, Any], seconds: int) -> None:
    nxt = tasks._next_evaluation_at(sched, NOW)
    assert nxt == NOW.replace(tzinfo=dt.UTC) + dt.timedelta(seconds=seconds)
