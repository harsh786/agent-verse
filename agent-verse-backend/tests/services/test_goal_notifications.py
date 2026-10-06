"""a08-F196-05: opt-in goal outcome notifications.

Owner decision: ``goalComplete`` / ``goalFailed`` are OFF unless the tenant opts
in; a consumer on the goal lifecycle stream (published by in-process goals and
the Celery worker alike) sends the outcome through the tenant's notification
channels once per goal outcome across replicas / redeliveries. Content is
sanitized; a channel failure never escapes (logged + counted).
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

import fakeredis
import pytest

from app.observability.metrics import GOAL_NOTIFICATION_TOTAL
from app.services import notification_service as ns_mod
from app.services.goal_notifications import (
    GoalNotificationConsumer,
    claim_key,
    sanitize_summary,
)
from app.services.notification_prefs import prefs_key, serialize_prefs
from app.services.notification_service import NotificationChannel, NotificationService


class _Recorder:
    """Stands in for NotificationService; records every outcome sent."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def notify_goal_outcome(self, **kw: Any) -> dict[str, Any]:
        self.sent.append(kw)
        return {"sent": 1, "channels": [{"channel_id": "c1", "status": "sent"}]}


def _goal_db(goal_text: str = "Summarise Q3 revenue", error: str = "") -> Any:
    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        def begin(self) -> Any:
            @asynccontextmanager
            async def _b() -> Any:
                yield None

            return _b()

        async def execute(self, stmt: Any, params: Any = None) -> Any:
            class _R:
                def first(self) -> Any:
                    if "FROM goals" in str(stmt):
                        return (goal_text, error)
                    return None

            return _R()

    return lambda: _Session()


@pytest.fixture
async def redis() -> Any:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield client
    await client.aclose()


async def _opt_in(redis: Any, tenant: str = "t1", **prefs: bool) -> None:
    await redis.set(prefs_key(tenant), serialize_prefs(prefs))


def _event(goal: str = "g1", tenant: str = "t1") -> dict[str, Any]:
    return {"tenant_id": tenant, "goal_id": goal, "status": "complete"}


def _count(outcome: str, result: str) -> float:
    return GOAL_NOTIFICATION_TOTAL.labels(outcome=outcome, result=result)._value.get()


async def test_off_by_default_sends_nothing(redis: Any) -> None:
    rec = _Recorder()
    consumer = GoalNotificationConsumer(redis=redis, notification_service=rec)
    assert await consumer.handle_event("goal.completed", _event()) == "not_opted_in"
    assert await consumer.handle_event("goal.failed", _event()) == "not_opted_in"
    assert rec.sent == []
    assert await redis.get(claim_key("t1", "g1", "complete")) is None


async def test_prefs_saved_before_opt_in_existed_do_not_enable_it(redis: Any) -> None:
    # Legacy (unversioned) record: Settings used to save goalComplete=true as a
    # displayed default nobody chose.
    await redis.set(prefs_key("t1"), json.dumps({"goalComplete": True, "goalFailed": True}))
    rec = _Recorder()
    consumer = GoalNotificationConsumer(redis=redis, notification_service=rec)
    assert await consumer.handle_event("goal.completed", _event()) == "not_opted_in"
    assert rec.sent == []


async def test_opted_in_sends_once_across_replicas_and_redelivery(redis: Any) -> None:
    await _opt_in(redis, goalComplete=True)
    rec = _Recorder()
    replica_a = GoalNotificationConsumer(
        redis=redis, notification_service=rec, db_session_factory=_goal_db()
    )
    replica_b = GoalNotificationConsumer(
        redis=redis, notification_service=rec, db_session_factory=_goal_db()
    )
    before = _count("complete", "duplicate")
    assert await replica_a.handle_event("goal.completed", _event()) == "sent"
    assert await replica_b.handle_event("goal.completed", _event()) == "duplicate"
    assert await replica_a.handle_event("goal.completed", _event()) == "duplicate"  # redelivery
    assert len(rec.sent) == 1
    assert rec.sent[0] == {
        "goal_id": "g1",
        "status": "complete",
        "tenant_id": "t1",
        "summary": "Summarise Q3 revenue",
    }
    assert _count("complete", "duplicate") == before + 2
    # Opting into completions does not opt into failures.
    assert await replica_a.handle_event("goal.failed", _event("g2")) == "not_opted_in"


