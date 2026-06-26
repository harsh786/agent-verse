"""Tests for Celery routing and retry hardening."""

from __future__ import annotations

import datetime
import json
import sys
from collections.abc import Mapping
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Self, cast

import pytest

from app.scaling.celery_app import celery_app


def test_celery_routes_core_tasks_to_named_queues() -> None:
    routes = cast(Mapping[str, Mapping[str, str]], celery_app.conf.task_routes)

    assert routes["app.scaling.tasks.run_goal"]["queue"] == "goals"
    assert routes["app.scaling.tasks.run_scheduled_goal"]["queue"] == "schedules"
    assert routes["app.scaling.tasks.fire_due_schedules"]["queue"] == "schedules"
    assert routes["app.scaling.tasks.check_mcp_health"]["queue"] == "maintenance"
    assert routes["app.scaling.tasks.record_queue_depths"]["queue"] == "maintenance"


def test_celery_uses_hardened_retry_and_delivery_settings() -> None:
    from app.scaling import tasks

    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.task_default_retry_delay == 30
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert tasks.run_scheduled_goal.max_retries == 3
    assert tasks.fire_due_schedules.max_retries == 3
    assert tasks.record_queue_depths.max_retries == 3


def test_beat_schedule_routes_fire_due_schedules_to_schedules_queue() -> None:
    beat_schedule = cast(Mapping[str, Mapping[str, Any]], celery_app.conf.beat_schedule)
    schedule_entry = beat_schedule["fire-due-schedules-every-60s"]
    options = cast(Mapping[str, str], schedule_entry["options"])

    assert schedule_entry["task"] == "app.scaling.tasks.fire_due_schedules"
    assert options["queue"] == "schedules"


def test_beat_schedule_records_queue_depths_on_maintenance_queue() -> None:
    beat_schedule = cast(Mapping[str, Mapping[str, Any]], celery_app.conf.beat_schedule)
    schedule_entry = beat_schedule["record-queue-depths-every-30s"]
    options = cast(Mapping[str, str], schedule_entry["options"])

    assert schedule_entry["task"] == "app.scaling.tasks.record_queue_depths"
    assert options["queue"] == "maintenance"


def test_check_mcp_health_task_name_matches_route() -> None:
    from app.scaling import tasks

    assert hasattr(tasks, "check_mcp_health")
    assert tasks.check_mcp_health.name == "app.scaling.tasks.check_mcp_health"


def test_run_scheduled_goal_dispatches_downstream_goal_to_schedules_queue(monkeypatch: Any) -> None:
    from app.scaling import tasks

    calls: list[dict[str, Any]] = []
    schedule_id = "schedule:tenant-with-long-id:uuid-1234567890abcdef1234567890abcdef"

    class Result:
        id = "task-123"

    def fake_apply_async(*, kwargs: dict[str, Any], queue: str) -> Result:
        calls.append({"kwargs": kwargs, "queue": queue})
        return Result()

    monkeypatch.setattr(tasks.run_goal, "apply_async", fake_apply_async)

    result = tasks.run_scheduled_goal.run(
        schedule_id,
        "tenant-1",
        "Compile report",
        "agent-1",
    )

    assert result == {
        "status": "dispatched",
        "schedule_id": schedule_id,
        "task_id": "task-123",
    }
    assert calls == [
        {
            "kwargs": {
                "goal_id": calls[0]["kwargs"]["goal_id"],
                "goal_text": "Compile report",
                "tenant_id": "tenant-1",
                "agent_id": "agent-1",
            },
            "queue": "schedules",
        }
    ]
    assert calls[0]["kwargs"]["goal_id"].startswith("sched_")
    assert len(calls[0]["kwargs"]["goal_id"]) <= 32
    assert ":" not in calls[0]["kwargs"]["goal_id"]


def test_run_scheduled_goal_retries_dispatch_failure(monkeypatch: Any) -> None:
    from app.scaling import tasks

    def fail_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        raise RuntimeError("broker down")

    monkeypatch.setattr(tasks.run_goal, "apply_async", fail_apply_async)

    with pytest.raises(RuntimeError, match="broker down"):
        tasks.run_scheduled_goal.run(
            "schedule:tenant-1:sched-1",
            "tenant-1",
            "Compile report",
            "agent-1",
        )


