"""Tests for RPA execute and session endpoints."""
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


async def test_list_rpa_tools(authed_client):
    resp = await authed_client.get("/rpa/tools")
    assert resp.status_code == 200
    tools = resp.json()
    assert isinstance(tools, list)
    assert len(tools) >= 5
    names = {t["name"] for t in tools}
    assert "rpa_open_url" in names
    assert "rpa_screenshot" in names


async def test_execute_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/rpa/execute",
            json={"tool_name": "rpa_open_url", "arguments": {"url": "http://example.com"}},
        )
        assert resp.status_code == 401


async def test_execute_unknown_tool(authed_client):
    resp = await authed_client.post(
        "/rpa/execute",
        json={"tool_name": "rpa_unknown_tool", "arguments": {}},
    )
    assert resp.status_code == 400


async def test_execute_open_url(authed_client):
    resp = await authed_client.post(
        "/rpa/execute",
        json={"tool_name": "rpa_open_url", "arguments": {"url": "https://example.com"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "success" in data
    assert "output" in data
    assert "duration_ms" in data
    assert data["tool_name"] == "rpa_open_url"
    assert isinstance(data["success"], bool)
    assert isinstance(data["duration_ms"], (int, float))


async def test_execute_screenshot(authed_client):
    resp = await authed_client.post(
        "/rpa/execute",
        json={"tool_name": "rpa_screenshot", "arguments": {"name": "test-capture"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "success" in data
    assert isinstance(data["success"], bool)


async def test_execute_all_tools(authed_client):
    """All RPA tools should be executable without crashing."""
    test_cases = [
        ("rpa_open_url", {"url": "https://example.com"}),
        ("rpa_click", {"selector": "#submit", "text": "Submit"}),
        ("rpa_type", {"selector": "#input", "text": "Hello"}),
        ("rpa_extract_text", {"selector": "body"}),
        ("rpa_screenshot", {"name": "test"}),
    ]
    for tool_name, arguments in test_cases:
        resp = await authed_client.post(
            "/rpa/execute", json={"tool_name": tool_name, "arguments": arguments}
        )
        assert resp.status_code == 200, f"Tool {tool_name} failed: {resp.text}"
        data = resp.json()
        assert isinstance(data["success"], bool), f"{tool_name}: success must be bool"
        assert isinstance(data["duration_ms"], (int, float)), (
            f"{tool_name}: duration_ms must be numeric"
        )


async def test_rpa_sessions_crud(authed_client):
    """Create, list, and close RPA sessions."""
    # Create
    create_resp = await authed_client.post("/rpa/sessions")
    assert create_resp.status_code == 201
    session_id = create_resp.json()["session_id"]
    assert session_id
    assert create_resp.json()["status"] == "active"

    # List
    list_resp = await authed_client.get("/rpa/sessions")
    assert list_resp.status_code == 200
    sessions = list_resp.json()
    assert any(s["session_id"] == session_id for s in sessions)

    # Close
    close_resp = await authed_client.delete(f"/rpa/sessions/{session_id}")
    assert close_resp.status_code == 204

    # No longer in active list
    list_resp2 = await authed_client.get("/rpa/sessions")
    ids = [s["session_id"] for s in list_resp2.json()]
    assert session_id not in ids


async def test_rpa_sessions_require_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/rpa/sessions")).status_code == 401
        assert (await c.post("/rpa/sessions")).status_code == 401


async def test_rpa_session_close_unknown_returns_404(authed_client):
    """Closing a non-existent session returns 404."""
    resp = await authed_client.delete("/rpa/sessions/does-not-exist")
    assert resp.status_code == 404


async def test_rpa_session_tenant_isolation(app):
    """Session created by T1 is not visible to T2."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c1:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2:
            r1 = await c1.post("/tenants/signup", json={"name": "T1", "email": "rpa1@t.com"})
            r2 = await c2.post("/tenants/signup", json={"name": "T2", "email": "rpa2@t.com"})
            c1.headers["X-API-Key"] = r1.json()["api_key"]
            c2.headers["X-API-Key"] = r2.json()["api_key"]

            cr = await c1.post("/rpa/sessions")
            session_id = cr.json()["session_id"]

            t2_sessions = (await c2.get("/rpa/sessions")).json()
            assert not any(s["session_id"] == session_id for s in t2_sessions)
