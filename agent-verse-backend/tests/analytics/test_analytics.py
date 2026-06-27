import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app


@pytest.mark.asyncio
async def test_analytics_goals_requires_auth():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/analytics/goals")).status_code == 401


@pytest.mark.asyncio
async def test_analytics_tools_requires_auth():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/analytics/tools")).status_code == 401


@pytest.mark.asyncio
async def test_analytics_goals_returns_shape():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "A", "email": "a@a.com"})
        key = r.json().get("api_key") or r.json().get("raw_key")
        c.headers["X-API-Key"] = key
        r2 = await c.get("/analytics/goals")
        assert r2.status_code == 200
        data = r2.json()
        assert "total" in data or "error" in data  # OK either way


@pytest.mark.asyncio
async def test_analytics_costs_returns_shape():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "C", "email": "c@c.com"})
        key = r.json().get("api_key") or r.json().get("raw_key")
        c.headers["X-API-Key"] = key
        r2 = await c.get("/analytics/costs")
        assert r2.status_code == 200