def test_record_queue_depths_records_redis_llen_for_named_queues(monkeypatch: Any) -> None:
    from app.scaling import tasks

    recorded: list[tuple[str, float]] = []

    class FakeRedis:
        def llen(self, queue: str) -> int:
            return {"goals": 4, "schedules": 2, "maintenance": 1}[queue]

    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(from_url=lambda *args, **kwargs: FakeRedis()),
    )
    monkeypatch.setattr(
        "app.observability.metrics.record_queue_depth",
        lambda queue, depth: recorded.append((queue, depth)),
    )

    result = tasks.record_queue_depths()

    assert result == {
        "status": "ok",
        "queues_recorded": 3,
        "depths": {"goals": 4, "schedules": 2, "maintenance": 1},
    }
    assert recorded == [("goals", 4.0), ("schedules", 2.0), ("maintenance", 1.0)]


def test_fire_due_schedules_discovers_schedule_store_payload(monkeypatch: Any) -> None:
    from app.scaling import tasks

    redis_store = {
        "schedule:tenant-1:sched-1": json.dumps(
            {
                "schedule_id": "sched-1",
                "tenant_id": "tenant-1",
                "goal_id": "legacy-fallback",
                "agent_id": "agent-1",
                "goal_template": "Compile report",
                "trigger_type": "interval",
                "cron_expression": "",
                "timezone": "UTC",
                "interval_seconds": 60,
                "webhook_token": "",
                "event_channel": "",
                "fire_at_iso": "",
                "condition": "",
                "description": "",
                "paused": False,
            }
        )
    }
    dispatched: list[dict[str, Any]] = []

    class FakeRedis:
        def scan_iter(self, *, match: str, count: int) -> list[str]:
            assert match == "schedule:*"
            assert count == 100
            return list(redis_store)

        def get(self, key: str) -> str | None:
            return redis_store.get(key)

        def set(self, key: str, value: str) -> None:
            redis_store[key] = value

    def fake_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        dispatched.append({"kwargs": kwargs, "queue": queue})
        return SimpleNamespace(id="task-1")

    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(from_url=lambda *args, **kwargs: FakeRedis()),
    )
    monkeypatch.setattr(tasks.run_goal, "apply_async", fake_apply_async)

    result = tasks.fire_due_schedules()

    assert result["schedules_checked"] == 1
    assert result["schedules_fired"] == 1
    assert dispatched == [
        {
            "kwargs": {
                "goal_id": dispatched[0]["kwargs"]["goal_id"],
                "goal_text": "Compile report",
                "goal_template": "Compile report",
                "tenant_id": "tenant-1",
                "agent_id": "agent-1",
            },
            "queue": "schedules",
        }
    ]


def test_fire_due_schedules_redacts_legacy_redis_secret_fields(monkeypatch: Any) -> None:
    from app.scaling import tasks

    redis_store = {
        "schedule:tenant-1:sched-1": json.dumps(
            {
                "schedule_id": "sched-1",
                "tenant_id": "tenant-1",
                "goal_template": "Compile report",
                "agent_id": "agent-1",
                "trigger_type": "interval",
                "interval_seconds": 60,
                "paused": False,
                "webhook_token": "webhook-secret",
                "token": "token-secret",
                "password": "password-secret",
                "api_key": "api-key-secret",
                "secret": "secret-value",
            }
        )
    }

    class FakeRedis:
        def scan_iter(self, *, match: str, count: int) -> list[str]:
            return list(redis_store)

        def get(self, key: str) -> str | None:
            return redis_store.get(key)

        def set(self, key: str, value: str) -> None:
            redis_store[key] = value

    def fake_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        return SimpleNamespace(id="task-1")

    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(from_url=lambda *args, **kwargs: FakeRedis()),
    )
    monkeypatch.setattr(tasks.run_goal, "apply_async", fake_apply_async)

    result = tasks.fire_due_schedules()

    rewritten = redis_store["schedule:tenant-1:sched-1"]
    payload = json.loads(rewritten)
    assert result["schedules_fired"] == 1
    for key in ("webhook_token", "token", "password", "api_key", "secret"):
        assert key not in payload
    for value in (
        "webhook-secret",
        "token-secret",
        "password-secret",
        "api-key-secret",
        "secret-value",
    ):
        assert value not in rewritten


