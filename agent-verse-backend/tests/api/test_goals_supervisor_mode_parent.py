"""CORE-07: POST /goals workflow_mode=supervisor creates and enqueues a parent goal.

It used to run SupervisorAgent inside the HTTP request — awaiting every sub-goal
(up to 300 s each) — create no parent goal and answer ``goal_id: ""``. Now the
request only submits a parent goal whose own graph runs the supervisor fan-out
(on a worker, with sub-goals on the dedicated sub-goal pool — CORE-09) and
returns the parent's id at once.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.agent.supervisor import SUBGOAL_MARKER
from app.api.goals import router as goals_router
from app.core.errors import PlatformError
from app.services.goal_service import GRAPH_CONTEXT_KEYS, GoalService
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-sup", plan=PlanTier.PROFESSIONAL, api_key_id="kid-s")
_KEY = "ak_test_supervisor_parent"


def _app(svc: Any) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(PlatformError)
    async def _platform(_: Request, exc: PlatformError) -> JSONResponse:
        return JSONResponse(exc.to_dict(), status_code=exc.http_status)

    app.include_router(goals_router)
    app.state.goal_service = svc
    app.state._app_provider = AsyncMock()
    return app


class _NoInRequestSupervisor:
    def __init__(self, **kwargs: Any) -> None:
        raise AssertionError("the supervisor must not run inside the request")


def test_supervisor_submission_returns_the_parent_goal_id_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.agent.supervisor.SupervisorAgent", _NoInRequestSupervisor)
    svc = AsyncMock()
    svc._check_budget_preflight = AsyncMock(return_value=None)
    svc.submit_goal.return_value = {"goal_id": "parent-1", "status": "planning", "goal": "g"}

    resp = TestClient(_app(svc), raise_server_exceptions=False).post(
        "/goals",
        json={"goal": "Research three markets", "workflow_mode": "supervisor",
              "supervisor_max_parallel": 3},
        headers={"X-API-Key": _KEY},
    )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["goal_id"] == "parent-1" and body["mode"] == "supervisor"
    kwargs = svc.submit_goal.await_args.kwargs
    assert kwargs["workflow_mode"] == "supervisor"
    assert kwargs["execution_context"]["supervisor_max_parallel"] == 3


def test_supervisor_submission_failure_does_not_echo_the_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.agent.supervisor.SupervisorAgent", _NoInRequestSupervisor)
    svc = AsyncMock()
    svc._check_budget_preflight = AsyncMock(return_value=None)
    svc.submit_goal.side_effect = RuntimeError("secret-internal-dsn postgresql://u:pw@db/x")

    resp = TestClient(_app(svc), raise_server_exceptions=False).post(
        "/goals",
        json={"goal": "Research three markets", "workflow_mode": "supervisor"},
        headers={"X-API-Key": _KEY},
    )

    assert resp.status_code == 500
    assert "secret-internal-dsn" not in resp.text and "postgresql://" not in resp.text


class _Queue:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []

    def enqueue_goal(self, **kwargs: Any) -> str:
        self.enqueued.append(kwargs)
        return "task"


async def test_supervisor_parent_goal_compiles_the_in_graph_supervisor() -> None:
    queue = _Queue()
    svc = GoalService(task_queue=queue)
    result = await svc.submit_goal(
        goal="Research three markets", priority="normal", dry_run=False, tenant_ctx=_CTX,
        workflow_mode="supervisor", execution_context={"supervisor_max_parallel": 3},
    )
    ctx = svc._goals[result["goal_id"]].execution_context
    # The worker compiles its graph from this snapshot: the supervisor node is on.
    assert ctx["agent_pattern_flags"]["enable_supervisor"] is True
    assert queue.enqueued[0]["workflow_mode"] == "supervisor"
    # The parent runs on the main pool; only its sub-goals use the sub-goal pool.
    assert "subgoal" not in queue.enqueued[0]
    # The fan-out width reaches the graph context (API and worker paths).
    assert "supervisor_max_parallel" in GRAPH_CONTEXT_KEYS
    assert SUBGOAL_MARKER not in ctx


async def test_supervisor_node_uses_the_requested_fan_out_width() -> None:
    from unittest.mock import MagicMock, patch

    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.agent.supervisor import SupervisionResult
    from app.providers.fake import FakeProvider

    p = FakeProvider()
    graph = AgentGraph(planner=p, executor=p, verifier=p, enable_supervisor=True)
    graph._goal_service = MagicMock()
    agent_state = AgentState(goal="Research three markets", tenant_ctx=_CTX)
    agent_state.context["supervisor_max_parallel"] = 3
    graph._tenant_ctx_ref = _CTX
    seen: dict[str, Any] = {}

    async def _run(self: Any, **kwargs: Any) -> SupervisionResult:
        seen["max_parallel"] = self._max_parallel
        return SupervisionResult(success=True, tasks=[], synthesized_result="ok")

    with patch("app.agent.supervisor.SupervisorAgent.run", new=_run):
        await graph._node_supervisor_check(
            {"goal": agent_state.goal, "tenant_ctx": _CTX, "iteration": 0,
             "agent_state": agent_state}
        )
    assert seen["max_parallel"] == 3
