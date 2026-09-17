"""Unit coverage for app/org/analytics.OrgAnalyticsService.

The pure helper functions (compute_cost_efficiency, etc.) and OrgHealthScore
are already covered by test_analytics_defake.py and test_health_score.py.
This module exercises OrgAnalyticsService's async DB-query methods with a
mocked AsyncSession, following the ``tests/org/`` convention established in
test_service_comprehensive.py:

    session = AsyncMock(); session.execute = AsyncMock(side_effect=[...])
    svc = OrgAnalyticsService(session=session, tenant_id="t1")
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from app.org.analytics import OrgAnalyticsService, OrgHealthScore

# asyncio_mode = "auto" (pyproject.toml) autodetects async test functions.


class _Result:
    """Minimal stand-in for a SQLAlchemy ``Result``."""

    def __init__(
        self,
        scalar: Any = None,
        scalars_all: list[Any] | None = None,
        all_rows: list[Any] | None = None,
        one: tuple[Any, ...] | None = None,
    ) -> None:
        self._scalar = scalar
        self._scalars_all = list(scalars_all or [])
        self._all_rows = list(all_rows if all_rows is not None else [])
        self._one = one

    def scalar(self) -> Any:
        return self._scalar

    def scalars(self) -> Any:
        ns = SimpleNamespace(all=lambda: list(self._scalars_all))
        return ns

    def all(self) -> list[Any]:
        return self._all_rows

    def one(self) -> tuple[Any, ...]:
        return self._one if self._one is not None else (0.0, 0.0)


def _session(execute_side_effect: list[Any]) -> Any:
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    return session


# ── get_org_health_score ───────────────────────────────────────────────────────


async def test_get_org_health_score_computes_all_factors_from_real_data() -> None:
    results = [
        _Result(scalar=10),  # total_missions
        _Result(scalar=8),  # completed_missions
        _Result(scalar=2),  # blocked_tasks
        _Result(scalar=20),  # total_tasks_count
        _Result(one=(100.0, 50.0)),  # cost (est, act)
        _Result(scalar=15),  # completed_tasks
        _Result(scalar=5),  # failed_tasks
        _Result(scalar=2),  # escalations
        _Result(scalar=1),  # violations
        _Result(scalar=5),  # running_tasks
        _Result(scalar=10),  # active_pool
    ]
    session = _session(results)
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    hs = await svc.get_org_health_score("org-1")

    assert isinstance(hs, OrgHealthScore)
    assert hs.mission_completion_rate == 0.8
    assert hs.blocked_ratio == 0.1
    assert hs.cost_efficiency == 1.0  # spent under estimate, capped at 1.0
    assert hs.quality_avg == 0.75
    assert hs.escalation_rate == 0.1
    assert hs.security_compliance == 0.95  # 1 violation / 20 total_tasks_count
    assert hs.agent_utilization == 0.5
    # knowledge_freshness has no org-owned source — always indeterminate.
    assert hs.indeterminate_factors == {"knowledge_freshness"}


async def test_get_org_health_score_indeterminate_when_no_data() -> None:
    results = [
        _Result(scalar=0),  # total_missions
        _Result(scalar=0),  # completed_missions
        _Result(scalar=0),  # blocked_tasks
        _Result(scalar=0),  # total_tasks_count
        _Result(one=(0.0, 0.0)),  # cost (no spend recorded)
        _Result(scalar=0),  # completed_tasks
        _Result(scalar=0),  # failed_tasks
        _Result(scalar=0),  # escalations
        _Result(scalar=0),  # violations
        _Result(scalar=0),  # running_tasks
        _Result(scalar=0),  # active_pool
    ]
    session = _session(results)
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    hs = await svc.get_org_health_score("org-empty")

    assert hs.mission_completion_rate == 0.0
    assert hs.blocked_ratio == 0.0
    assert hs.cost_efficiency == 1.0  # neutral default, disclosed
    assert hs.quality_avg == 0.0
    assert hs.security_compliance == 1.0  # no activity -> fully compliant
    assert hs.agent_utilization == 0.0
    assert hs.indeterminate_factors == {
        "cost_efficiency",
        "quality_avg",
        "agent_utilization",
        "knowledge_freshness",
    }


async def test_get_org_health_score_db_error_returns_default_score() -> None:
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("db exploded"))
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    hs = await svc.get_org_health_score("org-1")

    assert hs == OrgHealthScore()


# ── get_overview ───────────────────────────────────────────────────────────────


async def test_get_overview_computes_completion_rate() -> None:
    results = [
        _Result(scalar=10),  # total
        _Result(scalar=3),  # active
        _Result(scalar=7),  # completed
    ]
    session = _session(results)
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    fake_health = OrgHealthScore(mission_completion_rate=0.7)
    with patch.object(svc, "get_org_health_score", AsyncMock(return_value=fake_health)):
        overview = await svc.get_overview("org-1")

    assert overview["period_days"] == 30
    assert overview["total_missions"] == 10
    assert overview["active_missions"] == 3
    assert overview["completed_missions"] == 7
    assert overview["completion_rate"] == 0.7
    assert overview["health_score"]["score"] == fake_health.score
    assert "generated_at" in overview


async def test_get_overview_zero_total_missions_yields_zero_completion_rate() -> None:
    results = [_Result(scalar=0), _Result(scalar=0), _Result(scalar=0)]
    session = _session(results)
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    with patch.object(svc, "get_org_health_score", AsyncMock(return_value=OrgHealthScore())):
        overview = await svc.get_overview("org-1")

    assert overview["completion_rate"] == 0.0


# ── get_department_analytics ───────────────────────────────────────────────────


async def test_get_department_analytics_empty_org_returns_empty_list() -> None:
    session = _session([_Result(scalars_all=[])])
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    depts = await svc.get_department_analytics("org-1")

    assert depts == []


async def test_get_department_analytics_returns_per_dept_metrics() -> None:
    dept_a = SimpleNamespace(id="d1", name="Engineering")
    dept_b = SimpleNamespace(id="d2", name="Sales")
    results = [
        _Result(scalars_all=[dept_a, dept_b]),  # dept_q
        _Result(scalar=5),  # dept_a mission total
        _Result(scalar=3),  # dept_a completed
        _Result(scalar=2),  # dept_b mission total
        _Result(scalar=0),  # dept_b completed
    ]
    session = _session(results)
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    depts = await svc.get_department_analytics("org-1")

    assert len(depts) == 2
    assert depts[0].department_id == "d1"
    assert depts[0].department_name == "Engineering"
    assert depts[0].total_missions == 5
    assert depts[0].completed_missions == 3
    assert depts[0].completion_rate == 3 / 5
    assert depts[1].total_missions == 2
    assert depts[1].completion_rate == 0.0  # no missions completed


# ── get_cost_breakdown ─────────────────────────────────────────────────────────


async def test_get_cost_breakdown_aggregates_by_department() -> None:
    results = [
        _Result(scalar=1234.5678),  # total spend
        _Result(all_rows=[("d1", "Engineering", 900.123), ("d2", "Sales", 334.4448)]),
    ]
    session = _session(results)
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    breakdown = await svc.get_cost_breakdown("org-1")

    assert breakdown["total_usd_30d"] == 1234.5678
    assert breakdown["by_department"] == [
        {"department_id": "d1", "department_name": "Engineering", "actual_cost_usd": 900.123},
        {"department_id": "d2", "department_name": "Sales", "actual_cost_usd": 334.4448},
    ]
    assert breakdown["by_mission_type"] == []
    assert breakdown["indeterminate"] == ["by_mission_type"]
    assert "generated_at" in breakdown


async def test_get_cost_breakdown_no_spend_returns_zero() -> None:
    results = [_Result(scalar=0.0), _Result(all_rows=[])]
    session = _session(results)
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    breakdown = await svc.get_cost_breakdown("org-1")

    assert breakdown["total_usd_30d"] == 0.0
    assert breakdown["by_department"] == []


# ── get_bottlenecks ────────────────────────────────────────────────────────────


class _TaskWithTitle:
    def __init__(self, id_: str, title: str, updated_at: datetime | None) -> None:
        self.id = id_
        self.title = title
        self.updated_at = updated_at


class _TaskWithoutTitle:
    """Deliberately has no ``title`` attribute (hasattr(task, 'title') is False)."""

    def __init__(self, id_: str, updated_at: datetime | None) -> None:
        self.id = id_
        self.updated_at = updated_at


async def test_get_bottlenecks_severity_high_when_blocked_over_a_day() -> None:
    old = datetime.now(UTC) - timedelta(days=2)
    task = _TaskWithTitle("t1", "Fix the thing", old)
    session = _session([_Result(scalars_all=[task])])
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    bottlenecks = await svc.get_bottlenecks("org-1")

    assert len(bottlenecks) == 1
    assert bottlenecks[0]["type"] == "blocked_task"
    assert bottlenecks[0]["entity_id"] == "t1"
    assert bottlenecks[0]["title"] == "Fix the thing"
    assert bottlenecks[0]["severity"] == "high"
    assert bottlenecks[0]["blocked_hours"] > 24


async def test_get_bottlenecks_severity_medium_when_recently_blocked() -> None:
    recent = datetime.now(UTC) - timedelta(hours=2)
    task = _TaskWithTitle("t2", "Small thing", recent)
    session = _session([_Result(scalars_all=[task])])
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    bottlenecks = await svc.get_bottlenecks("org-1")

    assert bottlenecks[0]["severity"] == "medium"


async def test_get_bottlenecks_missing_title_falls_back_to_unknown() -> None:
    task = _TaskWithoutTitle("t3", datetime.now(UTC))
    session = _session([_Result(scalars_all=[task])])
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    bottlenecks = await svc.get_bottlenecks("org-1")

    assert bottlenecks[0]["title"] == "Unknown task"


async def test_get_bottlenecks_no_updated_at_is_treated_as_just_blocked() -> None:
    task = _TaskWithTitle("t4", "No timestamp", None)
    session = _session([_Result(scalars_all=[task])])
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    bottlenecks = await svc.get_bottlenecks("org-1")

    assert bottlenecks[0]["blocked_hours"] == 0.0
    assert bottlenecks[0]["severity"] == "medium"


async def test_get_bottlenecks_empty_when_nothing_blocked() -> None:
    session = _session([_Result(scalars_all=[])])
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    bottlenecks = await svc.get_bottlenecks("org-1")

    assert bottlenecks == []


# ── get_model_performance ──────────────────────────────────────────────────────


async def test_get_model_performance_reports_cost_and_flags_indeterminate() -> None:
    session = _session([_Result(scalar=42.987654)])
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    perf = await svc.get_model_performance("org-1")

    assert perf["models"] == []
    assert perf["total_tokens_24h"] is None
    assert perf["total_cost_usd_24h"] == 42.9877
    assert perf["indeterminate"] == ["models", "total_tokens_24h"]
    assert "generated_at" in perf


async def test_get_model_performance_zero_cost_when_no_spend() -> None:
    session = _session([_Result(scalar=None)])
    svc = OrgAnalyticsService(session=session, tenant_id="t1")

    perf = await svc.get_model_performance("org-1")

    assert perf["total_cost_usd_24h"] == 0.0