@pytest.mark.parametrize(
    ("schedule_key", "payload"),
    [
        (
            "schedule:tenant-1:sched-paused",
            {
                "schedule_id": "sched-paused",
                "tenant_id": "tenant-1",
                "goal_template": "Compile paused report",
                "agent_id": "agent-1",
                "trigger_type": "interval",
                "interval_seconds": 60,
                "paused": True,
                "webhook_token": "paused-webhook-secret",
            },
        ),
        (
            "schedule:tenant-1:sched-not-due",
            {
                "schedule_id": "sched-not-due",
                "tenant_id": "tenant-1",
                "goal_template": "Compile recent report",
                "agent_id": "agent-1",
                "trigger_type": "interval",
                "interval_seconds": 3600,
                "last_fired_at": datetime.datetime.now(datetime.UTC)
                .replace(tzinfo=None)
                .isoformat(),
                "paused": False,
                "webhook_token": "recent-webhook-secret",
            },
        ),
    ],
)
def test_fire_due_schedules_sanitizes_undispatched_legacy_redis_secrets(
    monkeypatch: Any, schedule_key: str, payload: dict[str, Any]
) -> None:
    from app.scaling import tasks

    redis_store = {schedule_key: json.dumps(payload)}

    class FakeRedis:
        def scan_iter(self, *, match: str, count: int) -> list[str]:
            return list(redis_store)

        def get(self, key: str) -> str | None:
            return redis_store.get(key)

        def set(self, key: str, value: str) -> None:
            redis_store[key] = value

    def fail_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        raise AssertionError("schedule should not dispatch")

    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(from_url=lambda *args, **kwargs: FakeRedis()),
    )
    monkeypatch.setattr(tasks.run_goal, "apply_async", fail_apply_async)

    result = tasks.fire_due_schedules()

    rewritten = redis_store[schedule_key]
    assert result["schedules_checked"] == 1
    assert result["schedules_fired"] == 0
    assert "webhook_token" not in json.loads(rewritten)
    assert str(payload["webhook_token"]) not in rewritten


def test_fire_due_schedules_does_not_discover_db_by_default_with_redis(
    monkeypatch: Any,
) -> None:
    from app.scaling import tasks

    class FakeRedis:
        def scan_iter(self, *, match: str, count: int) -> list[str]:
            return []

    async def fail_db_discovery() -> dict[str, dict[str, Any]]:
        raise AssertionError("DB discovery should be gated by env")

    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(from_url=lambda *args, **kwargs: FakeRedis()),
    )
    monkeypatch.setattr(tasks, "_load_db_schedules", fail_db_discovery)

    result = tasks.fire_due_schedules()

    assert result["status"] == "ok"
    assert result["schedules_checked"] == 0
    assert result["schedules_fired"] == 0


def test_fire_due_schedules_discovers_db_schedule_without_redis(monkeypatch: Any) -> None:
    from app.scaling import tasks

    dispatched: list[dict[str, Any]] = []

    class FakeResult:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def scalars(self) -> FakeResult:
            return self

        def all(self) -> list[Any]:
            return self._rows

    class FakeSession:
        def __init__(self) -> None:
            self._results = [
                FakeResult([SimpleNamespace(id="tenant-1")]),
                FakeResult(
                    [
                        SimpleNamespace(
                            id="sched-db-1",
                            tenant_id="tenant-1",
                            agent_id="agent-1",
                            goal_id_template="Compile DB report",
                            trigger_type="interval",
                            cron_expression="",
                            timezone="UTC",
                            interval_seconds=60,
                            webhook_token="",
                            event_channel="",
                            fire_at_iso="",
                            condition="",
                            description="",
                            paused=False,
                            last_fired_at=None,
                        )
                    ]
                ),
            ]

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def execute(self, query: Any) -> FakeResult:
            return self._results.pop(0)

    @asynccontextmanager
    async def fake_rls_context(session: Any, tenant_id: str) -> Any:
        assert tenant_id == "tenant-1"
        yield session

    def fake_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        dispatched.append({"kwargs": kwargs, "queue": queue})
        return SimpleNamespace(id="task-db-1")

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "true")
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: FakeSession)
    monkeypatch.setattr("app.db.rls.sqlalchemy_rls_context", fake_rls_context)
    monkeypatch.setattr(tasks.run_scheduled_goal, "apply_async", fake_apply_async)

    result = tasks.fire_due_schedules()

    assert result["schedules_checked"] == 1
    assert result["schedules_fired"] == 1
    assert dispatched == [
        {
            "kwargs": {
                "schedule_id": "schedule:tenant-1:sched-db-1",
                "goal_template": "Compile DB report",
                "tenant_id": "tenant-1",
                "agent_id": "agent-1",
                "fire_instance_id": result["checked_at"],
            },
            "queue": "schedules",
        }
    ]


