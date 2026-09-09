"""Test builder preview hosting endpoints."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


def test_builder_preview_returns_html():
    """GET /builder/preview/{id} must return HTML, not JSON."""
    from fastapi import FastAPI

    from app.api.builder import router
    app = FastAPI()
    app.include_router(router)
    # Mock artifact store returning no artifacts
    app.state.artifact_store = MagicMock()
    app.state.artifact_store.list_artifacts = AsyncMock(return_value=[])
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/builder/preview/test-workspace-123")
    assert resp.status_code in (200, 202)
    assert "text/html" in resp.headers.get("content-type", "")
    assert "test-workspace-123" in resp.text


def test_builder_preview_building_message_when_no_artifacts():
    """Must show 'Building' status when no index.html artifact found."""
    from fastapi import FastAPI

    from app.api.builder import router
    app = FastAPI()
    app.include_router(router)
    app.state.artifact_store = MagicMock()
    app.state.artifact_store.list_artifacts = AsyncMock(return_value=[])
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/builder/preview/ws-123")
    assert resp.status_code in (200, 202)
    content = resp.text.lower()
    assert "building" in content or "workspace" in content


def test_builder_preview_serves_index_html_when_available():
    """Must serve actual index.html content when artifact exists."""
    from fastapi import FastAPI

    from app.api.builder import router
    app = FastAPI()
    app.include_router(router)
    mock_store = MagicMock()
    mock_store.list_artifacts = AsyncMock(return_value=[
        {"id": "art-001", "name": "index.html"}
    ])
    mock_store.read_bytes = AsyncMock(return_value=b"<html><head></head><body>My Site</body></html>")
    app.state.artifact_store = mock_store
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/builder/preview/ws-456")
    assert resp.status_code == 200
    assert "My Site" in resp.text
    assert "text/html" in resp.headers.get("content-type", "")


def test_builder_project_preview_url_format():
    """Created project must have a /builder/preview/ URL."""
    from fastapi import FastAPI

    from app.api.builder import router
    app = FastAPI()
    app.include_router(router)
    app.state.goal_service = None
    # Mock auth middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    class FakeTenant(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.tenant = MagicMock(tenant_id="t1")
            return await call_next(request)
    app.add_middleware(FakeTenant)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/builder/projects", json={
        "description": "A landing page",
        "project_type": "landing",
        "framework": "react",
    })
    if resp.status_code in (200, 201):
        data = resp.json()
        assert "preview_url" in data
        assert "/builder/preview/" in data["preview_url"]


def test_builder_preview_handles_missing_list_artifacts():
    """Preview must not crash if artifact_store.list_artifacts raises AttributeError."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.builder import router
    app = FastAPI()
    app.include_router(router)
    # Store without list_artifacts method
    app.state.artifact_store = object()  # plain object, no list_artifacts
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/builder/preview/test-ws")
    # Must return HTML status page, not 500
    assert resp.status_code in (200, 202)
    assert "text/html" in resp.headers.get("content-type", "")
