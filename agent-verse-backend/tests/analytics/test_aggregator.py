"""Tests for GoalAnalyticsAggregator."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.analytics.aggregator import GoalAnalyticsAggregator


def _make_mock_goal(
    status: str,
    cost: float = 0.01,
    agent_id: str = "agent-1",
):
    g = MagicMock()
    g.status = status
    g.cost_usd = cost
    g.agent_id = agent_id
    g.created_at = datetime.now(UTC)
    g.completed_at = None
    g.events = []
    g.eval_score = 0.85 if "complete" in status.lower() else None
    return g


def test_goal_metrics_success_rate():
    svc = MagicMock()
    svc._goals = {
        "g1": _make_mock_goal("complete"),
        "g2": _make_mock_goal("complete"),
        "g3": _make_mock_goal("failed"),
    }
    agg = GoalAnalyticsAggregator(svc)
    m = agg.goal_metrics(days=30)
    assert m.total == 3
    assert m.completed == 2
    assert m.failed == 1
    assert abs(m.success_rate - 2 / 3) < 0.01


def test_cost_trends_buckets_by_day():
    svc = MagicMock()
    svc._goals = {
        "g1": _make_mock_goal("complete", cost=0.10),
        "g2": _make_mock_goal("complete", cost=0.20),
    }
    agg = GoalAnalyticsAggregator(svc)
    trends = agg.cost_trends(days=30, bucket="day")
    # All goals created today, so one bucket
    assert len(trends) == 1
    assert abs(trends[0]["cost_usd"] - 0.30) < 1e-6


def test_tool_metrics_empty_when_no_events():
    svc = MagicMock()
    svc._goals = {
        "g1": _make_mock_goal("complete"),
    }
    agg = GoalAnalyticsAggregator(svc)
    tools = agg.tool_metrics(days=30)
    assert tools == []


def test_agent_metrics_groups_by_agent():
    svc = MagicMock()
    svc._goals = {
        "g1": _make_mock_goal("complete", agent_id="agent-a"),
        "g2": _make_mock_goal("failed", agent_id="agent-a"),
        "g3": _make_mock_goal("complete", agent_id="agent-b"),
    }
    agg = GoalAnalyticsAggregator(svc)
    result = agg.agent_metrics(days=30)
    agent_ids = {a.agent_id for a in result}
    assert "agent-a" in agent_ids
    assert "agent-b" in agent_ids
