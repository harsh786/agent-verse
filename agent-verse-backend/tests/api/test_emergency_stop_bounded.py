"""INC-07 / INC-03: the tenant emergency stop is bounded and reports the real handoff.

POST /governance/emergency-stop enumerated every non-terminal goal of the tenant
(no LIMIT) and cancelled them one by one inside the HTTP request — a large queued
backlog timed the call out. It now cancels at most one bounded page inline and
hands the rest to the keyset-batched ``cancel_goals_for_emergency_stop`` task.
``celery_signal_sent`` used to be a hard-coded ``True``; it now says whether
that task was really enqueued.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import governance as gov_api
from app.api.governance import router as governance_router
from app.governance.hitl import HITLGateway
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(
    tenant_id="t-estop-bounded", plan=PlanTier.ENTERPRISE, api_key_id="kid", roles=("admin",)
)


def _client(goal_service: Any) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(governance_router)
    app.state.hitl_gateway = HITLGateway()
    app.state.goal_service = goal_service
    app.state._redis = fakeredis.aioredis.FakeRedis()
    return TestClient(app, raise_server_exceptions=False)


def _goal_service(n_goals: int) -> MagicMock:
    svc = MagicMock()
    goals = [f"g-{i}" for i in range(n_goals)]

    async def _active(ctx: Any, *, limit: int | None = None) -> list[str]:
        return goals[:limit] if limit is not None else goals

    svc.active_goal_ids = _active
    svc.cancel_goal = AsyncMock()
    return svc


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _apply_async(*, kwargs: dict[str, Any], **_: Any) -> None:
        calls.append(kwargs)

    from app.scaling import tasks

    monkeypatch.setattr(tasks.cancel_goals_for_emergency_stop, "apply_async", _apply_async)
    return calls


def test_large_backlog_is_cancelled_off_the_request_path(
    enqueued: list[dict[str, Any]],
) -> None:
    svc = _goal_service(gov_api._ESTOP_INLINE_CANCEL_LIMIT * 5)
    resp = _client(svc).post("/governance/emergency-stop", headers={"X-API-Key": "k"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert svc.cancel_goal.await_count == gov_api._ESTOP_INLINE_CANCEL_LIMIT
    assert enqueued == [{"tenant_id": "t-estop-bounded", "org_id": None, "rollback": False}]
    assert body["celery_signal_sent"] is True
    assert body["goal_cancellation"] == "enqueued"
    assert body["partial"] is False


def test_small_tenant_is_cancelled_inline_without_a_task(
    enqueued: list[dict[str, Any]],
) -> None:
    svc = _goal_service(3)
    body = _client(svc).post("/governance/emergency-stop", headers={"X-API-Key": "k"}).json()
    assert svc.cancel_goal.await_count == 3
    assert enqueued == []
    assert body["celery_signal_sent"] is False
    assert body["goal_cancellation"] == "inline"


def test_failed_enqueue_is_reported_not_claimed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks

    def _broker_down(**_: Any) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.cancel_goals_for_emergency_stop, "apply_async", _broker_down)
    svc = _goal_service(gov_api._ESTOP_INLINE_CANCEL_LIMIT + 1)
    body = _client(svc).post("/governance/emergency-stop", headers={"X-API-Key": "k"}).json()
    assert body["celery_signal_sent"] is False
    assert body["goal_cancellation"] == "not_enqueued"
    assert body["partial"] is True
    assert any("cancel_task_not_enqueued" in e for e in body["errors"])


def test_rollback_is_opt_in_and_reaches_every_cancel(
    enqueued: list[dict[str, Any]],
) -> None:
    """a08-F200-03: ``rollback=true`` is recorded on the stop, passed to every
    inline cancel and to the batched task, and audited; the default is no rollback."""
    import asyncio
    import json

    from app.governance.audit import AuditLog

    svc = _goal_service(gov_api._ESTOP_INLINE_CANCEL_LIMIT + 1)
    client = _client(svc)
    audit = AuditLog()
    client.app.state.audit_log = audit  # type: ignore[attr-defined]
    body = client.post(
        "/governance/emergency-stop?rollback=true", headers={"X-API-Key": "k"}
    ).json()
    assert body["rollback_requested"] is True
    assert all(c.kwargs.get("rollback") is True for c in svc.cancel_goal.await_args_list)
    assert enqueued == [{"tenant_id": "t-estop-bounded", "org_id": None, "rollback": True}]
    raw = asyncio.run(
        client.app.state._redis.get("emergency_stop:t-estop-bounded")  # type: ignore[attr-defined]
    )
    assert json.loads(raw)["rollback"] is True
    notes = [e.note for e in audit.query(tenant_ctx=_CTX)]
    assert any("rollback=true" in n for n in notes)

    svc2 = _goal_service(1)
    body2 = _client(svc2).post("/governance/emergency-stop", headers={"X-API-Key": "k"}).json()
    assert body2["rollback_requested"] is False
    assert not svc2.cancel_goal.await_args.kwargs.get("rollback", False)
