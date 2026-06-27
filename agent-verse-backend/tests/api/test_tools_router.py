"""Tests for the /tools/* API endpoints."""
from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.mark.asyncio
async def test_execute_code_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tools/execute-code", json={"code": "print(1)"})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_file_ops_require_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/tools/files")).status_code == 401


@pytest.mark.asyncio
async def test_email_send_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/tools/email/send",
            json={"to": "x@x.com", "subject": "hi", "body": "hello"},
        )
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_openapi_import_requires_auth(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/connectors/import-openapi", json={})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_execute_code_happy_path():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "ToolTest", "email": "code_tool@t.com"})
        assert r.status_code == 201
        c.headers["X-API-Key"] = r.json()["api_key"]
        r2 = await c.post(
            "/tools/execute-code",
            json={"code": "print('hi')", "language": "python"},
        )
        assert r2.status_code == 200
        assert "success" in r2.json()
