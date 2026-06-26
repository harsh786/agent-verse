"""Tests for eval suite REST API endpoints."""
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
async def test_create_eval_suite(authed_client):
    r = await authed_client.post("/intelligence/eval-suites", json={"name": "smoke"})
    assert r.status_code == 201
    data = r.json()
    assert "suite_id" in data


@pytest.mark.asyncio
async def test_list_eval_suites(authed_client):
    await authed_client.post("/intelligence/eval-suites", json={})
    r = await authed_client.get("/intelligence/eval-suites")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_add_golden_task(authed_client):
    r = await authed_client.post("/intelligence/eval-suites", json={})
    suite_id = r.json()["suite_id"]
    r2 = await authed_client.post(
        f"/intelligence/eval-suites/{suite_id}/tasks",
        json={"goal": "Fix the bug", "expected_tools": ["jira.search_issues"]}
    )
    assert r2.status_code == 201
    assert r2.json()["goal"] == "Fix the bug"


@pytest.mark.asyncio
async def test_get_suite_results_empty(authed_client):
    r = await authed_client.post("/intelligence/eval-suites", json={})
    suite_id = r.json()["suite_id"]
    r2 = await authed_client.get(f"/intelligence/eval-suites/{suite_id}/results")
    assert r2.status_code == 200
    assert r2.json() == []


@pytest.mark.asyncio
async def test_eval_suite_auth_required(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/intelligence/eval-suites")).status_code == 401
        assert (await c.post("/intelligence/eval-suites", json={})).status_code == 401
