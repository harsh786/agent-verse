"""API-level tests for perception endpoints."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.api.perception import router as perception_router
from app.api.tenants import router as tenants_router
from app.main import create_app
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware
from app.services.tenant_service import TenantService


@pytest.fixture
def app() -> FastAPI:
    """Full app with perception router mounted (perception is not in the default factory)."""
    _app = create_app()
    _app.include_router(perception_router)
    return _app


@pytest.fixture
async def authed_client(app: FastAPI):
    """Authenticated AsyncClient — signs up a new tenant and sets the API key header."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/tenants/signup", json={"name": "Test", "email": "t@test.com"}
        )
        assert resp.status_code == 201, resp.text
        c.headers["X-API-Key"] = resp.json()["api_key"]
        yield c


@pytest.mark.asyncio
async def test_status_requires_auth(app: FastAPI):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.get("/perception/status")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_status_returns_shape(authed_client: AsyncClient):
    resp = await authed_client.get("/perception/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "playwright_available" in data
    assert "vision_available" in data
    assert "browser_actions" in data
    assert isinstance(data["browser_actions"], list)
    assert len(data["browser_actions"]) >= 3


@pytest.mark.asyncio
async def test_screenshot_requires_auth(app: FastAPI):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/perception/screenshot", json={"url": "https://example.com"}
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_screenshot_validates_url(authed_client: AsyncClient):
    resp = await authed_client.post(
        "/perception/screenshot", json={"url": "not-a-url"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_screenshot_returns_result_shape(authed_client: AsyncClient):
    resp = await authed_client.post(
        "/perception/screenshot", json={"url": "https://example.com"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "success" in data
    assert "url" in data
    assert data["url"] == "https://example.com"


@pytest.mark.asyncio
async def test_analyze_requires_body(authed_client: AsyncClient):
    """analyze with no screenshot_b64 and no url returns 400."""
    resp = await authed_client.post(
        "/perception/analyze", json={"question": "what?"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_analyze_with_screenshot_b64(authed_client: AsyncClient):
    """analyze with a screenshot_b64 returns analysis shape."""
    # Minimal valid PNG (1×1 white pixel) base64-encoded
    tiny_png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    resp = await authed_client.post(
        "/perception/analyze",
        json={"screenshot_b64": tiny_png, "question": "What do you see?"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "analysis" in data
    assert "question" in data


@pytest.mark.asyncio
async def test_extract_requires_auth(app: FastAPI):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/perception/extract", json={"url": "https://example.com"}
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_extract_validates_url(authed_client: AsyncClient):
    resp = await authed_client.post(
        "/perception/extract", json={"url": "bad-url"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_extract_returns_result_shape(authed_client: AsyncClient):
    resp = await authed_client.post(
        "/perception/extract",
        json={"url": "https://example.com", "selector": "body"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "success" in data
    assert "url" in data
    assert "char_count" in data


@pytest.mark.asyncio
async def test_goal_with_image_requires_auth(app: FastAPI):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/perception/goal-with-image", json={"goal": "test"}
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_goal_with_image_submits_goal(authed_client: AsyncClient):
    """Goal with just text (no image) is submitted normally via dry_run."""
    resp = await authed_client.post(
        "/perception/goal-with-image",
        json={"goal": "Analyze this for me", "dry_run": True},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "goal_id" in data
    assert "has_visual_context" in data
    assert data["has_visual_context"] is False


@pytest.mark.asyncio
async def test_goal_with_image_validates_image_url(authed_client: AsyncClient):
    resp = await authed_client.post(
        "/perception/goal-with-image",
        json={"goal": "Analyze this", "image_url": "not-a-url"},
    )
    assert resp.status_code == 400
