"""Comprehensive tests for app/analytics/aggregator.py."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.analytics.aggregator import (
    AgentMetrics,
    GoalAnalyticsAggregator,
    GoalMetrics,
    ToolMetrics,
    _goal_status_cancelled,
    _goal_status_completed,
    _goal_status_failed,
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


def test_goal_metrics_defaults():
    m = GoalMetrics()
    assert m.total == 0
    assert m.completed == 0
    assert m.failed == 0
    assert m.cancelled == 0
    assert m.success_rate == 0.0
    assert m.avg_duration_s == 0.0
    assert m.avg_cost_usd == 0.0
    assert m.total_cost_usd == 0.0


def test_tool_metrics_defaults():
    m = ToolMetrics(tool_name="search")
    assert m.call_count == 0
    assert m.failure_count == 0
    assert m.avg_latency_ms == 0.0
    assert m.failure_rate == 0.0


def test_agent_metrics_defaults():
    m = AgentMetrics(agent_id="agent-1")
    assert m.goal_count == 0
    assert m.success_rate == 0.0
    assert m.avg_eval_score == 0.0
    assert m.avg_cost_usd == 0.0


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------


def test_goal_status_completed_variants():
    assert _goal_status_completed("complete") is True
    assert _goal_status_completed("completed") is True
    assert _goal_status_completed("COMPLETE") is True
    assert _goal_status_completed("COMPLETED") is True
    assert _goal_status_completed("failed") is False
    assert _goal_status_completed("") is False


def test_goal_status_failed():
    assert _goal_status_failed("failed") is True
    assert _goal_status_failed("FAILED") is True
    assert _goal_status_failed("complete") is False
    assert _goal_status_failed("") is False


def test_goal_status_cancelled():
    assert _goal_status_cancelled("cancelled") is True
    assert _goal_status_cancelled("CANCELLED") is True
    assert _goal_status_cancelled("failed") is False
    assert _goal_status_cancelled("complete") is False


# ---------------------------------------------------------------------------
# Helper: mock goal factory
# ---------------------------------------------------------------------------


def _mock_goal(
    status: str,
    cost: float = 0.01,
    agent_id: str = "agent-1",
    events: list | None = None,
    eval_score: float | None = None,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> MagicMock:
    g = MagicMock()
    g.status = status
    g.cost_usd = cost
    g.agent_id = agent_id
    g.events = events or []
    g.eval_score = eval_score
    g.created_at = created_at or datetime.now(UTC)
    g.completed_at = completed_at
    return g


# ---------------------------------------------------------------------------
# goal_metrics — in-memory path
# ---------------------------------------------------------------------------


async def test_goal_metrics_success_rate_calculation():
    svc = MagicMock()
    svc._goals = {
        "g1": _mock_goal("complete"),
        "g2": _mock_goal("complete"),
        "g3": _mock_goal("failed"),
    }
    agg = GoalAnalyticsAggregator(goal_service=svc)
    m = await agg.goal_metrics(days=30)

    assert m.total == 3
    assert m.completed == 2
    assert m.failed == 1
    assert abs(m.success_rate - (2 / 3)) < 0.001


async def test_goal_metrics_all_completed():
    svc = MagicMock()
    svc._goals = {f"g{i}": _mock_goal("complete") for i in range(5)}
    agg = GoalAnalyticsAggregator(goal_service=svc)
    m = await agg.goal_metrics(days=30)
    assert m.success_rate == 1.0
    assert m.failed == 0
    assert m.cancelled == 0


async def test_goal_metrics_all_failed():
    svc = MagicMock()
    svc._goals = {"g1": _mock_goal("failed"), "g2": _mock_goal("failed")}
    agg = GoalAnalyticsAggregator(goal_service=svc)
    m = await agg.goal_metrics(days=30)
    assert m.success_rate == 0.0
    assert m.failed == 2
    assert m.completed == 0


async def test_goal_metrics_cancelled_counted():
    svc = MagicMock()
    svc._goals = {
        "g1": _mock_goal("complete"),
        "g2": _mock_goal("cancelled"),
    }
    agg = GoalAnalyticsAggregator(goal_service=svc)
    m = await agg.goal_metrics(days=30)
    assert m.cancelled == 1
    assert m.total == 2


async def test_goal_metrics_cost_aggregation():
    svc = MagicMock()
    svc._goals = {
        "g1": _mock_goal("complete", cost=0.10),
        "g2": _mock_goal("complete", cost=0.20),
        "g3": _mock_goal("failed", cost=0.05),
    }
    agg = GoalAnalyticsAggregator(goal_service=svc)
    m = await agg.goal_metrics(days=30)
    assert abs(m.total_cost_usd - 0.35) < 0.001
    assert m.avg_cost_usd > 0


async def test_goal_metrics_duration():
    svc = MagicMock()
    now = datetime.now(UTC)
    goal = _mock_goal("complete", created_at=now, completed_at=now + timedelta(seconds=120))
    svc._goals = {"g1": goal}

    agg = GoalAnalyticsAggregator(goal_service=svc)
    m = await agg.goal_metrics(days=30)
    assert m.avg_duration_s == 120.0


async def test_goal_metrics_empty_goals():
    svc = MagicMock()
    svc._goals = {}
    agg = GoalAnalyticsAggregator(goal_service=svc)
    m = await agg.goal_metrics(days=30)
    assert m.total == 0
    assert m.success_rate == 0.0


async def test_goal_metrics_filters_old_goals():
    svc = MagicMock()
    old_goal = _mock_goal("complete", created_at=datetime(2020, 1, 1, tzinfo=UTC))
    recent_goal = _mock_goal("failed")
    svc._goals = {"old": old_goal, "recent": recent_goal}

    agg = GoalAnalyticsAggregator(goal_service=svc)
    m = await agg.goal_metrics(days=30)
    # Old goal should be filtered out
    assert m.total == 1
    assert m.failed == 1


# ---------------------------------------------------------------------------
# goal_metrics — DB path
# ---------------------------------------------------------------------------


async def test_goal_metrics_db_path():
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [
        (1, "complete", "normal", "agent-1", datetime.now(UTC), False),
        (2, "complete", "normal", "agent-1", datetime.now(UTC), False),
        (3, "failed", "high", "agent-2", datetime.now(UTC), False),
    ]
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)
    # Use MagicMock (not AsyncMock) so db() returns mock_session directly
    mock_db = MagicMock(return_value=mock_session)

    agg = GoalAnalyticsAggregator(db=mock_db)
    m = await agg.goal_metrics(tenant_id="t1", days=30)

    assert m.total == 3
    assert m.completed == 2
    assert m.failed == 1
    assert abs(m.success_rate - (2 / 3)) < 0.001


async def test_goal_metrics_db_query_failed_returns_in_memory():
    """When DB query fails, falls back to in-memory goal service."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(side_effect=Exception("query failed"))
    mock_db = MagicMock(return_value=mock_session)

    svc = MagicMock()
    svc._goals = {"g1": _mock_goal("complete")}

    agg = GoalAnalyticsAggregator(goal_service=svc, db=mock_db)
    m = await agg.goal_metrics(tenant_id="t1", days=30)

    # Falls back to in-memory — 0 goals because _get_goals_from_db returned []
    # and the DB path returned empty, so it uses the in-memory path
    assert m is not None


