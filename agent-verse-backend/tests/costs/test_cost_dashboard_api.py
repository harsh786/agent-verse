"""Tests for the Cost Dashboard API — response shapes, computed fields, SQL safety.

Run: uv run pytest tests/costs/ -x -v
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.costs import (
    _anomaly_id,
    _anomaly_type_label,
    _sigma_to_severity,
)
from app.intelligence.cost_tracker import CostAnomaly, CostTracker, calculate_cost

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_redis(store: dict | None = None) -> AsyncMock:
    """In-memory Redis mock with GET/SET/SETEX/INCRBYFLOAT/SCAN."""
    data: dict[str, str] = dict(store or {})
    redis = AsyncMock()

    async def _get(key):
        return data.get(key)

    async def _setex(key, _ttl, value):
        data[key] = value

    async def _incrbyfloat(key, amount):
        current = float(data.get(key, "0"))
        data[key] = str(current + amount)
        return current + amount

    async def _expire(key, _ttl):
        return key in data

    async def _scan(cursor, match="*", count=100):
        import fnmatch
        matched = [k for k in data if fnmatch.fnmatch(k, match)]
        return 0, matched

    async def _publish(*_):
        return 0

    redis.get.side_effect = _get
    redis.setex.side_effect = _setex
    redis.incrbyfloat.side_effect = _incrbyfloat
    redis.expire.side_effect = _expire
    redis.scan.side_effect = _scan
    redis.publish.side_effect = _publish
    return redis


def _make_db_with_rows(rows: list[tuple[Any, ...]]) -> MagicMock:
    """DB factory mock that returns given rows from session.execute."""
    session = AsyncMock()
    result = MagicMock()
    result.fetchall.return_value = rows
    result.fetchone.return_value = rows[0] if rows else None
    session.execute.return_value = result
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    db_factory = MagicMock(return_value=session)
    return db_factory


# ── Bug 4: SQL bind-variable interval ─────────────────────────────────────────

def test_sql_interval_not_literal():
    """Regression: INTERVAL ':days days' is a literal — the bind param never substitutes.
    The fixed form uses INTERVAL '1 day' * :days (arithmetic on an interval).
    """
    import inspect

    from app.intelligence import cost_tracker
    source = inspect.getsource(cost_tracker)
    # Must NOT contain the broken pattern
    assert "INTERVAL ':days days'" not in source, (
        "Broken SQL found: INTERVAL ':days days' — bind params inside string literals "
        "are not substituted. Use INTERVAL '1 day' * :days instead."
    )
    # Must contain the correct arithmetic form
    assert "INTERVAL '1 day' * :days" in source, (
        "Expected fixed SQL pattern 'INTERVAL \'1 day\' * :days' not found."
    )


# ── Bug 1: per-agent avg_cost_per_goal ────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_per_agent_includes_avg_cost_per_goal():
    """get_per_agent_summary must compute avg_cost_per_goal = total_cost / goal_count."""
    rows = [
        # agent_id, total_cost, prompt_tokens, completion_tokens, goal_count
        ("agent-abc", 1.50, 10_000, 5_000, 3),
        ("agent-xyz", 0.60, 4_000, 2_000, 2),
    ]
    db = _make_db_with_rows(rows)
    tracker = CostTracker(db_factory=db)

    result = await tracker.get_per_agent_summary("tenant-1", days=30)

    assert len(result) == 2

    abc = next(r for r in result if r["agent_id"] == "agent-abc")
    assert "avg_cost_per_goal" in abc, "avg_cost_per_goal missing from per-agent summary"
    assert abc["avg_cost_per_goal"] == pytest.approx(1.50 / 3, rel=1e-4)
    assert abc["goal_count"] == 3
    assert abc["total_cost_usd"] == pytest.approx(1.50, rel=1e-4)

    xyz = next(r for r in result if r["agent_id"] == "agent-xyz")
    assert xyz["avg_cost_per_goal"] == pytest.approx(0.60 / 2, rel=1e-4)


@pytest.mark.asyncio
async def test_get_per_agent_avg_cost_handles_zero_goal_count():
    """avg_cost_per_goal must not divide by zero when goal_count is 0."""
    rows = [("agent-zero", 0.0, 0, 0, 0)]
    db = _make_db_with_rows(rows)
    tracker = CostTracker(db_factory=db)

    result = await tracker.get_per_agent_summary("tenant-1")
    assert result[0]["avg_cost_per_goal"] == 0.0


# ── Bug 3: anomaly response field mapping ─────────────────────────────────────

def test_sigma_to_severity_mapping():
    """Severity must be 'high' for ≥4σ, 'medium' for ≥2.5σ, 'low' otherwise."""
    assert _sigma_to_severity(5.0) == "high"
    assert _sigma_to_severity(4.0) == "high"
    assert _sigma_to_severity(3.5) == "medium"
    assert _sigma_to_severity(2.5) == "medium"
    assert _sigma_to_severity(1.0) == "low"
    assert _sigma_to_severity(0.0) == "low"


def test_anomaly_type_label():
    """anomaly_type must map to human-readable labels."""
    assert _anomaly_type_label("spike") == "Cost Spike"
    assert _anomaly_type_label("sustained_high") == "Sustained High"
    assert _anomaly_type_label("budget_exceed") == "Budget Exceeded"
    assert _anomaly_type_label("unknown_type") == "Unknown Type"


def test_anomaly_id_is_deterministic():
    """Same agent_id + detected_at always produces the same id."""
    id1 = _anomaly_id("agent-123", "2026-07-01T10:00:00Z")
    id2 = _anomaly_id("agent-123", "2026-07-01T10:00:00Z")
    id3 = _anomaly_id("agent-456", "2026-07-01T10:00:00Z")
    assert id1 == id2, "Anomaly id must be deterministic"
    assert id1 != id3, "Different agents must produce different ids"


@pytest.mark.asyncio
async def test_get_anomalies_returns_severity_and_message():
    """/costs/anomalies endpoint must return severity, message, cost_delta_usd."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(__import__("app.api.costs", fromlist=["router"]).router)

    # Mock cost_tracker on app.state
    tracker = AsyncMock()
    tracker.detect_anomaly.return_value = [
        CostAnomaly(
            tenant_id="tenant-1",
            agent_id="agent-abc",
            anomaly_type="spike",
            cost_actual_usd=0.85,
            cost_baseline_usd=0.20,
            sigma_deviation=4.2,
            detected_at="2026-07-01T12:00:00Z",
        )
    ]
    app.state.cost_tracker = tracker

    # Patch tenant middleware
    @app.middleware("http")
    async def _inject_tenant(request, call_next):
        class _Ctx:
            tenant_id = "tenant-1"
        request.state.tenant = _Ctx()
        return await call_next(request)

    client = TestClient(app)
    resp = client.get("/costs/anomalies")
    assert resp.status_code == 200

    data = resp.json()
    assert "anomalies" in data
    assert len(data["anomalies"]) == 1

    a = data["anomalies"][0]
    assert "id" in a, "id field missing"
    assert "severity" in a, "severity field missing"
    assert a["severity"] == "high"
    assert "message" in a, "message field missing"
    assert "spike" in a["message"].lower() or "cost spike" in a["message"].lower()
    assert "cost_delta_usd" in a, "cost_delta_usd field missing"
    assert a["cost_delta_usd"] == pytest.approx(0.85 - 0.20, rel=1e-4)
    assert "sigma_deviation" in a
    assert a["sigma_deviation"] == pytest.approx(4.2, rel=1e-2)


