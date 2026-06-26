"""Full coverage for ScheduleStore — covers all branches and code paths."""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from pytest import MonkeyPatch

from app.tenancy.context import PlanTier, TenantContext
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore

T = TenantContext(tenant_id="sched-full-t1", plan=PlanTier.PROFESSIONAL, api_key_id="sf1")
T_B = TenantContext(tenant_id="sched-full-t2", plan=PlanTier.FREE, api_key_id="sf2")


@contextlib.asynccontextmanager
async def _noop_async_context(*args: Any, **kwargs: Any) -> AsyncIterator[None]:
    yield None


class _FakeSchedule:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeSession:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.added: list[Any] = []
        self._rows = rows or []

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def begin(self) -> Any:
        return _noop_async_context()

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def execute(self, statement: Any) -> Any:
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: list(self._rows))
        )


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> int:
        self.deleted.append(key)
        return int(self.values.pop(key, None) is not None)


def test_create_returns_schedule_id() -> None:
    """create returns a 32-character hex schedule ID."""
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.ONCE)
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    assert sid is not None
    assert len(sid) == 32


def test_get_returns_record() -> None:
    """get returns the correct record for the owning tenant."""
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *")
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    rec = store.get(sid, tenant_ctx=T)
    assert rec is not None
    assert rec["schedule_id"] == sid
    assert rec["paused"] is False


def test_create_preserves_agent_id_and_goal_template() -> None:
    """create stores agent binding and scheduled goal text as first-class fields."""
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *")
    sid = store.create(
        goal_id="legacy-fallback",
        spec=spec,
        tenant_ctx=T,
        agent_id="agent-abc",
        goal_template="Run daily report",
    )

    rec = store.get(sid, tenant_ctx=T)
    assert rec is not None
    assert rec["goal_id"] == "legacy-fallback"
    assert rec["agent_id"] == "agent-abc"
    assert rec["goal_template"] == "Run daily report"
    assert store.list_all(tenant_ctx=T)[0]["agent_id"] == "agent-abc"


def test_get_wrong_tenant_returns_none() -> None:
    """get returns None when the schedule belongs to a different tenant."""
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.ONCE)
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    rec = store.get(sid, tenant_ctx=T_B)
    assert rec is None


def test_list_all_tenant_isolated() -> None:
    """list_all returns only schedules that belong to the requesting tenant."""
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=3600)
    store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    store.create(goal_id="g2", spec=spec, tenant_ctx=T)
    store.create(goal_id="g3", spec=spec, tenant_ctx=T_B)

    t_schedules = store.list_all(tenant_ctx=T)
    tb_schedules = store.list_all(tenant_ctx=T_B)

    assert len(t_schedules) == 2
    assert len(tb_schedules) == 1


def test_delete_removes_schedule() -> None:
    """delete returns True and removes the schedule; get returns None afterwards."""
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.ONCE)
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    assert store.delete(sid, tenant_ctx=T) is True
    assert store.get(sid, tenant_ctx=T) is None


def test_delete_nonexistent_returns_false() -> None:
    """delete returns False for an unknown schedule ID."""
    store = ScheduleStore()
    assert store.delete("nonexistent-id", tenant_ctx=T) is False


def test_pause_and_resume() -> None:
    """pause sets paused=True; resume sets paused=False."""
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=60)
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)

    assert store.pause(sid, tenant_ctx=T) is True
    rec = store.get(sid, tenant_ctx=T)
    assert rec is not None
    assert rec["paused"] is True

    assert store.resume(sid, tenant_ctx=T) is True
    rec = store.get(sid, tenant_ctx=T)
    assert rec is not None
    assert rec["paused"] is False


def test_pause_nonexistent_returns_false() -> None:
    """pause returns False for an unknown schedule ID."""
    store = ScheduleStore()
    assert store.pause("nonexistent", tenant_ctx=T) is False