def test_fire_due_schedules_persists_db_interval_last_fired_at(monkeypatch: Any) -> None:
    from app.scaling import tasks

    dispatched: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []

    class FakeResult:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def scalars(self) -> FakeResult:
            return self

        def all(self) -> list[Any]:
            return self._rows

    class FakeSession:
        def __init__(self) -> None:
            self._results = [
                FakeResult([SimpleNamespace(id="tenant-1")]),
                FakeResult(
                    [
                        SimpleNamespace(
                            id="sched-db-1",
                            tenant_id="tenant-1",
                            agent_id="agent-1",
                            goal_id_template="Compile DB report",
                            trigger_type="interval",
                            cron_expression="",
                            timezone="UTC",
                            interval_seconds=60,
                            webhook_token="",
                            event_channel="",
                            fire_at_iso="",
                            condition="",
                            description="",
                            paused=False,
                            last_fired_at=None,
                        )
                    ]
                ),
            ]

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def execute(self, query: Any) -> FakeResult:
            return self._results.pop(0)

    @asynccontextmanager
    async def fake_rls_context(session: Any, tenant_id: str) -> Any:
        assert tenant_id == "tenant-1"
        yield session

    def fake_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        dispatched.append({"kwargs": kwargs, "queue": queue})
        return SimpleNamespace(id="task-db-1")

    async def fake_update_last_fired_at(
        tenant_id: str,
        schedule_id: str,
        fired_at: datetime.datetime,
    ) -> None:
        updates.append(
            {"tenant_id": tenant_id, "schedule_id": schedule_id, "fired_at": fired_at}
        )

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "true")
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: FakeSession)
    monkeypatch.setattr("app.db.rls.sqlalchemy_rls_context", fake_rls_context)
    monkeypatch.setattr(tasks.run_scheduled_goal, "apply_async", fake_apply_async)
    monkeypatch.setattr(
        tasks,
        "_update_db_schedule_last_fired_at",
        fake_update_last_fired_at,
        raising=False,
    )

    result = tasks.fire_due_schedules()

    assert result["schedules_checked"] == 1
    assert result["schedules_fired"] == 1
    assert dispatched[0]["kwargs"]["schedule_id"] == "schedule:tenant-1:sched-db-1"
    assert updates == [
        {
            "tenant_id": "tenant-1",
            "schedule_id": "sched-db-1",
            "fired_at": datetime.datetime.fromisoformat(result["checked_at"]),
        }
    ]


def test_fire_due_schedules_updates_db_last_fired_after_dispatch(
    monkeypatch: Any,
) -> None:
    from app.scaling import tasks

    order: list[str] = []

    class FakeResult:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def scalars(self) -> FakeResult:
            return self

        def all(self) -> list[Any]:
            return self._rows

    class FakeSession:
        def __init__(self) -> None:
            self._results = [
                FakeResult([SimpleNamespace(id="tenant-1")]),
                FakeResult(
                    [
                        SimpleNamespace(
                            id="sched-db-1",
                            tenant_id="tenant-1",
                            agent_id="agent-1",
                            goal_id_template="Compile DB report",
                            trigger_type="interval",
                            cron_expression="",
                            timezone="UTC",
                            interval_seconds=60,
                            webhook_token="",
                            event_channel="",
                            fire_at_iso="",
                            condition="",
                            description="",
                            paused=False,
                            last_fired_at=None,
                        )
                    ]
                ),
            ]

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def execute(self, query: Any) -> FakeResult:
            return self._results.pop(0)

    @asynccontextmanager
    async def fake_rls_context(session: Any, tenant_id: str) -> Any:
        yield session

    def fake_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        order.append("dispatch")
        return SimpleNamespace(id="task-db-1")

    async def fake_update_last_fired_at(
        tenant_id: str,
        schedule_id: str,
        fired_at: datetime.datetime,
    ) -> None:
        order.append("update")

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "true")
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: FakeSession)
    monkeypatch.setattr("app.db.rls.sqlalchemy_rls_context", fake_rls_context)
    monkeypatch.setattr(tasks.run_scheduled_goal, "apply_async", fake_apply_async)
    monkeypatch.setattr(
        tasks,
        "_update_db_schedule_last_fired_at",
        fake_update_last_fired_at,
        raising=False,
    )

    result = tasks.fire_due_schedules()

    assert result["schedules_fired"] == 1
    assert order == ["dispatch", "update"]


