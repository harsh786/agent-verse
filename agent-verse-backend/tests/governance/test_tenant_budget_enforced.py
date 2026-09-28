"""Configured tenant budgets (budget_configs) are enforced, and PUT is admin-only.

PUT /costs/budgets had no role and no scope, returned success without persisting
when the tracker had no DB, and nothing read budget_configs during execution —
every tenant ran at the hard-coded BudgetConfig() defaults.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from httpx import ASGITransport, AsyncClient

from app.governance.cost import (
    BudgetConfig,
    CostController,
    RedisCostController,
    TenantBudgetSource,
)
from tests.governance._router_app import make_app, tenant


class _Res:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _BudgetDB:
    def __init__(self, row: Any = None, fail: bool = False) -> None:
        self.row = row
        self.fail = fail
        self.stmts: list[tuple[str, Any]] = []

    def __call__(self) -> Any:
        s = MagicMock()
        s.__aenter__ = AsyncMock(return_value=s)
        s.__aexit__ = AsyncMock(return_value=False)
        b = MagicMock()
        b.__aenter__ = AsyncMock(return_value=s)
        b.__aexit__ = AsyncMock(return_value=False)
        s.begin = MagicMock(return_value=b)
        s.execute = AsyncMock(side_effect=self._execute)
        return s

    async def _execute(self, q: Any, params: Any = None) -> _Res:
        sql = str(q)
        self.stmts.append((sql, params))
        if self.fail and "budget_configs" in sql:
            raise RuntimeError("db down")
        if "FROM budget_configs" in sql:
            return _Res(self.row)
        return _Res()


class _FakeRedis:
    def __init__(self) -> None:
        self.d: dict[str, float] = {}

    async def get(self, k: str) -> Any:
        return self.d.get(k)

    async def exists(self, k: str) -> int:
        return 0

    async def incrbyfloat(self, k: str, v: float) -> float:
        self.d[k] = self.d.get(k, 0.0) + v
        return self.d[k]

    async def expire(self, *a: Any) -> None:
        return None

    async def expireat(self, *a: Any) -> None:
        return None

    async def set(self, *a: Any, **k: Any) -> None:
        return None


async def test_redis_controller_enforces_configured_daily_budget() -> None:
    ctx = tenant("t-b")
    cc = RedisCostController(_FakeRedis(), budget_db=_BudgetDB(row=(100.0, 1.0)))
    assert await cc.check_and_record(tenant_ctx=ctx, goal_id="g", cost_usd=0.6)
    # Default daily budget is $500; the configured one is $1 — must deny.
    assert not await cc.check_and_record(tenant_ctx=ctx, goal_id="g", cost_usd=0.6)
    status = await cc.get_budget_status(tenant_ctx=ctx)
    assert status["daily_limit"] == 1.0


async def test_in_memory_controller_enforces_configured_per_goal_budget() -> None:
    ctx = tenant("t-b")
    cc = CostController()
    cc.set_budget_db(_BudgetDB(row=(0.5, 500.0)))
    assert await cc.check_and_record(goal_id="g", cost_usd=0.4, tenant_ctx=ctx)
    assert not await cc.check_and_record(goal_id="g", cost_usd=0.4, tenant_ctx=ctx)


async def test_budget_load_failure_without_cache_fails_closed() -> None:
    ctx = tenant("t-b")
    cc = RedisCostController(_FakeRedis(), budget_db=_BudgetDB(fail=True))
    assert not await cc.check_and_record(tenant_ctx=ctx, goal_id="g", cost_usd=0.01)


async def test_budget_source_reads_under_tenant_rls() -> None:
    db = _BudgetDB(row=(2.0, 3.0))
    cfg = await TenantBudgetSource(db).get("t-rls")
    assert cfg == BudgetConfig(per_goal_usd=2.0, per_tenant_daily_usd=3.0)
    assert "set_config('app.tenant_id'" in db.stmts[0][0]
    assert db.stmts[0][1] == {"tid": "t-rls"}


def _costs_app(db: Any, roles: tuple[str, ...] = ("admin",)) -> Any:
    from app.api.costs import router

    app = make_app(router, ctx=tenant("t-b", roles=roles))
    tracker = MagicMock()
    tracker._db = db
    app.state.cost_tracker = tracker
    app.state.redis_cost_controller = RedisCostController(_FakeRedis(), budget_db=db)
    return app


_BODY = {"per_goal_usd": 1.0, "per_tenant_daily_usd": 2.0}


async def test_put_budgets_requires_admin() -> None:
    db = _BudgetDB()
    app = _costs_app(db, roles=("viewer",))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.put("/costs/budgets", json=_BODY)
    assert r.status_code == 403
    assert not any("INSERT" in q for q, _ in db.stmts)


def test_put_costs_requires_costs_admin_scope() -> None:
    from app.auth.scope_enforcement import ScopeEnforcementMiddleware

    assert ScopeEnforcementMiddleware._required_scope("PUT", "/costs/budgets") == "costs:admin"
    assert ScopeEnforcementMiddleware._required_scope("PUT", "/governance/budget") == (
        "governance:write"
    )


async def test_put_budgets_without_db_is_503_not_fake_success() -> None:
    app = _costs_app(None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.put("/costs/budgets", json=_BODY)
    assert r.status_code == 503


async def test_put_budgets_persists_under_rls_and_takes_effect_immediately() -> None:
    db = _BudgetDB(row=(10.0, 500.0))
    app = _costs_app(db)
    cc = app.state.redis_cost_controller
    ctx = tenant("t-b")
    assert (await cc.resolve_config("t-b")).per_tenant_daily_usd == 500.0  # cached
    db.row = (1.0, 2.0)  # what the upsert stores
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.put("/costs/budgets", json=_BODY)
    assert r.status_code == 200, r.text
    ins = [(q, p) for q, p in db.stmts if "INSERT INTO budget_configs" in q]
    assert ins and ins[0][1]["tid"] == "t-b"
    # This replica's cache was invalidated — the new $2 daily limit binds now.
    assert await cc.check_and_record(tenant_ctx=ctx, goal_id="g", cost_usd=0.9)
    assert not await cc.check_and_record(tenant_ctx=ctx, goal_id="g2", cost_usd=1.5)


def test_budget_exhausted_latch_hard_fails_goal_with_budget_reason() -> None:
    from app.agent.nodes.routing_mixin import RoutingMixin
    from app.agent.state import AgentState, GoalStatus

    st = AgentState(goal_id="g", goal="x", tenant_ctx=tenant("t-b"))
    st.context["_budget_exhausted"] = True
    router = RoutingMixin.__new__(RoutingMixin)
    assert router._route_after_execute({"agent_state": st}) == "failed"  # type: ignore[arg-type,typeddict-item]
    assert st.status is GoalStatus.FAILED
    assert st.context["terminal_reason"] == "budget_exceeded"
    assert "budget_exceeded" in (st.error_message or "")