def test_resume_nonexistent_returns_false() -> None:
    """resume returns False for an unknown schedule ID."""
    store = ScheduleStore()
    assert store.resume("nonexistent", tenant_ctx=T) is False


def test_create_webhook_trigger() -> None:
    """Webhook trigger stores the TriggerSpec correctly."""
    store = ScheduleStore()
    spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK, webhook_token="tok123")
    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)
    rec = store.get(sid, tenant_ctx=T)
    assert rec is not None
    assert rec["spec"].trigger_type == TriggerType.WEBHOOK
    assert rec["spec"].webhook_token == "tok123"


def test_redis_schedule_payload_omits_webhook_token() -> None:
    redis = _FakeRedis()
    store = ScheduleStore(redis=redis)
    spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK, webhook_token="tok-secret-123")

    sid = store.create(goal_id="g1", spec=spec, tenant_ctx=T)

    key = f"schedule:{T.tenant_id}:{sid}"
    raw_payload = redis.values[key]
    payload = json.loads(raw_payload)
    assert "webhook_token" not in payload
    assert "tok-secret-123" not in raw_payload


def test_redis_schedule_key_written_updated_and_deleted() -> None:
    redis = _FakeRedis()
    store = ScheduleStore(redis=redis)
    spec = TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=60)

    sid = store.create(
        goal_id="legacy-fallback",
        spec=spec,
        tenant_ctx=T,
        agent_id="agent-abc",
        goal_template="Run daily report",
    )

    key = f"schedule:{T.tenant_id}:{sid}"
    payload = json.loads(redis.values[key])
    assert payload == {
        "schedule_id": sid,
        "tenant_id": T.tenant_id,
        "goal_id": "legacy-fallback",
        "agent_id": "agent-abc",
        "goal_template": "Run daily report",
        "trigger_type": "interval",
        "cron_expression": "",
        "timezone": "UTC",
        "interval_seconds": 60,
        "event_channel": "",
        "fire_at_iso": "",
        "condition": "",
        "description": "",
        "paused": False,
    }

    assert store.pause(sid, tenant_ctx=T) is True
    assert json.loads(redis.values[key])["paused"] is True

    assert store.resume(sid, tenant_ctx=T) is True
    assert json.loads(redis.values[key])["paused"] is False

    assert store.delete(sid, tenant_ctx=T) is True
    assert key in redis.deleted
    assert key not in redis.values


async def test_sync_from_db_noop() -> None:
    """sync_from_db returns 0 immediately when no db_session_factory is configured."""
    store = ScheduleStore()
    count = await store.sync_from_db()
    assert count == 0


async def test_db_create_persists_agent_id_and_goal_template(
    monkeypatch: MonkeyPatch,
) -> None:
    """_db_create writes agent binding and scheduled goal text to the DB model."""
    session = _FakeSession()
    store = ScheduleStore(db_session_factory=lambda: session)
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *")

    monkeypatch.setattr("app.db.models.scheduling.Schedule", _FakeSchedule)
    monkeypatch.setattr(
        "app.db.rls.sqlalchemy_rls_context",
        lambda *args, **kwargs: _noop_async_context(),
    )

    await store._db_create(
        "sched-1",
        "legacy-fallback",
        spec,
        T.tenant_id,
        "agent-abc",
        "Run daily report",
    )

    assert len(session.added) == 1
    row = session.added[0]
    assert row.kwargs["agent_id"] == "agent-abc"
    assert row.kwargs["goal_id_template"] == "Run daily report"