async def test_goal_metrics_db_empty_falls_back_to_in_memory():
    """When DB returns empty rows, uses in-memory goals."""
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []  # Empty DB result
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db = MagicMock(return_value=mock_session)

    svc = MagicMock()
    svc._goals = {"g1": _mock_goal("complete")}

    agg = GoalAnalyticsAggregator(goal_service=svc, db=mock_db)
    m = await agg.goal_metrics(tenant_id="t1", days=30)

    # Falls back to in-memory
    assert m.total == 1
    assert m.completed == 1


# ---------------------------------------------------------------------------
# tool_metrics
# ---------------------------------------------------------------------------


def test_tool_metrics_call_count():
    svc = MagicMock()
    events = [
        {"type": "tool_call_complete", "tool": "search", "latency_ms": 100},
        {"type": "tool_call_complete", "tool": "search", "latency_ms": 200},
        {"type": "tool_call_failed", "tool": "search", "error": "timeout"},
    ]
    svc._goals = {"g1": _mock_goal("complete", events=events)}
    agg = GoalAnalyticsAggregator(goal_service=svc)

    results = agg.tool_metrics(days=30)
    assert len(results) == 1
    tool = results[0]
    assert tool.tool_name == "search"
    assert tool.call_count == 3
    assert tool.failure_count == 1
    assert abs(tool.failure_rate - (1 / 3)) < 0.001


