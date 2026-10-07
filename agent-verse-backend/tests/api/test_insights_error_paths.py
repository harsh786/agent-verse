"""a10-F233-05 / a10-F233-06: ``/insights`` error paths are honest.

* ``GET /insights/graph/{id}`` swallowed every ``get_events`` error into a 200
  start-only graph — an unknown goal and a store outage looked like an empty
  run. ``/insights/analysis/{id}`` turned a store outage into a 404.
* ``POST /insights/query`` fed the LLM-parsed ``days`` straight into
  ``timedelta`` (a huge value -> OverflowError -> 500; a negative one put the
  cutoff in the future) and ``float()`` of a malformed regex cost raised.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.insights import router as insights_router
from app.core.errors import NotFoundError, ServiceUnavailableError
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-ins-err", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _client(goal_service: Any, provider: Any = None) -> TestClient:
    app = FastAPI()
    app.include_router(insights_router)
    app.state.goal_service = goal_service
    if provider is not None:
        app.state._app_provider = provider

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = _CTX
        return await call_next(request)

    return TestClient(app)


def _svc(**methods: Any) -> MagicMock:
    svc = MagicMock()
    svc._db = None
    for name, value in methods.items():
        setattr(svc, name, value)
    return svc


def test_graph_of_unknown_goal_is_404() -> None:
    svc = _svc(get_events=AsyncMock(side_effect=NotFoundError("Goal not found: g1")))
    resp = _client(svc).get("/insights/graph/g1")
    assert resp.status_code == 404, resp.text


@pytest.mark.parametrize("exc", [ServiceUnavailableError("db down"), RuntimeError("boom")])
def test_graph_store_failure_is_503_not_an_empty_graph(exc: Exception) -> None:
    svc = _svc(get_events=AsyncMock(side_effect=exc))
    resp = _client(svc).get("/insights/graph/g1")
    assert resp.status_code == 503, resp.text


def test_graph_of_known_goal_still_builds() -> None:
    svc = _svc(get_events=AsyncMock(return_value=[{"type": "goal_complete"}]))
    resp = _client(svc).get("/insights/graph/g1")
    assert resp.status_code == 200
    assert {n["id"] for n in resp.json()["nodes"]} == {"start", "end"}


def test_analysis_store_failure_is_503_and_unknown_goal_404() -> None:
    down = _svc(get_goal=AsyncMock(side_effect=RuntimeError("connection refused")))
    assert _client(down).get("/insights/analysis/g1").status_code == 503
    missing = _svc(get_goal=AsyncMock(side_effect=NotFoundError("Goal not found: g1")))
    assert _client(missing).get("/insights/analysis/g1").status_code == 404


@pytest.mark.parametrize(
    ("llm_days", "expected"),
    [(10**12, 3650), (-5, 1), (0, 1), ("lots", 30), (7, 7)],
)
def test_query_bounds_llm_parsed_days(llm_days: Any, expected: int) -> None:
    svc = _svc(list_goals=AsyncMock(return_value={"goals": []}))
    provider = FakeProvider(responses=[json.dumps({"days": llm_days})])
    resp = _client(svc, provider).post("/insights/query", json={"query": "goals lately"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["query_parsed"]["days"] == expected


def test_query_ignores_non_finite_llm_cost() -> None:
    svc = _svc(list_goals=AsyncMock(return_value={"goals": []}))
    provider = FakeProvider(responses=['{"days": 3, "cost_min": "Infinity"}'])
    resp = _client(svc, provider).post("/insights/query", json={"query": "expensive goals"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["query_parsed"]["cost_min"] is None


def test_query_malformed_regex_cost_is_not_a_500() -> None:
    svc = _svc(list_goals=AsyncMock(return_value={"goals": []}))
    resp = _client(svc).post("/insights/query", json={"query": "goals cost more than 1.2.3"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["query_parsed"]["cost_min"] is None