async def test_sync_from_db_loads_agent_id_and_goal_template() -> None:
    """sync_from_db restores persisted agent binding and goal text."""
    row = SimpleNamespace(
        id="sched-1",
        tenant_id=T.tenant_id,
        agent_id="agent-abc",
        goal_id_template="Run daily report",
        trigger_type="cron",
        cron_expression="0 9 * * *",
        timezone="UTC",
        interval_seconds=0,
        webhook_token="",
        event_channel="",
        fire_at_iso="",
        condition="",
        description="Weekday report",
        paused=True,
    )
    session = _FakeSession(rows=[row])
    store = ScheduleStore(db_session_factory=lambda: session)

    count = await store.sync_from_db()

    assert count == 1
    rec = store.get("sched-1", tenant_ctx=T)
    assert rec is not None
    assert rec["agent_id"] == "agent-abc"
    assert rec["goal_template"] == "Run daily report"
    assert rec["goal_id"] == "Run daily report"


async def test_delete_removes_db_schedule_so_sync_does_not_resurrect(
    monkeypatch: MonkeyPatch,
) -> None:
    row = SimpleNamespace(
        id="sched-delete",
        tenant_id=T.tenant_id,
        agent_id="agent-abc",
        goal_id_template="Run daily report",
        trigger_type="cron",
        cron_expression="0 9 * * *",
        timezone="UTC",
        interval_seconds=0,
        webhook_token="",
        event_channel="",
        fire_at_iso="",
        condition="",
        description="Weekday report",
        paused=False,
    )
    rows = [row]
    executed: list[str] = []

    class DeletingSession(_FakeSession):
        async def execute(self, statement: Any) -> Any:
            sql = str(statement)
            executed.append(sql)
            if sql.startswith("DELETE"):
                rows.clear()
                return SimpleNamespace(rowcount=1)
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: list(rows))
            )

    monkeypatch.setattr(
        "app.db.rls.sqlalchemy_rls_context",
        lambda *args, **kwargs: _noop_async_context(),
    )

    store = ScheduleStore(db_session_factory=lambda: DeletingSession())
    loaded = await store.sync_from_db()
    assert loaded == 1

    assert await store.delete_async("sched-delete", tenant_ctx=T) is True

    resurrected = await store.sync_from_db()

    assert resurrected == 0
    assert store.get("sched-delete", tenant_ctx=T) is None
    assert any(sql.startswith("DELETE") for sql in executed)


async def test_delete_async_waits_for_db_delete_before_returning(
    monkeypatch: MonkeyPatch,
) -> None:
    events: list[str] = []

    class AwaitingDeleteSession(_FakeSession):
        async def execute(self, statement: Any) -> Any:
            sql = str(statement)
            if sql.startswith("DELETE"):
                events.append("db-delete-start")
                await asyncio.sleep(0)
                events.append("db-delete-finished")
                return SimpleNamespace(rowcount=1)
            return await super().execute(statement)

    monkeypatch.setattr(
        "app.db.rls.sqlalchemy_rls_context",
        lambda *args, **kwargs: _noop_async_context(),
    )

    store = ScheduleStore(db_session_factory=lambda: AwaitingDeleteSession())
    spec = TriggerSpec(trigger_type=TriggerType.ONCE)
    store._data[(T.tenant_id, "sched-await-delete")] = {
        "schedule_id": "sched-await-delete",
        "goal_id": "g1",
        "agent_id": "",
        "goal_template": "",
        "spec": spec,
        "paused": False,
    }

    removed = await store.delete_async("sched-await-delete", tenant_ctx=T)
    events.append("delete-returned")

    assert removed is True
    assert events == ["db-delete-start", "db-delete-finished", "delete-returned"]


async def test_delete_async_raises_on_db_delete_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    class FailingDeleteSession(_FakeSession):
        async def execute(self, statement: Any) -> Any:
            if str(statement).startswith("DELETE"):
                raise RuntimeError("db delete failed")
            return await super().execute(statement)

    monkeypatch.setattr(
        "app.db.rls.sqlalchemy_rls_context",
        lambda *args, **kwargs: _noop_async_context(),
    )

    store = ScheduleStore(db_session_factory=lambda: FailingDeleteSession())
    spec = TriggerSpec(trigger_type=TriggerType.ONCE)
    store._data[(T.tenant_id, "sched-db-fail")] = {
        "schedule_id": "sched-db-fail",
        "goal_id": "g1",
        "agent_id": "",
        "goal_template": "",
        "spec": spec,
        "paused": False,
    }

    with pytest.raises(RuntimeError, match="db delete failed"):
        await store.delete_async("sched-db-fail", tenant_ctx=T)


