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

from app.agent.state import GoalStatus
from app.core.errors import NotFoundError, ServiceUnavailableError
from app.intelligence.eval import EvalScorecard
from app.services.goal_service import GoalRecord, GoalService
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


# ── MEM-19: eval reads work on a replica that does not hold the goal ─────────


def _db_record(goal_id: str) -> GoalRecord:
    return GoalRecord(
        goal_id=goal_id,
        goal_text="Summarise the churn cohort",
        status=GoalStatus.COMPLETE,
        tenant_id=_CTX.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="2026-09-01T00:00:00+00:00",
    )


def _fresh_replica(db: _DB, *, known: set[str]) -> GoalService:
    """A GoalService with no in-memory goals whose Postgres knows *known*."""
    svc = GoalService()
    svc._db = db

    async def _load(goal_id: str, tenant_ctx: TenantContext) -> GoalRecord | None:
        if goal_id in known and tenant_ctx.tenant_id == _CTX.tenant_id:
            return _db_record(goal_id)
        return None

    svc._db_get_goal_record = _load  # type: ignore[method-assign]
    return svc


async def test_fresh_replica_serves_the_persisted_scorecard() -> None:
    db = _DB(evaluations_row=(json.dumps({"accuracy": 0.9}), 0.9, True))
    svc = _fresh_replica(db, known={"g-remote"})
    out = await svc.get_eval("g-remote", _CTX)
    assert out["status"] == "evaluated" and out["average_score"] == pytest.approx(0.9)


async def test_fresh_replica_serves_suggestions_from_the_persisted_scorecard() -> None:
    db = _DB(evaluations_row=(json.dumps({"accuracy": 0.1}), 0.1, False))
    svc = _fresh_replica(db, known={"g-remote"})
    out = await svc.get_eval_suggestions("g-remote", _CTX)
    assert out["status"] == "evaluated" and out["count"] == 1


async def test_unknown_goal_is_still_not_found() -> None:
    svc = _fresh_replica(_DB(), known=set())
    with pytest.raises(NotFoundError):
        await svc.get_eval("g-missing", _CTX)
    with pytest.raises(NotFoundError):
        await svc.get_eval_suggestions("g-missing", _CTX)
    with pytest.raises(NotFoundError):
        await svc.run_eval("g-missing", _CTX)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        self.ttl[key] = ex

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0


async def test_pending_scoring_is_visible_to_every_replica() -> None:
    redis = _FakeRedis()
    scoring = GoalService()
    scoring._redis = redis
    await scoring._mark_eval_pending("g-remote", _CTX.tenant_id)
    assert all(redis.ttl.values()), "the pending marker must expire on its own"

    other = _fresh_replica(_DB(), known={"g-remote"})
    other._redis = redis
    assert (await other.get_eval("g-remote", _CTX))["status"] == "pending"

    await scoring._clear_eval_pending("g-remote", _CTX.tenant_id)
    assert (await other.get_eval("g-remote", _CTX))["status"] == "not_evaluated"
