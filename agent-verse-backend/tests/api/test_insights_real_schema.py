"""Insights endpoints must query the real schema, under RLS, and never invent numbers.

Regression for the audit finding: ``/insights/estimate`` and
``/insights/agent-health`` read ``goals.cost_usd`` / ``goals.duration_s`` /
``goals.embedding`` (none exist) and ``evaluations.score_*`` (never existed),
swallowed the resulting error and returned made-up defaults (success probability
0.82, every health axis 0.7). ``/insights/benchmarks`` ran its cross-tenant
aggregate on a tenant session with no RLS context, so under FORCE RLS it always
saw zero rows and reported ``insufficient_data``.
"""

from __future__ import annotations

import inspect
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import TextClause

import app.api.insights as insights
from app.api.insights import router as insights_router
from app.db.models import Base
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_TID = "0123456789abcdef0123456789abcdef"
_CTX = TenantContext(tenant_id=_TID, plan=PlanTier.PROFESSIONAL, api_key_id="rs-key")
_HEADERS = {"X-API-Key": "rs-key"}


# ── Schema guard ─────────────────────────────────────────────────────────────


def _all_statements() -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=30)
    return {
        "estimate": insights.build_estimate_stmt(_TID, "deploy the app", None),
        "estimate_agent": insights.build_estimate_stmt(_TID, "deploy the app", "a1"),
        "agent_goals": insights.build_agent_goal_summary_stmt(_TID, "a1"),
        "agent_evals": insights.build_agent_eval_summary_stmt(_TID, "a1"),
        "agent_tools": insights.build_agent_tool_count_stmt(_TID, "a1"),
        "benchmarks": insights.build_benchmark_stmt(since),
        "query": insights.build_goal_query_stmt(
            _TID, since=since, status_filter="failed", cost_min=1.0, limit=5
        ),
        "query_nocost": insights.build_goal_query_stmt(
            _TID, since=since, status_filter=None, cost_min=None, limit=5
        ),
    }


@pytest.mark.parametrize("name", sorted(_all_statements()))
def test_every_referenced_column_exists_in_orm_metadata(name: str) -> None:
    stmt = _all_statements()[name]
    seen = 0
    for el in visitors.iterate(stmt):
        assert not isinstance(el, TextClause), f"{name}: raw SQL text escapes the schema check"
        if isinstance(el, Column) and isinstance(el.table, Table):
            table = Base.metadata.tables.get(el.table.name)
            assert table is not None, f"{name}: unknown table {el.table.name}"
            assert el.name in table.c, f"{name}: {el.table.name}.{el.name} does not exist"
            seen += 1
    assert seen > 0
    # And it compiles for Postgres.
    str(stmt.compile(dialect=postgresql.dialect()))


def test_module_issues_no_raw_sql_text() -> None:
    src = inspect.getsource(insights)
    assert not re.search(r"\btext\(|import text\b", src), "use Core statements over ORM tables"
    for col in ("cost_usd, duration_s", "embedding", "score_task_completion", "last_tool_used"):
        assert col not in src


# ── Fake DB plumbing ─────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def one(self) -> dict[str, Any]:
        return self._rows[0]


class _Session:
    """Routes a Core select to canned rows by one of its column labels."""

    def __init__(self, routes: dict[str, list[dict[str, Any]]], fail: bool = False) -> None:
        self.routes = routes
        self.fail = fail
        self.sql: list[str] = []
        self.params: list[Any] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    @asynccontextmanager
    async def _begin(self) -> Any:
        yield self

    def begin(self) -> Any:
        return self._begin()

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        q = str(stmt)
        self.sql.append(q)
        self.params.append(params)
        if "set_config" in q or "row_security" in q:
            return _Result([])
        if self.fail:
            raise RuntimeError("connection refused")
        labels = set(getattr(stmt, "selected_columns", {}).keys())
        for marker, rows in self.routes.items():
            if marker in labels:
                return _Result(rows)
        raise AssertionError(f"unrouted statement: {labels}")


