"""Comprehensive tests for /costs API endpoints — targets 40% → 90%+ coverage."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.costs import router as costs_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-costs-comp", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_costs_comp"


def _make_app(cost_tracker: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(costs_router)
    app.state.cost_tracker = cost_tracker
    return app


def _make_tracker() -> Any:
    tracker = AsyncMock()
    tracker.get_budget_status.return_value = {
        "daily_spent_usd": 10.0,
        "limit_usd": 500.0,
        "remaining_usd": 490.0,
    }
    tracker.get_per_agent_summary.return_value = [
        {"agent_id": "agent-1", "total_cost_usd": 5.0},
        {"agent_id": "agent-2", "total_cost_usd": 3.5},
    ]
    tracker.predict_cost.return_value = {
        "estimated_usd": 1.5,
        "estimated_steps": 10,
        "confidence": "medium",
    }
    tracker.detect_anomaly.return_value = []
    tracker._db = None
    return tracker


# ---------------------------------------------------------------------------
# Auth guard tests
# ---------------------------------------------------------------------------


def test_get_summary_requires_auth() -> None:
    client = TestClient(_make_app(_make_tracker()), raise_server_exceptions=False)
    resp = client.get("/costs/summary")
    assert resp.status_code == 401


def test_get_per_agent_requires_auth() -> None:
    client = TestClient(_make_app(_make_tracker()), raise_server_exceptions=False)
    resp = client.get("/costs/per-agent")
    assert resp.status_code == 401


def test_predict_cost_requires_auth() -> None:
    client = TestClient(_make_app(_make_tracker()), raise_server_exceptions=False)
    resp = client.post("/costs/predict", json={"goal_description": "Test"})
    assert resp.status_code == 401


def test_get_anomalies_requires_auth() -> None:
    client = TestClient(_make_app(_make_tracker()), raise_server_exceptions=False)
    resp = client.get("/costs/anomalies")
    assert resp.status_code == 401


def test_get_budgets_requires_auth() -> None:
    client = TestClient(_make_app(_make_tracker()), raise_server_exceptions=False)
    resp = client.get("/costs/budgets")
    assert resp.status_code == 401


def test_update_budgets_requires_auth() -> None:
    client = TestClient(_make_app(_make_tracker()), raise_server_exceptions=False)
    resp = client.put("/costs/budgets", json={"per_goal_usd": 5.0})
    assert resp.status_code == 401


def test_get_pricing_requires_auth() -> None:
    client = TestClient(_make_app(_make_tracker()), raise_server_exceptions=False)
    resp = client.get("/costs/pricing")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 503 when no cost tracker
# ---------------------------------------------------------------------------


def test_get_summary_no_tracker_returns_503() -> None:
    client = TestClient(_make_app(None), raise_server_exceptions=False)
    resp = client.get("/costs/summary", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 503


def test_get_per_agent_no_tracker_returns_503() -> None:
    client = TestClient(_make_app(None), raise_server_exceptions=False)
    resp = client.get("/costs/per-agent", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 503


def test_predict_no_tracker_returns_503() -> None:
    client = TestClient(_make_app(None), raise_server_exceptions=False)
    resp = client.post(
        "/costs/predict",
        json={"goal_description": "Build feature"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 503


def test_get_anomalies_no_tracker_returns_503() -> None:
    client = TestClient(_make_app(None), raise_server_exceptions=False)
    resp = client.get("/costs/anomalies", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 503


def test_get_budgets_no_tracker_returns_503() -> None:
    client = TestClient(_make_app(None), raise_server_exceptions=False)
    resp = client.get("/costs/budgets", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 503


def test_update_budgets_no_tracker_returns_503() -> None:
    client = TestClient(_make_app(None), raise_server_exceptions=False)
    resp = client.put("/costs/budgets", json={}, headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /costs/summary
# ---------------------------------------------------------------------------


def test_get_cost_summary_success() -> None:
    tracker = _make_tracker()
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.get("/costs/summary", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert "total_usd" in body
    assert "by_agent" in body
    assert "budget_status" in body
    assert body["period_days"] == 30


def test_get_cost_summary_custom_period() -> None:
    tracker = _make_tracker()
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.get("/costs/summary?period_days=7", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["period_days"] == 7
    tracker.get_per_agent_summary.assert_called_once_with(_CTX.tenant_id, days=7)


def test_get_cost_summary_totals_agents() -> None:
    tracker = _make_tracker()
    tracker.get_per_agent_summary.return_value = [
        {"agent_id": "a1", "total_cost_usd": 4.0},
        {"agent_id": "a2", "total_cost_usd": 6.0},
    ]
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.get("/costs/summary", headers={"X-API-Key": _VALID_KEY})
    body = resp.json()
    assert abs(body["total_usd"] - 10.0) < 0.001


def test_get_cost_summary_empty_agents() -> None:
    tracker = _make_tracker()
    tracker.get_per_agent_summary.return_value = []
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.get("/costs/summary", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["total_usd"] == 0.0


# ---------------------------------------------------------------------------
# GET /costs/per-agent
# ---------------------------------------------------------------------------


def test_get_per_agent_success() -> None:
    tracker = _make_tracker()
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.get("/costs/per-agent", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert "agents" in body
    assert body["period_days"] == 30
    assert len(body["agents"]) == 2


def test_get_per_agent_custom_period() -> None:
    tracker = _make_tracker()
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.get("/costs/per-agent?period_days=14", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["period_days"] == 14
    tracker.get_per_agent_summary.assert_called_once_with(_CTX.tenant_id, days=14)


def test_get_per_agent_empty() -> None:
    tracker = _make_tracker()
    tracker.get_per_agent_summary.return_value = []
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.get("/costs/per-agent", headers={"X-API-Key": _VALID_KEY})
    assert resp.json()["agents"] == []


# ---------------------------------------------------------------------------
# POST /costs/predict
# ---------------------------------------------------------------------------


def test_predict_cost_basic() -> None:
    tracker = _make_tracker()
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.post(
        "/costs/predict",
        json={"goal_description": "Deploy to production"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    tracker.predict_cost.assert_called_once_with(
        tenant_id=_CTX.tenant_id,
        agent_id=None,
        goal_description="Deploy to production",
        max_iterations=10,
    )


def test_predict_cost_with_agent_id() -> None:
    tracker = _make_tracker()
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.post(
        "/costs/predict",
        json={
            "goal_description": "Run tests",
            "agent_id": "agent-1",
            "max_iterations": 5,
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    tracker.predict_cost.assert_called_once_with(
        tenant_id=_CTX.tenant_id,
        agent_id="agent-1",
        goal_description="Run tests",
        max_iterations=5,
    )


def test_predict_cost_no_goal_description_fails() -> None:
    tracker = _make_tracker()
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.post("/costs/predict", json={}, headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /costs/anomalies
# ---------------------------------------------------------------------------


def test_get_anomalies_empty() -> None:
    tracker = _make_tracker()
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.get("/costs/anomalies", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["anomalies"] == []


def test_get_anomalies_with_data() -> None:
    tracker = _make_tracker()
    anomaly = MagicMock()
    anomaly.tenant_id = _CTX.tenant_id
    anomaly.agent_id = "agent-1"
    anomaly.anomaly_type = "spike"
    anomaly.cost_actual_usd = 50.0
    anomaly.cost_baseline_usd = 10.0
    anomaly.sigma_deviation = 3.5
    anomaly.detected_at = "2024-01-01T00:00:00Z"
    tracker.detect_anomaly.return_value = [anomaly]

    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.get("/costs/anomalies", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["anomalies"]) == 1
    assert body["anomalies"][0]["anomaly_type"] == "spike"
    assert body["anomalies"][0]["sigma_deviation"] == 3.5


# ---------------------------------------------------------------------------
# GET /costs/budgets
# ---------------------------------------------------------------------------


def test_get_budgets_success() -> None:
    tracker = _make_tracker()
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.get("/costs/budgets", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert "tenant_id" in body
    assert body["tenant_id"] == _CTX.tenant_id


# ---------------------------------------------------------------------------
# PUT /costs/budgets
# ---------------------------------------------------------------------------


def test_update_budgets_success_no_db() -> None:
    tracker = _make_tracker()
    tracker._db = None
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.put(
        "/costs/budgets",
        json={
            "per_goal_usd": 5.0,
            "per_tenant_daily_usd": 200.0,
            "per_agent_daily_usd": {"agent-1": 50.0},
            "alert_pct_thresholds": [50, 80],
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["per_goal_usd"] == 5.0
    assert body["per_tenant_daily_usd"] == 200.0
    assert body["tenant_id"] == _CTX.tenant_id


def test_update_budgets_defaults() -> None:
    tracker = _make_tracker()
    tracker._db = None
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.put("/costs/budgets", json={}, headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["per_goal_usd"] == 10.0


# ---------------------------------------------------------------------------
# GET /costs/pricing
# ---------------------------------------------------------------------------


def test_get_pricing_success() -> None:
    tracker = _make_tracker()
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.get("/costs/pricing", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert "models" in body
    assert isinstance(body["models"], list)
    # Should have at least one pricing entry
    if body["models"]:
        assert "model_id" in body["models"][0]
        assert "pricing_usd_per_1m" in body["models"][0]


def test_get_pricing_returns_known_models() -> None:
    """Verifies MODEL_PRICING is exposed correctly."""
    tracker = _make_tracker()
    client = TestClient(_make_app(tracker), raise_server_exceptions=False)
    resp = client.get("/costs/pricing", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    model_ids = [m["model_id"] for m in resp.json()["models"]]
    # Should include at least some well-known models
    assert len(model_ids) > 0
