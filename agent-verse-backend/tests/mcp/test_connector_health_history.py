"""Tests for connector health snapshot persistence."""
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def authed_client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "T", "email": "t@t.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        yield c


@pytest.mark.asyncio
async def test_health_history_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/connectors/some-server/health")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_health_history_returns_empty_without_db(authed_client):
    r = await authed_client.get("/connectors/unknown-server/health")
    assert r.status_code == 200
    assert r.json() == []
