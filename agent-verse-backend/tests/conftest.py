"""Shared pytest fixtures for all test packages."""

from __future__ import annotations

import os

import pytest

# Allow subprocess execution in test environments (not production).
# The CodeInterpreter uses subprocess as Docker fallback in dev/CI.
os.environ.setdefault("AGENTVERSE_ALLOW_SUBPROCESS_EXEC", "true")
# Ensure tests run in development mode (not production fail-closed)
os.environ.setdefault("ENVIRONMENT", "development")


@pytest.fixture
def app():
    from app.main import create_app

    return create_app()


@pytest.fixture
async def signed_up_client(app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "Test", "email": "b@b.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        yield c
