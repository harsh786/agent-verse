"""Tests for DB-backed aggregator methods: tool_metrics_db, cost_trends_db, cost_by_model_db."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.analytics.aggregator import GoalAnalyticsAggregator

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_db_factory(rows: list[Any], *, scalars: bool = False):
    """Return a DB factory whose session.execute() returns the given rows."""
    mock_result = MagicMock()
    mock_result.fetchall.return_value = rows

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    def factory():
        return mock_session

    return factory


# ── tool_metrics_db ───────────────────────────────────────────────────────────


def test_tool_metrics_db_returns_results():
    """DB-backed tool metrics should parse rows into ToolMetrics objects."""
    rows = [
        ("jira:search", 100, 5, 250.0),    # tool_name, call_count, failure_count, avg_latency
        ("github:create_pr", 50, 2, 320.0),
    ]
    db = _make_db_factory(rows)
    agg = GoalAnalyticsAggregator(db=db)
    result = asyncio.run(agg.tool_metrics_db(tenant_id="tenant-1", days=30))
    assert len(result) == 2
    first = result[0]
    assert first.tool_name == "jira:search"
    assert first.call_count == 100
    assert first.failure_count == 5
    assert abs(first.failure_rate - 0.05) < 0.001
    assert first.avg_latency_ms == 250.0


def test_tool_metrics_db_failure_rate_calculation():
    """Failure rate should be failure_count / call_count."""
    rows = [("tool_x", 10, 3, 100.0)]
    db = _make_db_factory(rows)
    agg = GoalAnalyticsAggregator(db=db)
    result = asyncio.run(agg.tool_metrics_db(tenant_id="tenant-1", days=7))
    assert abs(result[0].failure_rate - 0.3) < 0.001
    # success_rate is 1 - failure_rate, calculated at the API layer (not in ToolMetrics)
    assert abs((1 - result[0].failure_rate) - 0.7) < 0.001


def test_tool_metrics_db_empty_results_falls_back_to_memory():
    """Empty DB result should fall back to in-memory (returns empty from in-memory too)."""
    db = _make_db_factory([])  # no rows
    svc = MagicMock()
    svc._goals = {}
    agg = GoalAnalyticsAggregator(goal_service=svc, db=db)
    result = asyncio.run(agg.tool_metrics_db(tenant_id="tenant-1", days=30))
    # Falls back to in-memory which also returns empty
    assert result == []


def test_tool_metrics_db_no_db_falls_back_to_memory():
    """No DB should fall back to in-memory tool_metrics()."""
    svc = MagicMock()
    svc._goals = {}
    agg = GoalAnalyticsAggregator(goal_service=svc, db=None)
    # Should not raise; returns in-memory result (empty)
    result = asyncio.run(agg.tool_metrics_db(tenant_id="tenant-1", days=30))
    assert isinstance(result, list)


def test_tool_metrics_db_exception_falls_back_to_memory():
    """DB exception should fall back to in-memory tool_metrics()."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("DB error"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    svc = MagicMock()
    svc._goals = {}
    agg = GoalAnalyticsAggregator(goal_service=svc, db=lambda: mock_session)
    result = asyncio.run(agg.tool_metrics_db(tenant_id="tenant-1", days=30))
    assert isinstance(result, list)  # graceful fallback


# ── cost_trends_db ────────────────────────────────────────────────────────────


def test_cost_trends_db_returns_daily_buckets():
    """DB-backed cost trends should return period+cost_usd dicts."""
    rows = [
        ("2026-06-01", 1.50),
        ("2026-06-02", 2.30),
        ("2026-06-03", 0.80),
    ]
    db = _make_db_factory(rows)
    agg = GoalAnalyticsAggregator(db=db)
    result = asyncio.run(agg.cost_trends_db(tenant_id="tenant-1", days=30))
    assert len(result) == 3
    assert result[0]["period"] == "2026-06-01"
    assert abs(result[0]["cost_usd"] - 1.50) < 0.001
    assert result[2]["period"] == "2026-06-03"


def test_cost_trends_db_rounds_cost():
    """cost_usd values should be rounded to 6 decimal places."""
    rows = [("2026-06-01", 1.123456789)]
    db = _make_db_factory(rows)
    agg = GoalAnalyticsAggregator(db=db)
    result = asyncio.run(agg.cost_trends_db(tenant_id="tenant-1", days=7))
    assert result[0]["cost_usd"] == round(1.123456789, 6)


def test_cost_trends_db_no_db_falls_back():
    """No DB should fall back to in-memory cost_trends()."""
    svc = MagicMock()
    svc._goals = {}
    agg = GoalAnalyticsAggregator(goal_service=svc, db=None)
    result = asyncio.run(agg.cost_trends_db(tenant_id="tenant-1", days=30))
    assert isinstance(result, list)


def test_cost_trends_db_empty_falls_back_to_memory():
    """Empty DB result falls back to in-memory."""
    db = _make_db_factory([])
    svc = MagicMock()
    svc._goals = {}
    agg = GoalAnalyticsAggregator(goal_service=svc, db=db)
    result = asyncio.run(agg.cost_trends_db(tenant_id="tenant-1", days=30))
    assert isinstance(result, list)


# ── cost_by_model_db ──────────────────────────────────────────────────────────


def test_cost_by_model_db_returns_dict():
    """Should return a dict of model_name → total_cost."""
    rows = [
        ("claude-3-5-sonnet", 5.20),
        ("gpt-4o", 3.10),
    ]
    db = _make_db_factory(rows)
    agg = GoalAnalyticsAggregator(db=db)
    result = asyncio.run(agg.cost_by_model_db(tenant_id="tenant-1", days=30))
    assert "claude-3-5-sonnet" in result
    assert abs(result["claude-3-5-sonnet"] - 5.20) < 0.001
    assert abs(result["gpt-4o"] - 3.10) < 0.001


def test_cost_by_model_db_no_db_returns_empty():
    """No DB should return empty dict."""
    agg = GoalAnalyticsAggregator(db=None)
    result = asyncio.run(agg.cost_by_model_db(tenant_id="tenant-1", days=30))
    assert result == {}


def test_cost_by_model_db_exception_returns_empty():
    """DB exception should return empty dict gracefully."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("connection refused"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    agg = GoalAnalyticsAggregator(db=lambda: mock_session)
    result = asyncio.run(agg.cost_by_model_db(tenant_id="tenant-1", days=30))
    assert result == {}