def _client(
    session: _Session | None = None, *, system: _Session | None = None, tenant_db: bool = True
) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == "rs-key" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(insights_router)
    if session is not None and tenant_db:
        app.state.db_session_factory = lambda: session
    if system is not None:
        app.state.system_db_session_factory = lambda: system
    app.state.goal_service = MagicMock(_db=None)
    return TestClient(app)


def _rls_set_for_tenant(session: _Session) -> bool:
    return any(
        "set_config" in q and isinstance(p, dict) and p.get("tid") == _TID
        for q, p in zip(session.sql, session.params, strict=True)
    )


# ── /insights/estimate ───────────────────────────────────────────────────────


def test_estimate_without_db_is_501_not_defaults() -> None:
    resp = _client().post("/insights/estimate", json={"goal": "deploy it"}, headers=_HEADERS)
    assert resp.status_code == 501


def test_estimate_db_error_is_503() -> None:
    s = _Session({}, fail=True)
    resp = _client(s).post("/insights/estimate", json={"goal": "deploy it"}, headers=_HEADERS)
    assert resp.status_code == 503


def test_estimate_no_history_returns_honest_nulls() -> None:
    s = _Session({"similarity": []})
    resp = _client(s).post("/insights/estimate", json={"goal": "deploy it"}, headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success_probability"] is None
    assert data["estimated_cost_usd"] is None
    assert data["estimated_duration_s"] is None
    assert data["estimated_iterations"] is None
    assert data["similar_goals_count"] == 0
    assert data["confidence"] == "none"
    assert _rls_set_for_tenant(s)


def test_estimate_computes_from_real_rows() -> None:
    rows = [
        {"status": "complete", "iterations": 2, "duration_s": 30.0, "cost_usd": 0.02,
         "similarity": 0.9},
        {"status": "complete", "iterations": 4, "duration_s": 90.0, "cost_usd": 0.06,
         "similarity": 0.8},
        {"status": "failed", "iterations": 6, "duration_s": None, "cost_usd": None,
         "similarity": 0.5},
    ]
    s = _Session({"similarity": rows})
    data = (
        _client(s)
        .post("/insights/estimate", json={"goal": "deploy it", "agent_id": "a1"},
              headers=_HEADERS)
        .json()
    )
    assert data["similar_goals_count"] == 3
    assert data["success_probability"] == round(2 / 3, 3)
    assert data["estimated_cost_usd"] == {"min": 0.02, "mean": 0.04, "max": 0.06}
    assert data["estimated_duration_s"] == {"min": 30, "mean": 60, "max": 90}
    assert data["estimated_iterations"] == {"min": 2, "mean": 4, "max": 6}
    assert data["confidence"] == "medium"
    assert data["based_on"] == "similar_tenant_goals"


# ── /insights/agent-health ───────────────────────────────────────────────────


def test_agent_health_without_db_is_501() -> None:
    assert _client().get("/insights/agent-health/a1", headers=_HEADERS).status_code == 501


def test_agent_health_db_error_is_503() -> None:
    s = _Session({}, fail=True)
    assert _client(s).get("/insights/agent-health/a1", headers=_HEADERS).status_code == 503


def test_agent_health_without_runs_is_all_null() -> None:
    s = _Session(
        {
            "finished": [{"total": 0, "completed": 0, "finished": 0, "avg_duration_s": None,
                          "avg_cost_usd": None}],
            "avg_accuracy": [{"evaluated": 0, "avg_accuracy": None, "avg_coherence": None}],
            "unique_tools": [{"unique_tools": 0}],
        }
    )
    data = _client(s).get("/insights/agent-health/a1", headers=_HEADERS).json()
    assert data["sample_size"] == 0
    assert set(data["health"]) == {
        "speed", "accuracy", "cost_efficiency", "tool_coverage", "success_rate", "coherence"
    }
    assert all(v is None for v in data["health"].values())


def test_agent_health_computed_from_real_rows() -> None:
    s = _Session(
        {
            "finished": [{"total": 10, "completed": 6, "finished": 8, "avg_duration_s": 60.0,
                          "avg_cost_usd": 0.05}],
            "avg_accuracy": [{"evaluated": 4, "avg_accuracy": 0.9, "avg_coherence": 0.6}],
            "unique_tools": [{"unique_tools": 5}],
        }
    )
    data = _client(s).get("/insights/agent-health/a1", headers=_HEADERS).json()
    h = data["health"]
    assert data["sample_size"] == 10
    assert data["eval_sample_size"] == 4
    assert h["success_rate"] == 0.75
    assert h["speed"] == 0.5
    assert h["cost_efficiency"] == 0.5
    assert h["accuracy"] == 0.9
    assert h["coherence"] == 0.6
    assert h["tool_coverage"] == 0.5
    assert _rls_set_for_tenant(s)


def test_agent_health_missing_evals_and_tools_are_null_not_defaults() -> None:
    s = _Session(
        {
            "finished": [{"total": 3, "completed": 3, "finished": 3, "avg_duration_s": None,
                          "avg_cost_usd": None}],
            "avg_accuracy": [{"evaluated": 0, "avg_accuracy": None, "avg_coherence": None}],
            "unique_tools": [{"unique_tools": 0}],
        }
    )
    h = _client(s).get("/insights/agent-health/a1", headers=_HEADERS).json()["health"]
    assert h["success_rate"] == 1.0
    assert h["speed"] is None
    assert h["cost_efficiency"] is None
    assert h["accuracy"] is None
    assert h["coherence"] is None
    assert h["tool_coverage"] is None


# ── /insights/benchmarks ─────────────────────────────────────────────────────


def _bench_row(total: int, n_tenants: int) -> dict[str, Any]:
    return {
        "total": total, "n_tenants": n_tenants, "avg_success_rate": 0.8, "avg_cost_usd": 0.04,
        "avg_duration_s": 30.0, "avg_iterations": 3.0,
        "p25_sr": 0.5, "p50_sr": 0.7, "p75_sr": 0.9, "p90_sr": 0.95,
        "p25_cost": 0.01, "p50_cost": 0.03, "p75_cost": 0.05, "p90_cost": 0.09,
    }


def test_benchmarks_run_on_the_system_session_with_rls_bypass() -> None:
    tenant = _Session({})
    system = _Session({"n_tenants": [_bench_row(50, 6)]})
    data = _client(tenant, system=system).get("/insights/benchmarks", headers=_HEADERS).json()
    assert data["data_source"] == "live_platform_data"
    assert data["platform_avg_success_rate"] == 0.8
    assert data["percentile_bands"]["p90"] == {"success_rate": 0.95, "cost_usd": 0.09}
    assert any("row_security = off" in q for q in system.sql)
    assert tenant.sql == []


def test_benchmarks_too_few_tenants_is_insufficient_data() -> None:
    system = _Session({"n_tenants": [_bench_row(50, 2)]})
    data = _client(system=system).get("/insights/benchmarks", headers=_HEADERS).json()
    assert data["data_source"] == "insufficient_data"
    assert data["platform_avg_success_rate"] is None


def test_benchmarks_db_error_is_503() -> None:
    system = _Session({}, fail=True)
    resp = _client(system=system).get("/insights/benchmarks", headers=_HEADERS)
    assert resp.status_code == 503


def test_benchmarks_without_db_is_501() -> None:
    assert _client().get("/insights/benchmarks", headers=_HEADERS).status_code == 501


# ── /insights/query ──────────────────────────────────────────────────────────


def test_query_db_error_is_503_not_in_memory_fallback() -> None:
    s = _Session({}, fail=True)
    resp = _client(s).post("/insights/query", json={"query": "failed goals"}, headers=_HEADERS)
    assert resp.status_code == 503


def test_query_reads_cost_from_the_breakdown_ledger() -> None:
    created = datetime.now(UTC)
    rows = [{"id": "g1", "status": "failed", "goal_text": "x", "priority": "normal",
             "dry_run": False, "agent_id": None, "workflow_mode": "single_agent",
             "created_at": created, "cost_usd": 1.5}]
    s = _Session({"cost_usd": rows})
    data = (
        _client(s)
        .post("/insights/query", json={"query": "failed goals cost more than $1"},
              headers=_HEADERS)
        .json()
    )
    assert data["total"] == 1
    assert data["results"][0]["cost_usd"] == 1.5
    assert any("goal_cost_breakdowns" in q for q in s.sql)
    assert _rls_set_for_tenant(s)