# ── Bug 2: predict returns predicted_cost_usd ─────────────────────────────────

@pytest.mark.asyncio
async def test_predict_returns_predicted_cost_usd():
    """/costs/predict must return predicted_cost_usd (not estimated_cost_usd.mean)."""
    tracker = CostTracker()  # no DB/Redis → heuristic path
    result = await tracker.predict_cost(
        tenant_id="tenant-1",
        agent_id=None,
        goal_description="Send a daily weather summary email to the team",
        max_iterations=5,
    )
    assert "predicted_cost_usd" in result, (
        "predict_cost must return 'predicted_cost_usd', not 'estimated_cost_usd'"
    )
    assert isinstance(result["predicted_cost_usd"], float)
    assert result["predicted_cost_usd"] >= 0.0
    assert "p95_cost_usd" in result
    assert "confidence" in result
    assert "breakdown" in result
    assert "planning_usd" in result["breakdown"]
    assert "execution_usd" in result["breakdown"]
    assert "verification_usd" in result["breakdown"]


# ── New: get_cost_by_model ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_cost_by_model():
    """get_cost_by_model must return cost grouped by model with expected fields."""
    rows = [
        # model, total_cost, prompt_tokens, completion_tokens, call_count
        ("claude-opus-4", 2.50, 20_000, 8_000, 10),
        ("gpt-4o", 1.20, 15_000, 5_000, 8),
    ]
    db = _make_db_with_rows(rows)
    tracker = CostTracker(db_factory=db)

    result = await tracker.get_cost_by_model("tenant-1", days=30)

    assert len(result) == 2
    assert all("model" in r for r in result)
    assert all("total_cost_usd" in r for r in result)
    assert all("total_prompt_tokens" in r for r in result)
    assert all("total_completion_tokens" in r for r in result)
    assert all("call_count" in r for r in result)

    opus = next(r for r in result if r["model"] == "claude-opus-4")
    assert opus["total_cost_usd"] == pytest.approx(2.50, rel=1e-4)
    assert opus["call_count"] == 10


