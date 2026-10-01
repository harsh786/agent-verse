"""CORE-30: POST /goals workflow_mode=debate submits a goal; the debate runs in it.

The API used to run up to 3 rounds of N-agent proposal/critique/vote LLM calls
inside the HTTP request, before the goal existed: bounded by proxy timeouts,
holding API capacity, and its paid result lost on a disconnect or restart. Now
the request only submits the goal (with the in-graph debate node compiled in)
and returns its id at once; the debate runs on the worker as part of the goal —
queued, durable, charged to the goal and visible as goal events.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.goals import router as goals_router
from app.core.errors import PlatformError
from app.services.goal_service import GRAPH_CONTEXT_KEYS, GoalService
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-deb", plan=PlanTier.PROFESSIONAL, api_key_id="kid-d")
_KEY = "ak_test_debate_queued"


def _app(svc: Any, provider: Any) -> FastAPI:
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
    app.state._app_provider = provider
    return app


class _NoInRequestDebate:
    def __init__(self, **kwargs: Any) -> None:
        raise AssertionError("the debate must not run inside the request")


def test_debate_submission_returns_the_goal_id_without_any_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.agent.debate.DebateOrchestrator", _NoInRequestDebate)
    provider = AsyncMock()
    svc = AsyncMock()
    svc._check_budget_preflight = AsyncMock(return_value=None)
    svc.submit_goal.return_value = {"goal_id": "deb-1", "status": "planning", "goal": "g"}

    resp = TestClient(_app(svc, provider), raise_server_exceptions=False).post(
        "/goals",
        json={"goal": "Pick a database", "workflow_mode": "debate", "debate_rounds": 3},
        headers={"X-API-Key": _KEY},
    )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["goal_id"] == "deb-1" and body["mode"] == "debate"
    provider.complete.assert_not_awaited()
    svc._check_budget_preflight.assert_awaited_once()
    kwargs = svc.submit_goal.await_args.kwargs
    assert kwargs["workflow_mode"] == "debate"
    ctx = kwargs["execution_context"]
    assert ctx["debate_rounds"] == 3
    assert not {"debate_consensus", "debate_error"} & set(ctx)


class _Queue:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []

    def enqueue_goal(self, **kwargs: Any) -> str:
        self.enqueued.append(kwargs)
        return "task"


async def test_debate_goal_compiles_the_in_graph_debate_node() -> None:
    queue = _Queue()
    svc = GoalService(task_queue=queue)
    result = await svc.submit_goal(
        goal="Pick a database", priority="normal", dry_run=False, tenant_ctx=_CTX,
        workflow_mode="debate", execution_context={"debate_rounds": 3},
    )
    ctx = svc._goals[result["goal_id"]].execution_context
    # The worker compiles its graph from this snapshot: the debate node is on.
    assert ctx["agent_pattern_flags"]["enable_debate"] is True
    assert queue.enqueued[0]["workflow_mode"] == "debate"
    # The requested round count reaches the graph context (API and worker paths).
    assert "debate_rounds" in GRAPH_CONTEXT_KEYS


async def test_debate_node_runs_once_with_requested_rounds_and_feeds_the_planner() -> None:
    from app.agent.debate import DebateResult
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.providers.fake import FakeProvider

    p = FakeProvider()
    graph = AgentGraph(planner=p, executor=p, verifier=p, enable_debate=True)
    agent_state = AgentState(goal="Pick a database", tenant_ctx=_CTX)
    agent_state.context["debate_rounds"] = 3
    seen: list[int] = []

    async def _run(self: Any, **kwargs: Any) -> DebateResult:
        seen.append(self._rounds)
        return DebateResult(
            winning_proposal="Use Postgres", winning_agent="agent_1",
            all_proposals=[], consensus_level=1.0, rounds=self._rounds,
        )

    state = {"goal": agent_state.goal, "tenant_ctx": _CTX, "iteration": 0,
             "agent_state": agent_state}
    with patch("app.agent.debate.DebateOrchestrator.run", new=_run):
        await graph._node_debate(state)
        await graph._node_debate(state)  # a replan loop must not debate again
    assert seen == [3]
    assert agent_state.context["debate_result"] == "Use Postgres"
    assert any("Use Postgres" in part for part in graph._pattern_result_parts(agent_state))


async def test_debate_node_failure_records_only_the_exception_class() -> None:
    from app.agent.graph import AgentGraph
    from app.agent.state import AgentState
    from app.providers.fake import FakeProvider

    p = FakeProvider()
    graph = AgentGraph(planner=p, executor=p, verifier=p, enable_debate=True)
    graph._emit = AsyncMock()  # type: ignore[method-assign]
    agent_state = AgentState(goal="Pick a database", tenant_ctx=_CTX)
    boom = AsyncMock(side_effect=RuntimeError("upstream said sk_live_SECRET1234567890abcd is bad"))
    with patch("app.agent.debate.DebateOrchestrator.run", new=boom):
        await graph._node_debate(
            {"goal": agent_state.goal, "tenant_ctx": _CTX, "iteration": 0,
             "agent_state": agent_state}
        )
    assert agent_state.context["debate_error"] == "RuntimeError"
    assert "SECRET1234567890abcd" not in str(agent_state.context)
