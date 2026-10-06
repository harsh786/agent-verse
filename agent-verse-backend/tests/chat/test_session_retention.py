"""CHAT-SEC-3 unit tests: ttl_days is settable, and the beat task is wired.

The purge itself runs on a real Postgres in test_session_retention_integration.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.chat.router import router as chat_router
from app.chat.service import ChatService
from app.tenancy.context import PlanTier, TenantContext

_A = TenantContext("t-ttl", PlanTier.FREE, "user:a", roles=("viewer",), user_id="user-a")


def _client() -> TestClient:
    app = FastAPI()
    app.state.chat_service = ChatService()

    @app.middleware("http")
    async def _caller(request: Any, call_next: Any) -> Any:
        request.state.tenant = _A
        return await call_next(request)

    app.include_router(chat_router)
    return TestClient(app)


def test_ttl_days_can_be_set_validated_and_cleared() -> None:
    c = _client()
    sid = c.post("/chat/sessions", json={}).json()["id"]
    assert c.get(f"/chat/sessions/{sid}").json()["ttl_days"] is None
    assert c.patch(f"/chat/sessions/{sid}", json={"ttl_days": 7}).json()["ttl_days"] == 7
    for bad in (0, -1, 3651):
        assert c.patch(f"/chat/sessions/{sid}", json={"ttl_days": bad}).status_code == 422
    # Another field's update leaves the ttl alone; an explicit null clears it.
    assert c.patch(f"/chat/sessions/{sid}", json={"title": "x"}).json()["ttl_days"] == 7
    assert c.patch(f"/chat/sessions/{sid}", json={"ttl_days": None}).json()["ttl_days"] is None


def test_the_purge_runs_hourly_on_the_maintenance_queue() -> None:
    from app.scaling.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["purge-expired-chat-sessions-hourly"]
    assert entry["task"] == "agentverse.maintenance.purge_expired_chat_sessions"
    assert entry["schedule"] == 3600 and entry["options"]["queue"] == "maintenance"
    assert entry["task"] in celery_app.tasks


def test_the_task_queues_transcript_purges_for_owned_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.chat.retention as retention
    import app.db.session as db_session
    import app.services.chat_knowledge as chat_knowledge
    from app.scaling import tasks

    queued: list[dict[str, Any]] = []

    async def fake_purge(factory: Any, *, on_owned_sessions: Any) -> Any:
        assert factory == "system-factory"
        await on_owned_sessions("t1", "user-a", ["s1", "s2"])
        return retention.RetentionReport(sessions_deleted=2)

    monkeypatch.setattr(retention, "purge_expired_chat_sessions", fake_purge)
    monkeypatch.setattr(db_session, "get_system_session_factory", lambda: "system-factory")
    monkeypatch.setattr(
        chat_knowledge, "enqueue_purge_continuation",
        lambda tid, **kw: queued.append({"tenant_id": tid, **kw}),
    )
    out = tasks.purge_expired_chat_sessions.run()
    assert out["sessions_deleted"] == 2
    assert queued == [{"tenant_id": "t1", "user_id": "user-a", "session_ids": ["s1", "s2"],
                       "unconsented_only": False}]


def test_the_task_raises_when_the_purge_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.chat.retention as retention
    import app.db.session as db_session
    from app.scaling import tasks

    async def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("query would be affected by row-level security policy")

    monkeypatch.setattr(retention, "purge_expired_chat_sessions", broken)
    monkeypatch.setattr(db_session, "get_system_session_factory", lambda: "f")
    with pytest.raises(RuntimeError):
        tasks.purge_expired_chat_sessions.run()
