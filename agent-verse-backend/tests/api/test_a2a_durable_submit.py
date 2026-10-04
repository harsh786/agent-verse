"""A2A-01: the goal is submitted before the 202 and bound to the task.

It used to be submitted by an untracked asyncio task after the 202, so a
restart stranded the task as ``accepted`` with no goal and no callback, and a
submission failure (e.g. the plan's concurrency limit) was invisible to the
caller. Now the 202 carries the goal id, a refused submission is the caller's
error, and with a database nothing runs in-process: the beat reconciler
drives the outcome and the callback.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.a2a import _tasks
from app.api.a2a import router as a2a_router
from app.core.errors import PlatformError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.limits import PlanLimitExceededError
from tests.api._a2a_fakes import FakeGoalService

CALLER = TenantContext(tenant_id="a2a-durable", plan=PlanTier.FREE, api_key_id="k")


def _app(goal_service: Any, db: Any = None) -> FastAPI:
    app = FastAPI()
    app.state.db_session_factory = db
    app.state.goal_service = goal_service

    @app.exception_handler(PlatformError)
    async def _h(_: Any, exc: PlatformError) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.http_status, content={"code": exc.code})

    @app.middleware("http")
    async def _caller(request: Any, call_next: Any) -> Any:
        request.state.tenant = CALLER
        return await call_next(request)

    app.include_router(a2a_router)
    return app


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A2A_SHARED_SECRET", raising=False)
    _tasks.clear()


def test_202_carries_the_submitted_goal_and_the_task_is_bound_to_it() -> None:
    gs = FakeGoalService()
    resp = TestClient(_app(gs)).post("/a2a/tasks", json={"goal": "Summarise X"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["goal_id"] == gs.submitted[0]["goal_id"]
    assert _tasks[body["task_id"]]["goal_id"] == body["goal_id"]
    assert gs.submitted[0]["tenant_ctx"] is CALLER


def test_a_refused_submission_is_the_callers_error_and_the_task_is_not_left_open() -> None:
    gs = MagicMock()
    gs.submit_goal = AsyncMock(side_effect=PlanLimitExceededError("Concurrent goal limit"))
    resp = TestClient(_app(gs)).post("/a2a/tasks", json={"goal": "X"})
    assert resp.status_code == 429
    assert [t["status"] for t in _tasks.values()] == ["error"]


def test_an_unexpected_submission_failure_is_503_not_accepted() -> None:
    gs = MagicMock()
    gs.submit_goal = AsyncMock(side_effect=RuntimeError("queue down"))
    resp = TestClient(_app(gs), raise_server_exceptions=False).post(
        "/a2a/tasks", json={"goal": "X"}
    )
    assert resp.status_code == 503
    assert [t["status"] for t in _tasks.values()] == ["error"]


def test_with_a_database_no_in_process_watcher_is_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Durability: the outcome is driven by reconcile_a2a_tasks, not this process."""
    import app.api.a2a as a2a

    persisted: list[dict[str, Any]] = []
    bound: list[tuple[str, str]] = []

    async def _persist(task_id: str, data: dict[str, Any], db: Any) -> None:
        persisted.append(data)

    async def _bind(task_id: str, tenant_id: str, goal_id: str, db: Any) -> None:
        bound.append((task_id, goal_id))

    monkeypatch.setattr(a2a, "_persist_task", _persist)
    monkeypatch.setattr(a2a, "_bind_task_goal", _bind)
    created: list[Any] = []
    real_create_task = __import__("asyncio").create_task
    monkeypatch.setattr(
        "asyncio.create_task", lambda coro, **kw: created.append(coro) or real_create_task(coro)
    )
    resp = TestClient(_app(FakeGoalService(), db=object())).post(
        "/a2a/tasks", json={"goal": "X", "callback_url": "https://93.184.216.34/cb"}
    )
    assert resp.status_code == 202
    assert bound == [(resp.json()["task_id"], resp.json()["goal_id"])]
    assert not [c for c in created if "_watch_outcome" in getattr(c, "__qualname__", "")]


def test_polling_an_open_task_reconciles_it_from_its_goal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A peer polling GET sees the outcome as soon as the goal ends (not at the beat)."""
    import app.api.a2a as a2a
    import app.services.a2a_tasks as svc

    state = {"status": "working"}

    async def _get(task_id: str, db: Any, tenant_id: str) -> dict[str, Any]:
        assert tenant_id == CALLER.tenant_id
        return {"task_id": task_id, "status": state["status"], "goal_id": "g1"}

    reconciled: list[tuple[str, str]] = []

    async def _reconcile(db: Any, *, task_id: str, tenant_id: str, result_text: Any) -> bool:
        reconciled.append((task_id, tenant_id))
        state["status"] = "complete"
        return True

    monkeypatch.setattr(a2a, "_get_task", _get)
    monkeypatch.setattr(svc, "reconcile_task", _reconcile)
    client = TestClient(_app(FakeGoalService(), db=object()))
    assert client.get("/a2a/tasks/tk1").json()["status"] == "complete"
    assert reconciled == [("tk1", CALLER.tenant_id)]
    # A terminal task is returned as stored, no reconcile.
    assert client.get("/a2a/tasks/tk1").json()["status"] == "complete"
    assert len(reconciled) == 1