def test_fire_due_schedules_db_dispatch_failure_does_not_update_last_fired_at(
    monkeypatch: Any,
) -> None:
    from app.scaling import tasks

    updates: list[dict[str, Any]] = []

    class FakeResult:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def scalars(self) -> FakeResult:
            return self

        def all(self) -> list[Any]:
            return self._rows

    class FakeSession:
        def __init__(self) -> None:
            self._results = [
                FakeResult([SimpleNamespace(id="tenant-1")]),
                FakeResult(
                    [
                        SimpleNamespace(
                            id="sched-db-1",
                            tenant_id="tenant-1",
                            agent_id="agent-1",
                            goal_id_template="Compile DB report",
                            trigger_type="interval",
                            cron_expression="",
                            timezone="UTC",
                            interval_seconds=60,
                            webhook_token="",
                            event_channel="",
                            fire_at_iso="",
                            condition="",
                            description="",
                            paused=False,
                            last_fired_at=None,
                        )
                    ]
                ),
            ]

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def execute(self, query: Any) -> FakeResult:
            return self._results.pop(0)

    @asynccontextmanager
    async def fake_rls_context(session: Any, tenant_id: str) -> Any:
        yield session

    def fail_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        raise RuntimeError("broker down")

    async def fake_update_last_fired_at(
        tenant_id: str,
        schedule_id: str,
        fired_at: datetime.datetime,
    ) -> None:
        updates.append(
            {"tenant_id": tenant_id, "schedule_id": schedule_id, "fired_at": fired_at}
        )

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "true")
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: FakeSession)
    monkeypatch.setattr("app.db.rls.sqlalchemy_rls_context", fake_rls_context)
    monkeypatch.setattr(tasks.run_scheduled_goal, "apply_async", fail_apply_async)
    monkeypatch.setattr(
        tasks,
        "_update_db_schedule_last_fired_at",
        fake_update_last_fired_at,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="broker down"):
        tasks.fire_due_schedules.run()

    assert updates == []


def test_fire_due_schedules_retries_dispatch_failure(monkeypatch: Any) -> None:
    from app.scaling import tasks

    redis_store = {
        "schedule:tenant-1:sched-1": json.dumps(
            {
                "schedule_id": "sched-1",
                "tenant_id": "tenant-1",
                "agent_id": "agent-1",
                "goal_template": "Compile report",
                "trigger_type": "interval",
                "interval_seconds": 60,
                "paused": False,
            }
        )
    }

    class FakeRedis:
        def scan_iter(self, *, match: str, count: int) -> list[str]:
            return list(redis_store)

        def get(self, key: str) -> str | None:
            return redis_store.get(key)

        def set(self, key: str, value: str) -> None:
            redis_store[key] = value

    def fail_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        raise RuntimeError("broker down")

    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(from_url=lambda *args, **kwargs: FakeRedis()),
    )
    monkeypatch.setattr(tasks.run_goal, "apply_async", fail_apply_async)

    with pytest.raises(RuntimeError, match="broker down"):
        tasks.fire_due_schedules.run()


def test_fire_due_schedules_cron_dispatch_failure_raises(monkeypatch: Any) -> None:
    from app.scaling import tasks

    current_occurrence = datetime.datetime(2026, 6, 25, 10, 0, 0)
    redis_store = {
        "schedule:tenant-1:sched-cron-1": json.dumps(
            {
                "schedule_id": "sched-cron-1",
                "tenant_id": "tenant-1",
                "agent_id": "agent-1",
                "goal_template": "Compile cron report",
                "trigger_type": "cron",
                "cron_expression": "* * * * *",
                "timezone": "UTC",
                "paused": False,
            }
        )
    }

    class FakeCron:
        def get_prev(self, ret_type: Any) -> datetime.datetime:
            return current_occurrence

    class FakeRedis:
        def scan_iter(self, *, match: str, count: int) -> list[str]:
            return list(redis_store)

        def get(self, key: str) -> str | None:
            return redis_store.get(key)

        def set(self, key: str, value: str) -> None:
            redis_store[key] = value

    def fail_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        raise RuntimeError("broker down")

    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(from_url=lambda *args, **kwargs: FakeRedis()),
    )
    monkeypatch.setitem(
        sys.modules,
        "croniter",
        SimpleNamespace(croniter=lambda *args, **kwargs: FakeCron()),
    )
    monkeypatch.setattr(tasks.run_goal, "apply_async", fail_apply_async)

    with pytest.raises(RuntimeError, match="broker down"):
        tasks.fire_due_schedules.run()


