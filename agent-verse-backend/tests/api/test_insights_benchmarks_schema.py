"""/insights/benchmarks must query the real schema and never invent numbers.

The SQL read ``goals.cost_usd`` / ``goals.duration_s`` (neither exists), so the
query always raised and the endpoint could never report real data.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.insights import router as insights_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="ins-t1", plan=PlanTier.PROFESSIONAL, api_key_id="ins-key")
_HEADERS = {"X-API-Key": "ins-key"}


class _Session:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.row = row
        self.sql: list[str] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        q = str(stmt)
        self.sql.append(q)
        # Model the real schema: goals has no cost_usd / duration_s.
        if "FROM goals" in q and ("goals.cost_usd" in q or "duration_s" in q):
            raise RuntimeError('column "cost_usd" does not exist')
        res = MagicMock()
        res.fetchone.return_value = self.row
        return res


def _client(session: _Session) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == "ins-key" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(insights_router)
    svc = MagicMock()
    svc._db = lambda: session
    app.state.goal_service = svc
    return TestClient(app)


def _row(total: int, n_tenants: int) -> tuple[Any, ...]:
    return (total, 0.8, 0.04, 30.0, 3.0, 0.01, 0.03, 0.05, 0.09, 0.0, 1.0, 1.0, 1.0, n_tenants)


def test_real_data_reported_from_real_schema() -> None:
    session = _Session(_row(50, 6))
    data = _client(session).get("/insights/benchmarks", headers=_HEADERS).json()
    assert data["data_source"] == "live_platform_data"
    assert data["platform_avg_success_rate"] == 0.8
    assert data["platform_avg_cost_usd"] == 0.04
    assert any("FROM cost_ledger" in q for q in session.sql)


def test_too_few_tenants_is_insufficient_data() -> None:
    data = _client(_Session(_row(50, 2))).get("/insights/benchmarks", headers=_HEADERS).json()
    assert data["data_source"] == "insufficient_data"
    assert data["platform_avg_success_rate"] is None