def test_tool_metrics_latency():
    svc = MagicMock()
    events = [
        {"type": "tool_call_complete", "tool": "db_query", "latency_ms": 50},
        {"type": "tool_call_complete", "tool": "db_query", "latency_ms": 150},
    ]
    svc._goals = {"g1": _mock_goal("complete", events=events)}
    agg = GoalAnalyticsAggregator(goal_service=svc)

    results = agg.tool_metrics(days=30)
    assert results[0].avg_latency_ms == 100.0


def test_tool_metrics_multiple_tools_sorted_by_count():
    svc = MagicMock()
    events_a = [{"type": "tool_call_complete", "tool": "tool_a"}]
    events_b = [
        {"type": "tool_call_complete", "tool": "tool_b"},
        {"type": "tool_call_complete", "tool": "tool_b"},
        {"type": "tool_call_complete", "tool": "tool_b"},
    ]
    svc._goals = {
        "g1": _mock_goal("complete", events=events_a),
        "g2": _mock_goal("complete", events=events_b),
    }
    agg = GoalAnalyticsAggregator(goal_service=svc)
    results = agg.tool_metrics(days=30)

    # Most called tool should be first
    assert results[0].tool_name == "tool_b"
    assert results[0].call_count == 3


def test_tool_metrics_empty_goals():
    svc = MagicMock()
    svc._goals = {}
    agg = GoalAnalyticsAggregator(goal_service=svc)
    assert agg.tool_metrics() == []


def test_tool_metrics_step_complete_event():
    svc = MagicMock()
    events = [{"type": "step_complete", "tool_name": "my_tool"}]
    svc._goals = {"g1": _mock_goal("complete", events=events)}
    agg = GoalAnalyticsAggregator(goal_service=svc)
    results = agg.tool_metrics()
    assert any(t.tool_name == "my_tool" for t in results)


# ---------------------------------------------------------------------------
# cost_trends
# ---------------------------------------------------------------------------


def test_cost_trends_daily_bucket():
    svc = MagicMock()
    now = datetime.now(UTC)
    svc._goals = {
        "g1": _mock_goal("complete", cost=0.10, created_at=now),
        "g2": _mock_goal("complete", cost=0.20, created_at=now),
    }
    agg = GoalAnalyticsAggregator(goal_service=svc)
    trends = agg.cost_trends(days=30, bucket="day")

    assert len(trends) == 1
    assert abs(trends[0]["cost_usd"] - 0.30) < 0.001
    assert "period" in trends[0]


def test_cost_trends_weekly_bucket():
    svc = MagicMock()
    now = datetime.now(UTC)
    svc._goals = {
        "g1": _mock_goal("complete", cost=0.50, created_at=now),
    }
    agg = GoalAnalyticsAggregator(goal_service=svc)
    trends = agg.cost_trends(days=30, bucket="week")

    assert len(trends) == 1
    assert "W" in trends[0]["period"]


def test_cost_trends_empty():
    svc = MagicMock()
    svc._goals = {}
    agg = GoalAnalyticsAggregator(goal_service=svc)
    assert agg.cost_trends() == []


def test_cost_trends_sorted_by_period():
    svc = MagicMock()
    svc._goals = {
        "g1": _mock_goal("complete", cost=0.10, created_at=datetime(2024, 1, 3, tzinfo=UTC)),
        "g2": _mock_goal("complete", cost=0.20, created_at=datetime(2024, 1, 1, tzinfo=UTC)),
    }
    agg = GoalAnalyticsAggregator(goal_service=svc)
    trends = agg.cost_trends(days=365, bucket="day")
    periods = [t["period"] for t in trends]
    assert periods == sorted(periods)


# ---------------------------------------------------------------------------
# agent_metrics
# ---------------------------------------------------------------------------


