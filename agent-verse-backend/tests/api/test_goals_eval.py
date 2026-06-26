"""Tests for GET /goals/{id}/eval endpoint."""
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def authed_client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/tenants/signup", json={"name": "Test", "email": "t@test.com"})
        assert resp.status_code == 201
        c.headers["X-API-Key"] = resp.json()["api_key"]
        yield c


async def test_eval_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/goals/nonexistent/eval")
        assert resp.status_code == 401


async def test_eval_404_for_unknown_goal(authed_client):
    resp = await authed_client.get("/goals/doesnotexist/eval")
    assert resp.status_code == 404


async def test_eval_after_dry_run(authed_client):
    """A dry-run goal creates a goal_id we can request eval for."""
    submit = await authed_client.post(
        "/goals", json={"goal": "evaluate this", "dry_run": True}
    )
    assert submit.status_code == 202
    goal_id = submit.json()["goal_id"]

    resp = await authed_client.get(f"/goals/{goal_id}/eval")
    assert resp.status_code == 200
    data = resp.json()
    assert "goal_id" in data
    assert "status" in data
    assert data["goal_id"] == goal_id
    # A dry-run goal is not evaluated by a real runner
    assert data["status"] in ("not_evaluated", "evaluated")


async def test_eval_returns_all_fields_when_evaluated(authed_client):
    """Eval response always includes the required scorecard envelope keys."""
    submit = await authed_client.post(
        "/goals", json={"goal": "check this goal", "dry_run": True}
    )
    goal_id = submit.json()["goal_id"]

    resp = await authed_client.get(f"/goals/{goal_id}/eval")
    assert resp.status_code == 200
    data = resp.json()
    # These fields must always be present regardless of evaluation state
    for field in ("goal_id", "status", "scores"):
        assert field in data, f"Missing field: {field}"


async def test_eval_goal_belongs_to_tenant(app):
    """A tenant cannot fetch the eval for another tenant's goal."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c1:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2:
            r1 = await c1.post("/tenants/signup", json={"name": "T1", "email": "ta@t.com"})
            r2 = await c2.post("/tenants/signup", json={"name": "T2", "email": "tb@t.com"})
            c1.headers["X-API-Key"] = r1.json()["api_key"]
            c2.headers["X-API-Key"] = r2.json()["api_key"]

            sub = await c1.post("/goals", json={"goal": "tenant isolation", "dry_run": True})
            goal_id = sub.json()["goal_id"]

            # T2 should not see T1's goal eval
            resp = await c2.get(f"/goals/{goal_id}/eval")
            assert resp.status_code == 404
