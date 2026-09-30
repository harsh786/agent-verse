"""MEM-31: /intelligence/benchmarks shares /insights/benchmarks' platform figures.

The legacy endpoint ran its cross-tenant aggregate on the app session with no
tenant GUC (under FORCE RLS: always insufficient_data) and swallowed the
tenant-metrics DB errors into nulls, so SelfImprovementPage and AgentRadarPage
disagreed and a DB outage looked like "no data".
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.enterprise import intelligence_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="t-bench", plan=PlanTier.ENTERPRISE, api_key_id="k")
_H = {"X-API-Key": "k-bench"}


class _Rows:
    def __init__(self, row: Any = None, mapping: dict[str, Any] | None = None) -> None:
        self._row, self._mapping = row, mapping

    def fetchone(self) -> Any:
        return self._row

    def mappings(self) -> _Rows:
        return self

    def one(self) -> dict[str, Any]:
        assert self._mapping is not None
        return self._mapping


class _Session:
    def __init__(self, handler: Any) -> None:
        self._handler = handler

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        sql = str(stmt)
        if "set_config" in sql:
            return _Rows()
        return self._handler(sql)


def _factory(handler: Any) -> Any:
    return lambda: _Session(handler)


_PLATFORM = {
    "total": 400, "n_tenants": 12, "avg_success_rate": 0.8123, "avg_cost_usd": 0.04321,
    "avg_duration_s": 30.0, "avg_iterations": 3.0,
    **{f"p{p}_sr": 0.5 for p in (25, 50, 75, 90)},
    **{f"p{p}_cost": 0.02 for p in (10, 25, 50, 75, 90)},
}


def _tenant_ok(sql: str) -> _Rows:
    if "FROM goals" in sql:
        return _Rows((10, 9, 1))
    if "cost_ledger" in sql:
        return _Rows((0.05, 10))
    return _Rows((0, None))


def _system_ok(sql: str) -> _Rows:
    if "evaluations" in sql:
        return _Rows((50, 8, 0.7, 0.9, 0.8, 0.7, 0.6, 0.5))
    return _Rows(mapping=_PLATFORM)


def _client(*, db: Any, system_db: Any) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == "k-bench" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(intelligence_router)
    app.state.db_session_factory = db
    app.state.system_db_session_factory = system_db
    return TestClient(app, raise_server_exceptions=False)


def test_platform_figures_match_insights_benchmarks() -> None:
    from app.api.insights import router as insights_router

    client = _client(db=_factory(_tenant_ok), system_db=_factory(_system_ok))
    client.app.include_router(insights_router)  # type: ignore[attr-defined]
    legacy = client.get("/intelligence/benchmarks", headers=_H)
    shared = client.get("/insights/benchmarks", headers=_H)
    assert legacy.status_code == 200 and shared.status_code == 200, legacy.text
    body, ins = legacy.json(), shared.json()
    assert body["data_source"] == "live_platform_data"
    assert body["platform_avg_success_rate"] == ins["platform_avg_success_rate"]
    assert body["platform_avg_cost_usd"] == ins["platform_avg_cost_usd"]
    assert body["platform_avg_eval_score"] == 0.7
    assert body["dimensions"]["platform"]["task_completion"] == 0.9
    assert body["your_success_rate"] == 0.9


def test_tenant_metrics_db_error_is_503_not_nulls() -> None:
    def _boom(sql: str) -> _Rows:
        raise RuntimeError("connection refused")

    client = _client(db=_factory(_boom), system_db=_factory(_system_ok))
    resp = client.get("/intelligence/benchmarks", headers=_H)
    assert resp.status_code == 503


def test_platform_db_error_is_503() -> None:
    def _boom(sql: str) -> _Rows:
        raise RuntimeError("connection refused")

    client = _client(db=_factory(_tenant_ok), system_db=_factory(_boom))
    assert client.get("/intelligence/benchmarks", headers=_H).status_code == 503


def test_small_platform_stays_insufficient() -> None:
    small = {**_PLATFORM, "n_tenants": 2}

    def _system(sql: str) -> _Rows:
        if "evaluations" in sql:
            return _Rows((50, 2, 0.7, 0.9, 0.8, 0.7, 0.6, 0.5))
        return _Rows(mapping=small)

    client = _client(db=_factory(_tenant_ok), system_db=_factory(_system))
    body = client.get("/intelligence/benchmarks", headers=_H).json()
    assert body["data_source"] == "insufficient_data"
    assert body["platform_avg_success_rate"] is None
    assert body["platform_avg_eval_score"] is None
