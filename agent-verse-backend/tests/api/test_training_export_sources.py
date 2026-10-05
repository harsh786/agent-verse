"""Regression: the training-data export was always empty.

* DB path: it looked for ``goal_service._db_session_factory``; GoalService stores
  its factory as ``_db``, so the DB query never ran.
* In-memory path: it filtered on ``GoalRecord.eval_score``, which does not exist
  (scores live in ``GoalService._eval_scores``), so nothing ever qualified.
"""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.state import GoalStatus
from app.api.training_export import router
from app.intelligence.eval import EvalScorecard
from app.services.goal_service import GoalRecord
from app.tenancy.context import PlanTier, TenantContext
from tests._rls_recorder import RlsRecordingDb

TENANT = "t-train"


def _client(goal_service: Any, *, db_state: Any = None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.goal_service = goal_service
    if db_state is not None:
        app.state.db_session_factory = db_state

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id=TENANT, plan=PlanTier.ENTERPRISE, api_key_id="k"
        )
        return await call_next(request)

    return TestClient(app)


def _rows(sql: str, _p: dict[str, Any]) -> list[Any]:
    if sql.startswith("SELECT COUNT(*)"):
        # count, avg, min, max, then the four histogram buckets
        return [(1, 0.93, 0.93, 0.93, 0, 0, 1, 0)]
    if "FROM goals g" in sql:
        return [("g1", "Summarise the Q3 report", _dt.datetime(2026, 1, 1, tzinfo=_dt.UTC), 0.93)]
    if "FROM goal_steps" in sql:
        return [("g1", "report summary", [{"tool_name": "docs.read"}])]
    return []


def test_db_path_used_via_goal_service_db_attribute() -> None:
    db = RlsRecordingDb(rows_for=_rows)
    goal_service = SimpleNamespace(_db=db, _goals={}, _eval_scores={})
    resp = _client(goal_service).get("/intelligence/export-training-data/preview?min_score=0.8")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1 and body["max_score_found"] == 0.93
    assert body["samples"][0]["goal"] == "Summarise the Q3 report"
    queries = db.touching("FROM goals g")
    assert queries and all(q.tenant_guc == TENANT for q in queries)  # under tenant RLS
    assert all("evaluations" in q.sql and "eval_scorecards" in q.sql for q in queries)
    # OPS-37: per-goal latest-score lookups, never DISTINCT ON over the ledger.
    assert all("DISTINCT ON" not in q.sql for q in queries)


def test_db_failure_is_503_not_an_empty_export() -> None:
    class _Broken:
        def __call__(self) -> Any:
            raise RuntimeError("db down")

    goal_service = SimpleNamespace(_goals={}, _eval_scores={})
    resp = _client(goal_service, db_state=_Broken()).post("/intelligence/export-training-data")
    assert resp.status_code == 503


def test_in_memory_path_uses_eval_scores() -> None:
    record = GoalRecord(
        goal_id="g-mem",
        goal_text="Draft release notes",
        status=GoalStatus.COMPLETE,
        tenant_id=TENANT,
        priority="normal",
        dry_run=False,
        created_at="2026-01-01T00:00:00+00:00",
        events=[{"type": "step_complete", "tool_name": "git.log", "output": "notes"}],
    )
    goal_service = SimpleNamespace(
        _goals={"g-mem": record},
        _eval_scores={"g-mem": EvalScorecard(goal_id="g-mem", scores={"a": 0.9, "b": 1.0})},
    )
    resp = _client(goal_service).post("/intelligence/export-training-data?min_score=0.8")
    assert resp.status_code == 200
    assert resp.headers["X-Training-Examples"] == "1"
    assert "Draft release notes" in resp.text
