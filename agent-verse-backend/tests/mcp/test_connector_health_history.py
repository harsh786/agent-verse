"""Tests for connector health snapshot persistence."""
import pytest
from httpx import ASGITransport, AsyncClient

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
async def test_health_history_is_503_without_db(authed_client):
    """MCPREG-02: no DB is not "never checked" — an honest 503, not []."""
    r = await authed_client.get("/connectors/unknown-server/health")
    assert r.status_code == 503


class _BrokenSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin(self):
        return self

    async def execute(self, *a, **k):
        raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_health_history_db_error_is_503(app, authed_client):
    app.state.db_session_factory = lambda: _BrokenSession()
    r = await authed_client.get("/connectors/srv/health")
    assert r.status_code == 503
    assert "db down" not in r.text


@pytest.mark.asyncio
async def test_health_history_limit_is_bounded(authed_client):
    r = await authed_client.get("/connectors/srv/health", params={"limit": 201})
    assert r.status_code == 422
    r = await authed_client.get("/connectors/srv/health", params={"limit": 0})
    assert r.status_code == 422