async def test_failed_goal_sends_the_sanitized_failure_reason(redis: Any) -> None:
    await _opt_in(redis, goalFailed=True)
    rec = _Recorder()
    reason = "Tool call failed: password=hunter2-very-secret while posting to mail a@b.example"
    consumer = GoalNotificationConsumer(
        redis=redis, notification_service=rec, db_session_factory=_goal_db(error=reason)
    )
    assert await consumer.handle_event("goal.failed", _event()) == "sent"
    sent = rec.sent[0]
    assert sent["status"] == "failed"
    assert "Tool call failed" in sent["summary"]
    assert "hunter2" not in sent["summary"]
    assert "a@b.example" not in sent["summary"]


def test_summary_is_single_line_and_bounded() -> None:
    out = sanitize_summary("line one\nline two " + "x" * 1000)
    assert "\n" not in out and len(out) <= 280


async def test_channel_errors_are_swallowed_logged_and_counted(
    redis: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    await _opt_in(redis, goalComplete=True)

    async def _boom(url: str, payload: dict[str, Any]) -> None:
        raise ConnectionError("slack is down")

    monkeypatch.setattr(ns_mod, "_post_public", _boom)
    svc = NotificationService()
    svc._channels["t1"] = [
        NotificationChannel(
            channel_id="c1",
            tenant_id="t1",
            channel_type="slack",
            config={"webhook_url": "https://hooks.slack.example/x"},
        )
    ]
    consumer = GoalNotificationConsumer(redis=redis, notification_service=svc)
    before = _count("complete", "channel_failed")
    with caplog.at_level(logging.WARNING):
        assert await consumer.handle_event("goal.completed", _event()) == "channel_failed"
    assert _count("complete", "channel_failed") == before + 1
    assert any("goal_notification_channels_failed" in r.getMessage() for r in caplog.records)


async def test_unexpected_send_error_never_escapes(redis: Any) -> None:
    await _opt_in(redis, goalComplete=True)

    class _Broken:
        async def notify_goal_outcome(self, **kw: Any) -> dict[str, Any]:
            raise RuntimeError("bug")

    consumer = GoalNotificationConsumer(redis=redis, notification_service=_Broken())
    before = _count("complete", "error")
    assert await consumer.handle_event("goal.completed", _event()) == "error"
    assert _count("complete", "error") == before + 1


async def test_prefs_read_error_leaves_the_event_pending() -> None:
    class _Down:
        async def get(self, key: str) -> Any:
            raise ConnectionError("redis down")

    consumer = GoalNotificationConsumer(redis=_Down(), notification_service=_Recorder())
    with pytest.raises(ConnectionError):
        await consumer.handle_event("goal.completed", _event())


async def test_stream_message_is_parsed_and_score_events_ignored(redis: Any) -> None:
    await _opt_in(redis, goalComplete=True)
    rec = _Recorder()
    consumer = GoalNotificationConsumer(redis=redis, notification_service=rec)
    await consumer._handle({"channel": b"goal.score_below", "data": json.dumps(_event())})
    await consumer._handle({"channel": b"goal.completed", "data": json.dumps(_event("g9"))})
    assert [s["goal_id"] for s in rec.sent] == ["g9"]


async def test_put_opt_in_then_a_completion_is_notified(redis: Any) -> None:
    """End to end through the preferences API: GET shows off, PUT opts in."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.tenants import router as tenants_router
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.middleware import TenantMiddleware

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return TenantContext("t1", PlanTier.STARTER, key, roles=("admin",))

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(tenants_router)
    app.state._redis = redis
    client = TestClient(app)
    h = {"X-API-Key": "k"}
    prefs = client.get("/tenants/me/notifications", headers=h).json()
    assert prefs["goalComplete"] is False and prefs["goalFailed"] is False
    assert client.put(
        "/tenants/me/notifications", json={"goalComplete": True}, headers=h
    ).json()["preferences"]["budgetAlert"] is True

    rec = _Recorder()
    consumer = GoalNotificationConsumer(redis=redis, notification_service=rec)
    assert await consumer.handle_event("goal.completed", _event()) == "sent"
