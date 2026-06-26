"""Tests for /schedules, /nl, /webhooks, and /events API endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.api.schedules import (
    events_router,
    nl_router,
    webhooks_router,
)
from app.api.schedules import (
    router as schedules_router,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore

_CTX = TenantContext(tenant_id="tid-sched", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_schedkey"


def _make_app(
    schedule_store: ScheduleStore | None = None,
    nl_scheduler: Any | None = None,
    agent_store: Any | None = None,
) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(schedules_router)
    app.include_router(nl_router)
    app.include_router(webhooks_router)
    app.include_router(events_router)
    app.state.schedule_store = schedule_store or ScheduleStore()
    app.state.nl_scheduler = nl_scheduler or AsyncMock()
    if agent_store is not None:
        app.state.agent_store = agent_store
    return app


class _FakeAgentStore:
    def __init__(self, agent_ids: set[str]) -> None:
        self._agent_ids = agent_ids

    def get(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, str] | None:
        if agent_id not in self._agent_ids:
            return None
        return {"agent_id": agent_id, "tenant_id": tenant_ctx.tenant_id}


class _AsyncCreateStore(ScheduleStore):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []

    def create(
        self,
        *,
        goal_id: str,
        spec: TriggerSpec,
        tenant_ctx: TenantContext,
        agent_id: str = "",
        goal_template: str = "",
    ) -> str:
        self.events.append("sync-create-called")
        return super().create(
            goal_id=goal_id,
            spec=spec,
            tenant_ctx=tenant_ctx,
            agent_id=agent_id,
            goal_template=goal_template,
        )

    async def create_async(
        self,
        *,
        goal_id: str,
        spec: TriggerSpec,
        tenant_ctx: TenantContext,
        agent_id: str = "",
        goal_template: str = "",
    ) -> str:
        self.events.append("async-create-start")
        await asyncio.sleep(0)
        schedule_id = self.create(
            goal_id=goal_id,
            spec=spec,
            tenant_ctx=tenant_ctx,
            agent_id=agent_id,
            goal_template=goal_template,
        )
        self.events.append("async-create-finished")
        return schedule_id


# ---------------------------------------------------------------------------
# Schedule CRUD
# ---------------------------------------------------------------------------

def test_list_schedules_empty() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/schedules", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_schedule() -> None:
    client = TestClient(
        _make_app(agent_store=_FakeAgentStore({"agent-abc"})),
        raise_server_exceptions=False,
    )
    resp = client.post(
        "/schedules",
        json={
            "trigger_type": "cron",
            "cron_expr": "0 9 * * 1-5",
            "name": "weekday-9am",
            "goal_template": "Run daily report",
            "agent_id": "agent-abc",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "schedule_id" in body
    assert body["paused"] is False
    assert body["spec"]["trigger_type"] == "cron"
    assert body["agent_id"] == "agent-abc"
    assert body["goal_template"] == "Run daily report"


def test_create_schedule_awaits_create_async_before_returning() -> None:
    store = _AsyncCreateStore()
    client = TestClient(_make_app(schedule_store=store), raise_server_exceptions=False)

    resp = client.post(
        "/schedules",
        json={"trigger_type": "once", "name": "one-shot"},
        headers={"X-API-Key": _VALID_KEY},
    )

    assert resp.status_code == 201
    assert store.events == [
        "async-create-start",
        "sync-create-called",
        "async-create-finished",
    ]


def test_create_schedule_with_invalid_agent_id_returns_404() -> None:
    client = TestClient(
        _make_app(agent_store=_FakeAgentStore(set())),
        raise_server_exceptions=False,
    )
    resp = client.post(
        "/schedules",
        json={
            "trigger_type": "cron",
            "cron_expr": "0 9 * * 1-5",
            "goal_template": "Run daily report",
            "agent_id": "missing-agent",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404


def test_get_schedule() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    create_resp = client.post(
        "/schedules",
        json={"trigger_type": "interval", "interval_seconds": 3600, "name": "hourly"},
        headers={"X-API-Key": _VALID_KEY},
    )
    sched_id = create_resp.json()["schedule_id"]

    get_resp = client.get(f"/schedules/{sched_id}", headers={"X-API-Key": _VALID_KEY})
    assert get_resp.status_code == 200
    assert get_resp.json()["schedule_id"] == sched_id


def test_delete_schedule() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    create_resp = client.post(
        "/schedules",
        json={"trigger_type": "once", "name": "one-shot"},
        headers={"X-API-Key": _VALID_KEY},
    )
    sched_id = create_resp.json()["schedule_id"]

    del_resp = client.delete(f"/schedules/{sched_id}", headers={"X-API-Key": _VALID_KEY})
    assert del_resp.status_code == 204

    get_resp = client.get(f"/schedules/{sched_id}", headers={"X-API-Key": _VALID_KEY})
    assert get_resp.status_code == 404


def test_delete_schedule_returns_500_when_durable_delete_fails(
    monkeypatch: MonkeyPatch,
) -> None:
    @contextlib.asynccontextmanager
    async def noop_rls_context(*args: Any, **kwargs: Any) -> AsyncIterator[None]:
        yield None

    class FailingDeleteSession:
        async def __aenter__(self) -> FailingDeleteSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        def begin(self) -> Any:
            return noop_rls_context()

        async def execute(self, statement: Any) -> Any:
            if str(statement).startswith("DELETE"):
                raise RuntimeError("db delete failed")
            return SimpleNamespace(rowcount=0)

    monkeypatch.setattr(
        "app.db.rls.sqlalchemy_rls_context",
        lambda *args, **kwargs: noop_rls_context(),
    )

    store = ScheduleStore(db_session_factory=lambda: FailingDeleteSession())
    store._data[(_CTX.tenant_id, "sched-delete-fail")] = {
        "schedule_id": "sched-delete-fail",
        "goal_id": "g1",
        "agent_id": "",
        "goal_template": "",
        "spec": TriggerSpec(trigger_type=TriggerType.ONCE),
        "paused": False,
    }
    client = TestClient(_make_app(schedule_store=store), raise_server_exceptions=False)

    resp = client.delete(
        "/schedules/sched-delete-fail", headers={"X-API-Key": _VALID_KEY}
    )

    assert resp.status_code == 500


def test_pause_resume_schedule() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    create_resp = client.post(
        "/schedules",
        json={"trigger_type": "interval", "interval_seconds": 60},
        headers={"X-API-Key": _VALID_KEY},
    )
    sched_id = create_resp.json()["schedule_id"]

    # Pause.
    pause_resp = client.post(
        f"/schedules/{sched_id}/pause", headers={"X-API-Key": _VALID_KEY}
    )
    assert pause_resp.status_code == 200
    assert pause_resp.json()["paused"] is True

    # Resume.
    resume_resp = client.post(
        f"/schedules/{sched_id}/resume", headers={"X-API-Key": _VALID_KEY}
    )
    assert resume_resp.status_code == 200
    assert resume_resp.json()["paused"] is False


def test_pause_nonexistent_schedule_returns_404() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/schedules/ghost-id/pause", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# NL schedule creation
# ---------------------------------------------------------------------------

def test_nl_schedule_creation() -> None:
    mock_nl = AsyncMock()
    mock_nl.parse.return_value = [
        TriggerSpec(
            trigger_type=TriggerType.CRON,
            cron_expression="0 9 * * 1-5",
            timezone="UTC",
            description="Every weekday at 9 AM UTC",
        )
    ]
    client = TestClient(
        _make_app(
            nl_scheduler=mock_nl,
            agent_store=_FakeAgentStore({"agent-abc"}),
        ),
        raise_server_exceptions=False,
    )

    resp = client.post(
        "/nl/schedule",
        json={"command": "Every weekday at 9 AM UTC", "agent_id": "agent-abc"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["spec"]["trigger_type"] == "cron"
    assert "schedule_id" in body[0]
    assert body[0]["agent_id"] == "agent-abc"
    assert body[0]["goal_template"] == "Every weekday at 9 AM UTC"


def test_nl_schedule_with_invalid_agent_id_returns_404() -> None:
    mock_nl = AsyncMock()
    mock_nl.parse.return_value = [TriggerSpec(trigger_type=TriggerType.CRON)]
    client = TestClient(
        _make_app(
            nl_scheduler=mock_nl,
            agent_store=_FakeAgentStore(set()),
        ),
        raise_server_exceptions=False,
    )

    resp = client.post(
        "/nl/schedule",
        json={"command": "Every weekday at 9 AM UTC", "agent_id": "missing-agent"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 404
    mock_nl.parse.assert_not_awaited()


def test_fire_due_schedules_dispatches_agent_bound_goal(monkeypatch: MonkeyPatch) -> None:
    from app.scaling import tasks

    schedule_payload = {
        "tenant_id": _CTX.tenant_id,
        "trigger_type": "interval",
        "interval_seconds": 60,
        "goal_id": "legacy-goal-fallback",
        "goal_template": "Run daily report",
        "agent_id": "agent-abc",
    }
    redis_store = {"schedule:tid-sched:sched-1": json.dumps(schedule_payload)}
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

    def fake_apply_async(*args: Any, **kwargs: Any) -> SimpleNamespace:
        dispatched.append({"args": args, "kwargs": kwargs})
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

    assert result["schedules_fired"] == 1
    goal_id = dispatched[0]["kwargs"]["kwargs"]["goal_id"]
    assert dispatched[0]["kwargs"] == {
        "kwargs": {
            "goal_id": goal_id,
            "goal_text": "Run daily report",
            "goal_template": "Run daily report",
            "tenant_id": _CTX.tenant_id,
            "agent_id": "agent-abc",
        },
        "queue": "schedules",
    }
    assert goal_id.startswith("sched_")
    assert len(goal_id) <= 32
    assert ":" not in goal_id


def test_nl_schedule_compound_returns_multiple() -> None:
    mock_nl = AsyncMock()
    mock_nl.parse.return_value = [
        TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 8 * * *"),
        TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 14 * * *"),
        TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="0 20 * * *"),
    ]
    client = TestClient(_make_app(nl_scheduler=mock_nl), raise_server_exceptions=False)

    resp = client.post(
        "/nl/schedule",
        json={"command": "At 8 AM, 2 PM, and 8 PM daily"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert len(resp.json()) == 3


# ---------------------------------------------------------------------------
# Webhook trigger
# ---------------------------------------------------------------------------

def test_webhook_trigger() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    # Create a webhook-type schedule — this generates a token automatically.
    create_resp = client.post(
        "/schedules",
        json={"trigger_type": "webhook", "name": "pr-hook"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert create_resp.status_code == 201
    token = create_resp.json()["spec"]["webhook_token"]
    assert token  # Token must have been generated.

    # Fire the webhook.
    fire_resp = client.post(
        f"/webhooks/{token}", headers={"X-API-Key": _VALID_KEY}
    )
    assert fire_resp.status_code == 200
    body = fire_resp.json()
    assert body["status"] == "ok"
    assert "schedule_id" in body


def test_webhook_unknown_token_returns_404() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/webhooks/unknown-token-xyz", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_schedules_require_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    assert client.get("/schedules").status_code == 401
    assert client.post("/nl/schedule", json={"command": "daily"}).status_code == 401
