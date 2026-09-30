"""ENT-01 / ENT-02: analytics must never leak another tenant's goals.

The aggregator fell back to GoalService's in-memory goals — across ALL
tenants — whenever the tenant's DB result was empty or the query failed, so a
tenant with no goals (or a DB blip) saw every other tenant's metrics on
/analytics/goals|tools|costs|agents. An empty DB result now means "no data for
this tenant", a DB error raises (the API answers 503), and the in-memory path
(used only when there is no DB at all) is filtered by tenant.

ENT-02: the goal DB path ignored ``agent_id`` and reported zero duration/cost.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.analytics.aggregator import AnalyticsUnavailableError, GoalAnalyticsAggregator

TENANT = "tenant-mine"
OTHER = "tenant-other"


class _Goal:
    def __init__(
        self,
        tenant_id: str,
        status: str = "complete",
        agent_id: str = "agent-1",
        cost: float = 1.5,
        tool: str = "jira:search",
    ) -> None:
        self.tenant_id = tenant_id
        self.status = status
        self.agent_id = agent_id
        self.cost_usd = cost
        self.created_at = datetime.now(UTC).isoformat()
        self.completed_at = None
        self.eval_score = None
        self.events = [{"type": "tool_call_complete", "tool": tool}]


class _GoalService:
    def __init__(self, goals: list[_Goal]) -> None:
        self._goals = {str(i): g for i, g in enumerate(goals)}


def _svc() -> _GoalService:
    return _GoalService(
        [
            _Goal(OTHER, agent_id="their-agent", tool="their:secret_tool"),
            _Goal(OTHER, status="failed", agent_id="their-agent", tool="their:secret_tool"),
            _Goal(TENANT, agent_id="my-agent", cost=0.25, tool="my:tool"),
        ]
    )


class _Session:
    def __init__(self, db: _Db) -> None:
        self._db = db

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(stmt)
        if "set_config" in sql:
            return _Rows([])
        self._db.statements.append((sql, dict(params or {})))
        if self._db.error is not None:
            raise self._db.error
        return _Rows(self._db.rows)

    async def commit(self) -> None:
        return None

    def begin(self) -> Any:
        return self


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _Db:
    def __init__(self, rows: list[Any] | None = None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def __call__(self) -> _Session:
        return _Session(self)


# ── empty DB result = no data for this tenant (never another tenant's) ────────


async def test_goal_metrics_empty_db_result_is_zero_not_other_tenants() -> None:
    agg = GoalAnalyticsAggregator(goal_service=_svc(), db=_Db(rows=[]))
    m = await agg.goal_metrics(tenant_id=TENANT, days=30)
    assert (m.total, m.completed, m.failed) == (0, 0, 0)


async def test_tool_metrics_empty_db_result_is_empty() -> None:
    agg = GoalAnalyticsAggregator(goal_service=_svc(), db=_Db(rows=[]))
    assert await agg.tool_metrics_db(tenant_id=TENANT, days=30) == []


async def test_cost_trends_empty_db_result_is_empty() -> None:
    agg = GoalAnalyticsAggregator(goal_service=_svc(), db=_Db(rows=[]))
    assert await agg.cost_trends_db(tenant_id=TENANT, days=30) == []


async def test_agent_metrics_empty_db_result_is_empty() -> None:
    agg = GoalAnalyticsAggregator(goal_service=_svc(), db=_Db(rows=[]))
    assert await agg.agent_metrics_db(tenant_id=TENANT, days=30) == []


# ── DB error = unavailable (the API maps it to 503), never a fallback ─────────


@pytest.mark.parametrize(
    "call",
    [
        lambda a: a.goal_metrics(tenant_id=TENANT, days=30),
        lambda a: a.tool_metrics_db(tenant_id=TENANT, days=30),
        lambda a: a.cost_trends_db(tenant_id=TENANT, days=30),
        lambda a: a.cost_by_model_db(tenant_id=TENANT, days=30),
        lambda a: a.agent_metrics_db(tenant_id=TENANT, days=30),
    ],
)
async def test_db_error_raises_instead_of_falling_back(call: Any) -> None:
    agg = GoalAnalyticsAggregator(goal_service=_svc(), db=_Db(error=RuntimeError("db down")))
    with pytest.raises(AnalyticsUnavailableError):
        await call(agg)


# ── in-memory path (no DB at all) is tenant-filtered ──────────────────────────


async def test_in_memory_goal_metrics_only_count_the_tenants_goals() -> None:
    agg = GoalAnalyticsAggregator(goal_service=_svc(), db=None)
    m = await agg.goal_metrics(tenant_id=TENANT, days=30)
    assert (m.total, m.completed, m.failed) == (1, 1, 0)
    assert m.total_cost_usd == pytest.approx(0.25)


async def test_in_memory_tool_cost_agent_metrics_are_tenant_filtered() -> None:
    agg = GoalAnalyticsAggregator(goal_service=_svc(), db=None)
    tools = await agg.tool_metrics_db(tenant_id=TENANT, days=30)
    assert [t.tool_name for t in tools] == ["my:tool"]
    trends = await agg.cost_trends_db(tenant_id=TENANT, days=30)
    assert sum(t["cost_usd"] for t in trends) == pytest.approx(0.25)
    agents = await agg.agent_metrics_db(tenant_id=TENANT, days=30)
    assert [a.agent_id for a in agents] == ["my-agent"]


async def test_no_tenant_id_yields_no_data() -> None:
    agg = GoalAnalyticsAggregator(goal_service=_svc(), db=None)
    assert (await agg.goal_metrics(tenant_id="", days=30)).total == 0
    assert await agg.tool_metrics_db(tenant_id="", days=30) == []
    assert await agg.cost_trends_db(tenant_id="", days=30) == []
    assert await agg.agent_metrics_db(tenant_id="", days=30) == []


# ── ENT-02: goal DB path honours agent_id and reports duration/cost ──────────


async def test_goal_db_path_filters_by_agent_and_reports_duration_and_cost() -> None:
    now = datetime.now(UTC)
    rows = [
        # id, status, priority, agent_id, created_at, dry_run, completed_at, cost_usd
        ("g1", "complete", "normal", "a1", now - timedelta(seconds=30), False, now, 0.5),
        ("g2", "failed", "normal", "a1", now - timedelta(seconds=10), False, now, 1.5),
        ("g3", "executing", "normal", "a1", now, False, None, 0.0),
    ]
    db = _Db(rows=rows)
    agg = GoalAnalyticsAggregator(db=db)
    m = await agg.goal_metrics(tenant_id=TENANT, days=30, agent_id="a1")

    (sql, params), = [s for s in db.statements if "FROM goals" in s[0]]
    assert params.get("agent_id") == "a1"
    assert "agent_id = :agent_id" in sql
    assert params["tid"] == TENANT
    assert m.total == 3
    assert m.avg_duration_s == pytest.approx(20.0)
    assert m.total_cost_usd == pytest.approx(2.0)
    assert m.avg_cost_usd == pytest.approx(2.0 / 3, rel=1e-4)


async def test_goal_db_path_without_agent_filter_does_not_bind_one() -> None:
    db = _Db(rows=[])
    agg = GoalAnalyticsAggregator(db=db)
    await agg.goal_metrics(tenant_id=TENANT, days=30)
    (sql, params), = [s for s in db.statements if "FROM goals" in s[0]]
    assert "agent_id" not in params