def test_fire_due_schedules_updates_db_when_schedule_also_exists_in_redis(
    monkeypatch: Any,
) -> None:
    from app.scaling import tasks

    redis_store = {
        "schedule:tenant-1:sched-shared": json.dumps(
            {
                "schedule_id": "sched-shared",
                "tenant_id": "tenant-1",
                "goal_template": "Compile shared report",
                "agent_id": "agent-redis",
                "trigger_type": "interval",
                "interval_seconds": 60,
                "paused": False,
            }
        )
    }
    direct_dispatched: list[dict[str, Any]] = []
    dispatched: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []

    class FakeRedis:
        def scan_iter(self, *, match: str, count: int) -> list[str]:
            return list(redis_store)

        def get(self, key: str) -> str | None:
            return redis_store.get(key)

        def set(self, key: str, value: str) -> None:
            redis_store[key] = value

    class FakeResult:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def scalars(self) -> FakeResult:
            return self

        def all(self) -> list[Any]:
            return self._rows

    class FakeSession:
        def __init__(self) -> None:
            self._results = [
                FakeResult([SimpleNamespace(id="tenant-1")]),
                FakeResult(
                    [
                        SimpleNamespace(
                            id="sched-shared",
                            tenant_id="tenant-1",
                            agent_id="agent-db",
                            goal_id_template="Compile shared report from DB",
                            trigger_type="interval",
                            cron_expression="",
                            timezone="UTC",
                            interval_seconds=60,
                            webhook_token="",
                            event_channel="",
                            fire_at_iso="",
                            condition="",
                            description="",
                            paused=False,
                            last_fired_at=None,
                        )
                    ]
                ),
            ]

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def execute(self, query: Any) -> FakeResult:
            return self._results.pop(0)

    @asynccontextmanager
    async def fake_rls_context(session: Any, tenant_id: str) -> Any:
        yield session

    def fake_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        dispatched.append({"kwargs": kwargs, "queue": queue})
        return SimpleNamespace(id="task-db-1")

    def fake_direct_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        direct_dispatched.append({"kwargs": kwargs, "queue": queue})
        return SimpleNamespace(id="task-direct-1")

    async def fake_update_last_fired_at(
        tenant_id: str,
        schedule_id: str,
        fired_at: datetime.datetime,
    ) -> None:
        updates.append(
            {"tenant_id": tenant_id, "schedule_id": schedule_id, "fired_at": fired_at}
        )

    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "true")
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(from_url=lambda *args, **kwargs: FakeRedis()),
    )
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: FakeSession)
    monkeypatch.setattr("app.db.rls.sqlalchemy_rls_context", fake_rls_context)
    monkeypatch.setattr(tasks.run_goal, "apply_async", fake_direct_apply_async)
    monkeypatch.setattr(tasks.run_scheduled_goal, "apply_async", fake_apply_async)
    monkeypatch.setattr(
        tasks,
        "_update_db_schedule_last_fired_at",
        fake_update_last_fired_at,
        raising=False,
    )

    result = tasks.fire_due_schedules()

    assert result["schedules_checked"] == 1
    assert result["schedules_fired"] == 1
    assert direct_dispatched == []
    assert dispatched[0]["kwargs"]["schedule_id"] == "schedule:tenant-1:sched-shared"
    assert dispatched[0]["kwargs"]["fire_instance_id"] == result["checked_at"]
    assert updates == [
        {
            "tenant_id": "tenant-1",
            "schedule_id": "sched-shared",
            "fired_at": datetime.datetime.fromisoformat(result["checked_at"]),
        }
    ]