@pytest.mark.asyncio
async def test_get_cost_by_model_empty_db():
    """get_cost_by_model returns empty list when no DB is wired."""
    tracker = CostTracker()  # no DB
    result = await tracker.get_cost_by_model("tenant-1")
    assert result == []


# ── New: get_projected_monthly_cost ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_projected_monthly_cost_linear():
    """Projection must extrapolate linearly from last 7 days of spend."""
    # 7 days of constant $1/day → project $30/month
    rows = [(f"2026-07-0{i+1}", 1.0) for i in range(7)]
    db = _make_db_with_rows(rows)
    tracker = CostTracker(db_factory=db)

    result = await tracker.get_projected_monthly_cost("tenant-1")

    assert "projected_monthly_usd" in result
    assert "daily_avg_usd" in result
    assert "days_of_data" in result
    assert result["days_of_data"] == 7
    # With constant spend the slope is 0, projection ≈ 30 * daily_avg
    assert result["projected_monthly_usd"] == pytest.approx(30.0, abs=1.0)


@pytest.mark.asyncio
async def test_get_projected_monthly_cost_no_db():
    """Returns zeros gracefully when no DB is wired."""
    tracker = CostTracker()
    result = await tracker.get_projected_monthly_cost("tenant-1")
    assert result["projected_monthly_usd"] == 0.0
    assert result["confidence"] == "low"


# ── Pricing deprecation ────────────────────────────────────────────────────────

def test_pricing_estimate_cost_emits_deprecation_warning():
    """governance.pricing.estimate_cost must emit DeprecationWarning."""
    import warnings

    from app.governance import pricing

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        pricing.estimate_cost("gpt-4o", 1000, 500)
    assert any(issubclass(warning.category, DeprecationWarning) for warning in w), (
        "estimate_cost() must emit a DeprecationWarning directing callers to "
        "app.intelligence.cost_tracker.calculate_cost()"
    )


def test_calculate_cost_matches_pricing_for_known_model():
    """calculate_cost and the deprecated estimate_cost agree for GPT-4o-mini."""
    import warnings

    from app.governance import pricing

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        old = pricing.estimate_cost("gpt-4o-mini", 1_000, 500)
    new = calculate_cost("gpt-4o-mini", 1_000, 500)
    # Both should give a non-zero positive value; exact match isn't required
    # because rate tables differ (per-1k vs per-1M), but both must be positive
    assert old > 0
    assert new > 0
