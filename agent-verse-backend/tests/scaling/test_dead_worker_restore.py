"""a06-F099-03: liveness-aware restore of messages left unacked by dead workers.

Unit tests of the decision logic against an in-memory broker state with the
same atomic semantics as the Lua scripts (those run on a real Redis in
``test_dead_worker_restore_redis_integration.py``).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.scaling import dead_worker_restore as dwr

GRACE = 120.0
NOW = 10_000.0


def _message(task: str = "workflow.deliver_workflow_callback", tag: str = "t") -> dict[str, Any]:
    return {
        "body": "e30=",
        "headers": {"task": task, "id": f"id-{tag}"},
        "properties": {"delivery_tag": tag, "delivery_info": {"exchange": "q", "routing_key": "q"}},
    }


class FakeState:
    """In-memory twin of RedisBrokerState (same checks as the Lua scripts)."""

    def __init__(self) -> None:
        self.unacked: dict[str, str] = {}
        self.index: dict[str, float] = {}
        self.owner: dict[str, str] = {}
        self.alive: set[str] = set()
        self.queues: dict[str, list[str]] = {}
        self.restore_calls = 0
        self.before_restore: Any = None

    def router(self, message: dict[str, Any], exchange: str, routing_key: str) -> list[str]:
        return [routing_key] if routing_key else []

    def take(self, tag: str, instance: str | None, *, task: str = "x.task", at: float = 0.0,
             routing_key: str = "maintenance") -> None:
        self.unacked[tag] = json.dumps([_message(task, tag), routing_key, routing_key])
        self.index[tag] = at
        if instance is not None:
            self.owner[tag] = instance

    # -- RedisBrokerState surface --
    def owners(self) -> dict[str, str]:
        return dict(self.owner)

    def alive_instances(self, instances: set[str]) -> set[str]:
        return instances & self.alive

    def unacked_entry(self, tag: str) -> tuple[str, float | None] | None:
        if tag not in self.unacked:
            return None
        return self.unacked[tag], self.index.get(tag)

    def sweep_stale_owners(self, pairs: list[tuple[str, str]]) -> set[str]:
        gone = {t for t, i in pairs if t not in self.unacked and self.owner.get(t) == i}
        for t in gone:
            del self.owner[t]
        return gone

    def restore(self, tag: str, entry: str, instance: str, message: str,
                queue_keys: list[str]) -> int:
        self.restore_calls += 1
        if self.before_restore is not None:
            self.before_restore()
        if instance in self.alive:
            return dwr.INSTANCE_ALIVE
        if self.owner.get(tag) != instance:
            return dwr.OWNER_CHANGED
        if self.unacked.get(tag) != entry:
            return dwr.ENTRY_GONE
        del self.unacked[tag]
        self.index.pop(tag, None)
        del self.owner[tag]
        for q in queue_keys:
            self.queues.setdefault(q, []).append(message)
        return dwr.RESTORED


def _run(state: FakeState, **kw: Any) -> dwr.RestoreReport:
    return dwr.restore_dead_worker_messages(
        state, grace_seconds=GRACE, now=NOW, **kw  # type: ignore[arg-type]
    )


def test_dead_instance_message_is_restored_redelivered_to_its_queue() -> None:
    s = FakeState()
    s.take("t1", "w@a/1/x", task="workflow.deliver_workflow_callback", routing_key="workflows.m")
    report = _run(s)
    assert [r["tag"] for r in report.restored] == ["t1"]
    assert report.dead_instances == ["w@a/1/x"]
    assert "t1" not in s.unacked and "t1" not in s.owner and "t1" not in s.index
    (pushed,) = s.queues["workflows.m"]
    msg = json.loads(pushed)
    assert msg["headers"]["redelivered"] is True
    assert msg["properties"]["delivery_info"]["redelivered"] is True


def test_live_instance_message_is_untouched() -> None:
    s = FakeState()
    s.alive.add("w@b/2/y")
    s.take("t1", "w@b/2/y")
    report = _run(s)
    assert report.restored == [] and report.skipped_live == 1
    assert "t1" in s.unacked and s.owner["t1"] == "w@b/2/y" and s.queues == {}
    assert s.restore_calls == 0


def test_dead_and_live_side_by_side_only_the_dead_ones_move() -> None:
    s = FakeState()
    s.alive.add("live")
    s.take("a", "live")
    s.take("b", "dead")
    s.take("c", "dead")
    report = _run(s)
    assert sorted(r["tag"] for r in report.restored) == ["b", "c"]
    assert set(s.unacked) == {"a"}


def test_message_younger_than_the_grace_period_waits() -> None:
    """Dead instance, but the message was taken less than the grace ago."""
    s = FakeState()
    s.take("t1", "dead", at=NOW - GRACE + 5)
    report = _run(s)
    assert report.restored == [] and report.skipped_young == 1
    assert "t1" in s.unacked
    s.index["t1"] = NOW - GRACE  # exactly the grace: restored
    assert [r["tag"] for r in _run(s).restored] == ["t1"]


def test_unowned_message_is_left_to_the_visibility_timeout() -> None:
    s = FakeState()
    s.take("t1", None)
    report = _run(s)
    assert report.owners_seen == 0 and report.restored == []
    assert "t1" in s.unacked


def test_goal_tasks_are_left_to_the_goal_runner_reaper() -> None:
    s = FakeState()
    for i, name in enumerate(sorted(dwr.SWEEPER_OWNED_TASKS)):
        s.take(f"g{i}", "dead", task=name)
    s.take("cb", "dead", task="workflow.deliver_workflow_callback")
    report = _run(s)
    assert [r["task"] for r in report.restored] == ["workflow.deliver_workflow_callback"]
    assert report.skipped_sweeper_owned == len(dwr.SWEEPER_OWNED_TASKS)
    assert all(f"g{i}" in s.unacked for i in range(len(dwr.SWEEPER_OWNED_TASKS)))


def test_restored_at_most_once_across_ticks_and_racing_restorers() -> None:
    s = FakeState()
    s.take("t1", "dead")
    first = _run(s)
    second = _run(s)
    assert len(first.restored) == 1 and second.restored == []
    assert sum(len(v) for v in s.queues.values()) == 1


def test_lost_race_with_another_restorer_or_kombu_is_not_a_second_push() -> None:
    s = FakeState()
    s.take("t1", "dead")

    def kombu_restored_it_first() -> None:
        s.unacked.pop("t1")
        s.index.pop("t1")
        s.queues.setdefault("maintenance", []).append("by-kombu")

    s.before_restore = kombu_restored_it_first
    report = _run(s)
    assert report.restored == [] and report.lost_race == 1
    assert s.queues["maintenance"] == ["by-kombu"]


def test_instance_coming_back_between_read_and_restore_wins() -> None:
    s = FakeState()
    s.take("t1", "w")
    s.before_restore = lambda: s.alive.add("w")
    report = _run(s)
    assert report.restored == [] and report.lost_race == 1
    assert "t1" in s.unacked


def test_redelivered_message_re_owned_by_a_new_worker_is_not_restored_again() -> None:
    s = FakeState()
    s.take("t1", "dead")

    def picked_up_by_new_worker() -> None:
        s.owner["t1"] = "new"

    s.before_restore = picked_up_by_new_worker
    report = _run(s)
    assert report.restored == [] and s.owner["t1"] == "new"


def test_owner_records_of_acked_messages_are_swept() -> None:
    s = FakeState()
    s.alive.add("live")
    s.owner["acked"] = "live"
    s.owner["acked-dead"] = "dead"
    report = _run(s)
    assert report.stale_owners_removed == 2 and s.owner == {}
    assert report.restored == [] and report.lost_race == 0


def test_unroutable_message_is_kept_and_counted() -> None:
    s = FakeState()
    s.take("t1", "dead", routing_key="")
    report = _run(s)
    assert report.unroutable == 1 and "t1" in s.unacked


def test_limit_bounds_one_tick() -> None:
    s = FakeState()
    for i in range(5):
        s.take(f"t{i}", "dead")
    report = _run(s, limit=2)
    assert len(report.restored) == 2 and report.truncated
    assert len(_run(s, limit=10).restored) == 3


def test_one_broken_entry_does_not_stop_the_rest() -> None:
    s = FakeState()
    s.take("a", "dead")
    s.unacked["a"] = "not json"
    s.take("b", "dead")
    report = _run(s)
    assert report.errors == 1 and [r["tag"] for r in report.restored] == ["b"]


def test_restorations_are_counted_in_the_metric() -> None:
    from app.observability import metrics

    metric = metrics.CELERY_DEAD_WORKER_RESTORE_TOTAL
    before = metric.labels(outcome="restored")._value.get()
    s = FakeState()
    s.take("t1", "dead")
    s.take("t2", "dead")
    _run(s)
    assert metric.labels(outcome="restored")._value.get() == before + 2


def test_heartbeat_interval_is_a_quarter_of_the_grace() -> None:
    assert dwr.heartbeat_interval_seconds(120) == 30
    assert dwr.heartbeat_interval_seconds(2) == 1.0


def test_settings_defaults() -> None:
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["celery_dead_worker_restore_enabled"].default is True
    assert fields["celery_dead_worker_grace_seconds"].default == 120.0


# ── worker side ────────────────────────────────────────────────────────────────


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def heartbeat(self, instance: str, ttl: float) -> None:
        self.calls.append(("beat", instance))

    def record_owner(self, tag: str, instance: str) -> bool:
        self.calls.append(("own", tag, instance))
        return True


class _Conn:
    default_channel = object()

    def release(self) -> None:
        pass


class _App:
    def connection_for_write(self) -> _Conn:
        return _Conn()


def test_worker_beats_before_it_records_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder()
    monkeypatch.setattr(dwr, "state_for_channel", lambda _ch: rec)
    tracker = dwr.WorkerLiveness(_App(), grace_seconds=GRACE)
    try:
        tracker.record("tag-1", "celery@host")
        tracker.record("tag-2", "celery@host")
    finally:
        tracker.stop()
    inst = tracker.instance_id
    assert inst.startswith("celery@host/")
    assert rec.calls == [("beat", inst), ("own", "tag-1", inst), ("own", "tag-2", inst)]


def test_each_worker_start_is_a_new_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker restarted under the same hostname must not look like the dead one."""
    monkeypatch.setattr(dwr, "state_for_channel", lambda _ch: _Recorder())
    ids = set()
    for _ in range(2):
        t = dwr.WorkerLiveness(_App(), grace_seconds=GRACE)
        t.ensure_started("celery@same-host")
        ids.add(t.instance_id)
        t.stop()
    assert len(ids) == 2


def test_disabled_setting_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dwr, "_settings", lambda: (False, GRACE))
    app = object()
    dwr._liveness_by_app.pop(id(app), None)
    assert dwr.worker_liveness(app) is None


def test_disabled_setting_makes_the_beat_task_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import dead_worker_tasks

    monkeypatch.setattr(dwr, "_settings", lambda: (False, GRACE))
    body = dead_worker_tasks.restore_dead_worker_messages.run.__wrapped__
    assert body(None) == {"skipped": True, "reason": "disabled"}


def test_beat_entry_routes_to_maintenance_with_tick_expiry() -> None:
    from app.scaling.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["restore-dead-worker-messages"]
    assert entry["task"] == "app.scaling.dead_worker_tasks.restore_dead_worker_messages"
    assert entry["schedule"] == 60.0
    assert entry["options"]["queue"] == "maintenance"
    assert 0 < entry["options"]["expires"] < 60
    assert entry["task"] in celery_app.tasks