def test_fire_due_schedules_skips_cron_already_fired_for_current_occurrence(
    monkeypatch: Any,
) -> None:
    from app.scaling import tasks

    now = datetime.datetime(2026, 6, 25, 10, 0, 15)
    current_occurrence = datetime.datetime(2026, 6, 25, 10, 0, 0)
    dispatched: list[dict[str, Any]] = []

    class FrozenDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz: datetime.tzinfo | None = None) -> Self:
            frozen = cls(
                now.year,
                now.month,
                now.day,
                now.hour,
                now.minute,
                now.second,
                now.microsecond,
            )
            if tz is None:
                return frozen
            return frozen.replace(tzinfo=datetime.UTC).astimezone(tz)

    class FakeCron:
        def get_next(self, ret_type: Any) -> datetime.datetime:
            return current_occurrence

        def get_prev(self, ret_type: Any) -> datetime.datetime:
            return current_occurrence

    class FakeResult:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def scalars(self) -> FakeResult:
            return self

        def all(self) -> list[Any]:
            return self._rows

    class FakeSession:
        def __init__(self) -> None:
            self._results = [
                FakeResult([SimpleNamespace(id="tenant-1")]),
                FakeResult(
                    [
                        SimpleNamespace(
                            id="sched-cron-1",
                            tenant_id="tenant-1",
                            agent_id="agent-1",
                            goal_id_template="Compile cron report",
                            trigger_type="cron",
                            cron_expression="* * * * *",
                            timezone="UTC",
                            interval_seconds=0,
                            webhook_token="",
                            event_channel="",
                            fire_at_iso="",
                            condition="",
                            description="",
                            paused=False,
                            last_fired_at=current_occurrence,
                        )
                    ]
                ),
            ]

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def execute(self, query: Any) -> FakeResult:
            return self._results.pop(0)

    @asynccontextmanager
    async def fake_rls_context(session: Any, tenant_id: str) -> Any:
        yield session

    def fake_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        dispatched.append({"kwargs": kwargs, "queue": queue})
        return SimpleNamespace(id="task-db-1")

    monkeypatch.setattr(
        tasks,
        "datetime",
        SimpleNamespace(
            datetime=FrozenDateTime,
            timedelta=datetime.timedelta,
            UTC=datetime.UTC,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "croniter",
        SimpleNamespace(croniter=lambda *args, **kwargs: FakeCron()),
    )
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "true")
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: FakeSession)
    monkeypatch.setattr("app.db.rls.sqlalchemy_rls_context", fake_rls_context)
    monkeypatch.setattr(tasks.run_scheduled_goal, "apply_async", fake_apply_async)

    result = tasks.fire_due_schedules()

    assert result["schedules_checked"] == 1
    assert result["schedules_fired"] == 0
    assert dispatched == []


def test_fire_due_schedules_skips_recent_db_interval_schedule(monkeypatch: Any) -> None:
    from app.scaling import tasks

    dispatched: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []

    class FakeResult:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def scalars(self) -> FakeResult:
            return self

        def all(self) -> list[Any]:
            return self._rows

    class FakeSession:
        def __init__(self) -> None:
            self._results = [
                FakeResult([SimpleNamespace(id="tenant-1")]),
                FakeResult(
                    [
                        SimpleNamespace(
                            id="sched-db-recent",
                            tenant_id="tenant-1",
                            agent_id="agent-1",
                            goal_id_template="Compile DB report",
                            trigger_type="interval",
                            cron_expression="",
                            timezone="UTC",
                            interval_seconds=3600,
                            webhook_token="",
                            event_channel="",
                            fire_at_iso="",
                            condition="",
                            description="",
                            paused=False,
                            last_fired_at=datetime.datetime.now(datetime.UTC).replace(
                                tzinfo=None
                            ),
                        )
                    ]
                ),
            ]

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def execute(self, query: Any) -> FakeResult:
            return self._results.pop(0)

    @asynccontextmanager
    async def fake_rls_context(session: Any, tenant_id: str) -> Any:
        assert tenant_id == "tenant-1"
        yield session

    def fake_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        dispatched.append({"kwargs": kwargs, "queue": queue})
        return SimpleNamespace(id="task-db-1")

    async def fake_update_last_fired_at(
        tenant_id: str,
        schedule_id: str,
        fired_at: datetime.datetime,
    ) -> None:
        updates.append(
            {"tenant_id": tenant_id, "schedule_id": schedule_id, "fired_at": fired_at}
        )

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "true")
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: FakeSession)
    monkeypatch.setattr("app.db.rls.sqlalchemy_rls_context", fake_rls_context)
    monkeypatch.setattr(tasks.run_scheduled_goal, "apply_async", fake_apply_async)
    monkeypatch.setattr(
        tasks,
        "_update_db_schedule_last_fired_at",
        fake_update_last_fired_at,
        raising=False,
    )

    result = tasks.fire_due_schedules()

    assert result["schedules_checked"] == 1
    assert result["schedules_fired"] == 0
    assert dispatched == []
    assert updates == []