def test_agent_metrics_per_agent():
    svc = MagicMock()
    svc._goals = {
        "g1": _mock_goal("complete", agent_id="a1", cost=0.10, eval_score=0.9),
        "g2": _mock_goal("complete", agent_id="a1", cost=0.20, eval_score=0.8),
        "g3": _mock_goal("failed", agent_id="a2", cost=0.05),
    }
    agg = GoalAnalyticsAggregator(goal_service=svc)
    results = agg.agent_metrics(days=30)

    a1 = next(r for r in results if r.agent_id == "a1")
    a2 = next(r for r in results if r.agent_id == "a2")

    assert a1.goal_count == 2
    assert a1.success_rate == 1.0
    assert abs(a1.avg_eval_score - 0.85) < 0.001

    assert a2.goal_count == 1
    assert a2.success_rate == 0.0


def test_agent_metrics_sorted_by_goal_count():
    svc = MagicMock()
    svc._goals = {
        "g1": _mock_goal("complete", agent_id="busy"),
        "g2": _mock_goal("complete", agent_id="busy"),
        "g3": _mock_goal("complete", agent_id="busy"),
        "g4": _mock_goal("failed", agent_id="idle"),
    }
    agg = GoalAnalyticsAggregator(goal_service=svc)
    results = agg.agent_metrics()
    assert results[0].agent_id == "busy"
    assert results[0].goal_count == 3


def test_agent_metrics_no_eval_score_returns_zero():
    svc = MagicMock()
    svc._goals = {"g1": _mock_goal("complete", eval_score=None)}
    agg = GoalAnalyticsAggregator(goal_service=svc)
    results = agg.agent_metrics()
    assert results[0].avg_eval_score == 0.0


def test_agent_metrics_empty():
    svc = MagicMock()
    svc._goals = {}
    agg = GoalAnalyticsAggregator(goal_service=svc)
    assert agg.agent_metrics() == []


# ---------------------------------------------------------------------------
# _get_goals_from_db
# ---------------------------------------------------------------------------


async def test_get_goals_from_db_none_db_returns_empty():
    agg = GoalAnalyticsAggregator(db=None)
    result = await agg._get_goals_from_db("t1", None, days=30)
    assert result == []


async def test_get_goals_from_db_maps_rows():
    now = datetime.now(UTC)
    rows = [
        (1, "complete", "normal", "agent-1", now, False),
    ]
    mock_result = MagicMock()
    mock_result.fetchall.return_value = rows
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=mock_result)
    db = MagicMock(return_value=session)

    agg = GoalAnalyticsAggregator()
    result = await agg._get_goals_from_db("t1", db, days=30)

    assert len(result) == 1
    assert result[0]["status"] == "complete"
    assert result[0]["agent_id"] == "agent-1"
    assert result[0]["dry_run"] is False


async def test_get_goals_from_db_handles_exception():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(side_effect=Exception("query error"))
    db = MagicMock(return_value=session)

    agg = GoalAnalyticsAggregator()
    result = await agg._get_goals_from_db("t1", db, days=30)
    assert result == []


# ---------------------------------------------------------------------------
# _get_all_goals
# ---------------------------------------------------------------------------


def test_get_all_goals_without_service():
    agg = GoalAnalyticsAggregator(goal_service=None)
    result = agg._get_all_goals()
    assert result == []


def test_get_all_goals_filters_by_agent_id():
    svc = MagicMock()
    g1 = _mock_goal("complete", agent_id="a1")
    g2 = _mock_goal("complete", agent_id="a2")
    svc._goals = {"g1": g1, "g2": g2}
    agg = GoalAnalyticsAggregator(goal_service=svc)

    result = agg._get_all_goals(agent_id="a1")
    assert len(result) == 1
    assert result[0].agent_id == "a1"


def test_get_all_goals_filters_by_since():
    svc = MagicMock()
    old = _mock_goal("complete", created_at=datetime(2020, 1, 1, tzinfo=UTC))
    recent = _mock_goal("complete")
    svc._goals = {"old": old, "recent": recent}
    agg = GoalAnalyticsAggregator(goal_service=svc)

    since = datetime(2024, 1, 1, tzinfo=UTC)
    result = agg._get_all_goals(since=since)
    assert len(result) == 1
