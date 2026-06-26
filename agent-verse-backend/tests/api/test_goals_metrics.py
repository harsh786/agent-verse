"""Tests for GET /goals/metrics endpoint."""
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def authed_client(app):
    """Client with a valid tenant API key."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/tenants/signup", json={"name": "Test", "email": "t@test.com"})
        assert resp.status_code == 201
        api_key = resp.json()["api_key"]
        c.headers["X-API-Key"] = api_key
        yield c


async def test_metrics_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/goals/metrics")
        assert resp.status_code == 401


async def test_metrics_returns_expected_shape(authed_client):
    resp = await authed_client.get("/goals/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert "active_goals" in data
    assert "total_goals" in data
    assert "success_rate" in data
    assert "avg_latency_ms" in data
    assert "cost_today_usd" in data
    assert "goals_today" in data


async def test_metrics_initial_state(authed_client):
    """Fresh tenant has all-zero metrics."""
    resp = await authed_client.get("/goals/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_goals"] == 0
    assert data["total_goals"] == 0
    assert data["success_rate"] == 0.0


async def test_metrics_counts_submitted_goals(authed_client):
    """After submitting a dry-run goal, total_goals increments."""
    await authed_client.post("/goals", json={"goal": "test goal", "dry_run": True})

    resp = await authed_client.get("/goals/metrics")
    assert resp.status_code == 200
    data = resp.json()
    # dry-run goals count as submitted
    assert data["total_goals"] >= 1


async def test_metrics_tenant_isolation(app):
    """Two tenants each see only their own metrics."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c1:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2:
            r1 = await c1.post("/tenants/signup", json={"name": "T1", "email": "t1@t.com"})
            r2 = await c2.post("/tenants/signup", json={"name": "T2", "email": "t2@t.com"})
            c1.headers["X-API-Key"] = r1.json()["api_key"]
            c2.headers["X-API-Key"] = r2.json()["api_key"]

            # T1 submits a goal
            await c1.post("/goals", json={"goal": "t1 goal", "dry_run": True})

            m1 = (await c1.get("/goals/metrics")).json()
            m2 = (await c2.get("/goals/metrics")).json()

            assert m1["total_goals"] >= 1
            assert m2["total_goals"] == 0