def test_fire_due_schedules_merges_redis_and_db_without_duplicate(monkeypatch: Any) -> None:
    from app.scaling import tasks

    redis_store = {
        "schedule:tenant-1:sched-shared": json.dumps(
            {
                "schedule_id": "sched-shared",
                "tenant_id": "tenant-1",
                "goal_template": "Compile shared report",
                "agent_id": "agent-redis",
                "trigger_type": "interval",
                "interval_seconds": 60,
                "paused": False,
            }
        )
    }
    dispatched: list[dict[str, Any]] = []
    dispatched_scheduled: list[dict[str, Any]] = []

    class FakeRedis:
        def scan_iter(self, *, match: str, count: int) -> list[str]:
            return list(redis_store)

        def get(self, key: str) -> str | None:
            return redis_store.get(key)

        def set(self, key: str, value: str) -> None:
            redis_store[key] = value

    class FakeResult:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def scalars(self) -> FakeResult:
            return self

        def all(self) -> list[Any]:
            return self._rows

    class FakeSession:
        def __init__(self) -> None:
            self._results = [
                FakeResult([SimpleNamespace(id="tenant-1")]),
                FakeResult(
                    [
                        SimpleNamespace(
                            id="sched-shared",
                            tenant_id="tenant-1",
                            agent_id="agent-db",
                            goal_id_template="Compile shared report from DB",
                            trigger_type="interval",
                            cron_expression="",
                            timezone="UTC",
                            interval_seconds=60,
                            webhook_token="",
                            event_channel="",
                            fire_at_iso="",
                            condition="",
                            description="",
                            paused=False,
                            last_fired_at=None,
                        ),
                        SimpleNamespace(
                            id="sched-db-only",
                            tenant_id="tenant-1",
                            agent_id="agent-db-only",
                            goal_id_template="Compile DB-only report",
                            trigger_type="interval",
                            cron_expression="",
                            timezone="UTC",
                            interval_seconds=60,
                            webhook_token="",
                            event_channel="",
                            fire_at_iso="",
                            condition="",
                            description="",
                            paused=False,
                            last_fired_at=None,
                        ),
                    ]
                ),
            ]

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def execute(self, query: Any) -> FakeResult:
            return self._results.pop(0)

    @asynccontextmanager
    async def fake_rls_context(session: Any, tenant_id: str) -> Any:
        yield session

    def fake_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        dispatched.append({"kwargs": kwargs, "queue": queue})
        return SimpleNamespace(id=f"task-{len(dispatched)}")

    def fake_scheduled_apply_async(*, kwargs: dict[str, Any], queue: str) -> SimpleNamespace:
        dispatched_scheduled.append({"kwargs": kwargs, "queue": queue})
        return SimpleNamespace(id=f"scheduled-task-{len(dispatched_scheduled)}")

    monkeypatch.setenv("REDIS_URL", "redis://test")
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "true")
    monkeypatch.setitem(
        sys.modules,
        "redis",
        SimpleNamespace(from_url=lambda *args, **kwargs: FakeRedis()),
    )
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: FakeSession)
    monkeypatch.setattr("app.db.rls.sqlalchemy_rls_context", fake_rls_context)
    monkeypatch.setattr(tasks.run_goal, "apply_async", fake_apply_async)
    monkeypatch.setattr(tasks.run_scheduled_goal, "apply_async", fake_scheduled_apply_async)

    result = tasks.fire_due_schedules()

    assert result["schedules_checked"] == 2
    assert result["schedules_fired"] == 2
    assert dispatched == []
    assert [call["kwargs"]["goal_template"] for call in dispatched_scheduled] == [
        "Compile shared report",
        "Compile DB-only report",
    ]
    assert all(
        call["kwargs"]["fire_instance_id"] == result["checked_at"]
        for call in dispatched_scheduled
    )


def test_fire_due_schedules_skips_unavailable_db(monkeypatch: Any, caplog: Any) -> None:
    from app.scaling import tasks

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "true")
    monkeypatch.setattr(
        "app.db.session.get_session_factory",
        lambda: (_ for _ in ()).throw(RuntimeError("database not configured")),
    )

    result = tasks.fire_due_schedules()

    assert result["status"] == "ok"
    assert result["schedules_checked"] == 0
    assert result["schedules_fired"] == 0
    # Note: log message goes through structlog (stdout), not caplog — verify behavior only