async def test_create_async_db_failure_leaves_no_memory_or_redis(
    monkeypatch: MonkeyPatch,
) -> None:
    class FailingCreateSession(_FakeSession):
        def add(self, row: Any) -> None:
            raise RuntimeError("db create failed")

    redis = _FakeRedis()
    store = ScheduleStore(db_session_factory=lambda: FailingCreateSession(), redis=redis)
    spec = TriggerSpec(trigger_type=TriggerType.ONCE)

    monkeypatch.setattr("app.db.models.scheduling.Schedule", _FakeSchedule)
    monkeypatch.setattr(
        "app.db.rls.sqlalchemy_rls_context",
        lambda *args, **kwargs: _noop_async_context(),
    )

    with pytest.raises(RuntimeError, match="db create failed"):
        await store.create_async(goal_id="g1", spec=spec, tenant_ctx=T)

    assert store.list_all(tenant_ctx=T) == []
    assert redis.values == {}


async def test_create_async_redis_failure_rolls_back_db_delete(
    monkeypatch: MonkeyPatch,
) -> None:
    class FailingRedis:
        def set(self, key: str, value: str) -> None:
            raise RuntimeError(f"redis set failed for {key}")

    events: list[tuple[str, str, str, bool]] = []
    store = ScheduleStore(db_session_factory=lambda: _FakeSession(), redis=FailingRedis())
    spec = TriggerSpec(trigger_type=TriggerType.ONCE)

    async def db_create(
        sched_id: str,
        goal_id: str,
        spec: TriggerSpec,
        tenant_id: str,
        agent_id: str,
        goal_template: str,
        *,
        strict: bool = False,
    ) -> None:
        events.append(("create", sched_id, tenant_id, strict))

    async def db_delete(
        schedule_id: str, tenant_id: str, *, strict: bool = False
    ) -> None:
        events.append(("delete", schedule_id, tenant_id, strict))

    monkeypatch.setattr(store, "_db_create", db_create)
    monkeypatch.setattr(store, "_db_delete_schedule", db_delete)

    with pytest.raises(RuntimeError, match="redis set failed"):
        await store.create_async(goal_id="g1", spec=spec, tenant_ctx=T)

    assert len(events) == 2
    assert events[0][0] == "create"
    assert events[1] == ("delete", events[0][1], T.tenant_id, True)
    assert store.list_all(tenant_ctx=T) == []


async def test_delete_async_db_failure_leaves_memory_and_redis_untouched(
    monkeypatch: MonkeyPatch,
) -> None:
    class FailingDeleteSession(_FakeSession):
        async def execute(self, statement: Any) -> Any:
            if str(statement).startswith("DELETE"):
                raise RuntimeError("db delete failed")
            return await super().execute(statement)

    redis = _FakeRedis()
    store = ScheduleStore(db_session_factory=lambda: FailingDeleteSession(), redis=redis)
    spec = TriggerSpec(trigger_type=TriggerType.ONCE)
    store._data[(T.tenant_id, "sched-db-fail")] = {
        "schedule_id": "sched-db-fail",
        "goal_id": "g1",
        "agent_id": "",
        "goal_template": "",
        "spec": spec,
        "paused": False,
    }
    key = f"schedule:{T.tenant_id}:sched-db-fail"
    redis.values[key] = "{}"

    monkeypatch.setattr(
        "app.db.rls.sqlalchemy_rls_context",
        lambda *args, **kwargs: _noop_async_context(),
    )

    with pytest.raises(RuntimeError, match="db delete failed"):
        await store.delete_async("sched-db-fail", tenant_ctx=T)

    assert store.get("sched-db-fail", tenant_ctx=T) is not None
    assert redis.values == {key: "{}"}
    assert redis.deleted == []


