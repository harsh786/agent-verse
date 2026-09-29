"""GET /goals/{id}/eval reads persisted scorecards; the per-replica cache is bounded.

``get_eval`` only consulted ``self._eval_scores`` — a per-process dict that grew
by one scorecard per scored goal, forever. A goal scored on another replica (or
before a restart) reported ``not_evaluated`` although its scorecard was in
Postgres. It now reads ``evaluations`` / ``eval_scorecards`` under the tenant's
RLS context on a cache miss, and the cache is an LRU with a fixed capacity.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.errors import ServiceUnavailableError
from app.intelligence.eval import EvalScorecard
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-eval-read", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _Session:
    def __init__(self, db: _DB) -> None:
        self._db = db

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        if "set_config" in sql:
            self._db.guc = (params or {}).get("tid")
            return _Result(None)
        if self._db.fail:
            raise RuntimeError("connection refused")
        self._db.queries.append((sql, dict(params or {}), self._db.guc))
        if "FROM evaluations" in sql:
            return _Result(self._db.evaluations_row)
        if "FROM eval_scorecards" in sql:
            return _Result(self._db.scorecards_row)
        return _Result(None)


class _DB:
    def __init__(
        self, *, evaluations_row: Any = None, scorecards_row: Any = None, fail: bool = False
    ) -> None:
        self.evaluations_row = evaluations_row
        self.scorecards_row = scorecards_row
        self.fail = fail
        self.guc: str | None = None
        self.queries: list[tuple[str, dict[str, Any], str | None]] = []

    def __call__(self) -> _Session:
        return _Session(self)


async def _goal(svc: GoalService) -> str:
    created = await svc.submit_goal(
        goal="Summarise the churn cohort", tenant_ctx=_CTX, priority="normal", dry_run=True
    )
    return str(created["goal_id"] if isinstance(created, dict) else created.goal_id)


async def test_get_eval_reads_the_persisted_evaluation_under_rls() -> None:
    svc = GoalService()
    goal_id = await _goal(svc)
    db = _DB(evaluations_row=(json.dumps({"accuracy": 0.9, "safety": 0.7}), 0.8, True))
    svc._db = db
    out = await svc.get_eval(goal_id, _CTX)
    assert out["status"] == "evaluated"
    assert out["scores"] == {"accuracy": 0.9, "safety": 0.7}
    assert out["average_score"] == pytest.approx(0.8) and out["passed"] is True
    sql, params, guc = db.queries[0]
    assert "tenant_id = :tid" in sql and params["tid"] == _CTX.tenant_id
    assert guc == _CTX.tenant_id


async def test_get_eval_falls_back_to_eval_scorecards() -> None:
    svc = GoalService()
    goal_id = await _goal(svc)
    db = _DB(scorecards_row=({"task_completion": 0.2}, 0.2))
    svc._db = db
    out = await svc.get_eval(goal_id, _CTX)
    assert out["status"] == "evaluated"
    assert out["scores"] == {"task_completion": 0.2}
    assert out["average_score"] == pytest.approx(0.2) and out["passed"] is False


async def test_get_eval_with_nothing_persisted_is_not_evaluated() -> None:
    svc = GoalService()
    goal_id = await _goal(svc)
    svc._db = _DB()
    assert (await svc.get_eval(goal_id, _CTX))["status"] == "not_evaluated"


async def test_get_eval_db_failure_is_unavailable_not_a_false_not_evaluated() -> None:
    svc = GoalService()
    goal_id = await _goal(svc)
    svc._db = _DB(fail=True)
    with pytest.raises(ServiceUnavailableError):
        await svc.get_eval(goal_id, _CTX)


async def test_cached_scorecard_is_served_without_a_db_read() -> None:
    svc = GoalService()
    goal_id = await _goal(svc)
    db = _DB()
    svc._db = db
    svc._eval_scores[goal_id] = EvalScorecard(goal_id=goal_id, scores={"accuracy": 1.0})
    assert (await svc.get_eval(goal_id, _CTX))["status"] == "evaluated"
    assert db.queries == []


def test_eval_score_cache_is_bounded() -> None:
    svc = GoalService()
    cap = svc._eval_scores.maxsize
    for i in range(cap + 50):
        svc._eval_scores[f"g{i}"] = EvalScorecard(goal_id=f"g{i}", scores={})
    assert len(svc._eval_scores) == cap
    assert "g0" not in svc._eval_scores and f"g{cap + 49}" in svc._eval_scores
