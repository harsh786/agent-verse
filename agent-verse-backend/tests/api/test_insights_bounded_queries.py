"""a10-F233-02 / a10-F233-03: ``/insights`` statements are index-served and bounded.

* The estimator filtered on ``similarity(goal_text, :goal) >= 0.2`` — no index
  serves a function call, so every estimate scored the tenant's whole window.
  It now uses the pg_trgm ``%`` operator (``ix_goals_goal_text_trgm``) with the
  threshold set for the transaction.
* Per-goal cost was a ``GROUP BY goal_id`` over the tenant's (benchmarks: every
  tenant's) entire ``goal_cost_breakdowns`` ledger, joined back. It is now a
  correlated lookup of the goals the query keeps.
* The cross-tenant benchmark aggregate ran on every request; it is now served
  from a per-app TTL cache (errors are not cached).

Real-Postgres coverage (plan uses the index, figures unchanged):
``tests/integration/test_insights_postgres.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api import insights

_TID = "6f1c2f9e-7c1a-4f0e-9a43-1d2f3e4a5b6c"


def _sql(stmt: Any) -> str:
    return " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())


def _no_ledger_group_by(sql: str) -> None:
    assert "GROUP BY goal_cost_breakdowns.goal_id" not in sql
    assert "goal_cost_breakdowns.goal_id = goals.id" in sql


def test_estimate_uses_the_indexable_trgm_operator() -> None:
    sql = _sql(insights.build_estimate_stmt(_TID, "deploy the app", None))
    assert "goals.goal_text %% " in sql  # pyformat-escaped ``%`` operator
    _no_ledger_group_by(sql)


def test_estimate_setup_sets_the_threshold_for_the_transaction() -> None:
    sql = str(
        insights.estimate_setup_stmt().compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "set_config('pg_trgm.similarity_threshold', '0.2', true)" in sql


@pytest.mark.parametrize(
    "stmt",
    [
        insights.build_agent_goal_summary_stmt(_TID, "a1"),
        insights.build_goal_query_stmt(
            _TID, since=datetime.now(UTC), status_filter=None, cost_min=0.5, limit=10
        ),
        insights.build_benchmark_stmt(datetime.now(UTC)),
    ],
    ids=["agent_summary", "query", "benchmarks"],
)
def test_cost_is_a_correlated_lookup_not_a_ledger_aggregate(stmt: Any) -> None:
    _no_ledger_group_by(_sql(stmt))


def test_tenant_cost_lookup_is_tenant_scoped() -> None:
    sql = _sql(insights.build_agent_goal_summary_stmt(_TID, "a1"))
    assert "goal_cost_breakdowns.tenant_id = " in sql


async def test_benchmark_cache_serves_repeats_and_never_caches_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    outcome: dict[str, Any] = {"fail": True}

    async def _fake(system_db: Any, *, cache: Any = None) -> dict[str, Any]:
        if cache is not None:
            return await cache.get_or_compute("platform", lambda: _fake(system_db))
        calls.append(1)
        if outcome["fail"]:
            raise HTTPException(503, "db down")
        return {"data_source": "live_platform_data", "n": len(calls)}

    monkeypatch.setattr(insights, "compute_platform_benchmarks", _fake)
    cache = insights.BenchmarkCache(ttl_s=60)
    with pytest.raises(HTTPException):
        await insights.compute_platform_benchmarks(object(), cache=cache)
    outcome["fail"] = False
    first = await insights.compute_platform_benchmarks(object(), cache=cache)
    second = await insights.compute_platform_benchmarks(object(), cache=cache)
    assert first == second == {"data_source": "live_platform_data", "n": 2}
    assert len(calls) == 2  # the failure, then ONE successful query


async def test_benchmark_cache_expires() -> None:
    n = {"v": 0}

    async def _compute() -> dict[str, Any]:
        n["v"] += 1
        return {"v": n["v"]}

    cache = insights.BenchmarkCache(ttl_s=0.0)
    assert (await cache.get_or_compute("k", _compute))["v"] == 1
    assert (await cache.get_or_compute("k", _compute))["v"] == 2


def test_benchmark_cache_is_per_app_state() -> None:
    class _State:
        pass

    a, b = _State(), _State()
    assert insights.benchmark_cache_for(a) is insights.benchmark_cache_for(a)
    assert insights.benchmark_cache_for(a) is not insights.benchmark_cache_for(b)