async def test_delete_async_raises_on_redis_delete_failure() -> None:
    class FailingRedis:
        def delete(self, key: str) -> int:
            raise RuntimeError(f"redis delete failed for {key}")

    store = ScheduleStore(redis=FailingRedis())
    spec = TriggerSpec(trigger_type=TriggerType.ONCE)
    store._data[(T.tenant_id, "sched-redis-fail")] = {
        "schedule_id": "sched-redis-fail",
        "goal_id": "g1",
        "agent_id": "",
        "goal_template": "",
        "spec": spec,
        "paused": False,
    }

    with pytest.raises(RuntimeError, match="redis delete failed"):
        await store.delete_async("sched-redis-fail", tenant_ctx=T)


async def test_delete_async_redis_failure_after_db_success_leaves_memory(
    monkeypatch: MonkeyPatch,
) -> None:
    class FailingRedis:
        def delete(self, key: str) -> int:
            raise RuntimeError(f"redis delete failed for {key}")

    events: list[str] = []

    class SuccessfulDeleteSession(_FakeSession):
        async def execute(self, statement: Any) -> Any:
            if str(statement).startswith("DELETE"):
                events.append("db-delete")
                return SimpleNamespace(rowcount=1)
            return await super().execute(statement)

    store = ScheduleStore(
        db_session_factory=lambda: SuccessfulDeleteSession(), redis=FailingRedis()
    )
    spec = TriggerSpec(trigger_type=TriggerType.ONCE)
    store._data[(T.tenant_id, "sched-redis-fail")] = {
        "schedule_id": "sched-redis-fail",
        "goal_id": "g1",
        "agent_id": "",
        "goal_template": "",
        "spec": spec,
        "paused": False,
    }

    monkeypatch.setattr(
        "app.db.rls.sqlalchemy_rls_context",
        lambda *args, **kwargs: _noop_async_context(),
    )

    with pytest.raises(RuntimeError, match="redis delete failed"):
        await store.delete_async("sched-redis-fail", tenant_ctx=T)

    assert events == ["db-delete"]
    assert store.get("sched-redis-fail", tenant_ctx=T) is not None


async def test_create_async_then_delete_async_does_not_background_resurrect(
    monkeypatch: MonkeyPatch,
) -> None:
    rows: dict[str, Any] = {}
    events: list[str] = []

    class DurableSession(_FakeSession):
        def add(self, row: Any) -> None:
            events.append("db-create")
            rows[row.id] = row

        async def execute(self, statement: Any) -> Any:
            sql = str(statement)
            if sql.startswith("DELETE"):
                events.append("db-delete")
                rows.clear()
                return SimpleNamespace(rowcount=1)
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: list(rows.values()))
            )

    monkeypatch.setattr(
        "app.db.rls.sqlalchemy_rls_context",
        lambda *args, **kwargs: _noop_async_context(),
    )

    store = ScheduleStore(db_session_factory=lambda: DurableSession())
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 9 * * *")

    sid = await store.create_async(
        goal_id="legacy-fallback",
        spec=spec,
        tenant_ctx=T,
        agent_id="agent-abc",
        goal_template="Run daily report",
    )
    assert sid in rows
    assert len(store._db_tasks) == 0

    assert await store.delete_async(sid, tenant_ctx=T) is True
    resurrected = await store.sync_from_db()

    assert resurrected == 0
    assert store.get(sid, tenant_ctx=T) is None
    assert events == ["db-create", "db-delete"]


async def test_sync_from_db_with_failing_factory() -> None:
    """sync_from_db returns 0 gracefully when the DB factory raises on call."""

    def bad_factory() -> None:
        raise RuntimeError("DB connection failed")

    store = ScheduleStore(db_session_factory=bad_factory)
    count = await store.sync_from_db()
    assert count == 0
