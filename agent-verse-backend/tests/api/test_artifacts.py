"""Tests for the artifacts REST API and MinIOArtifactStore fallback."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.artifacts import router


# ── Test app helpers ──────────────────────────────────────────────────────────


def _make_app(*, with_tenant: bool = False) -> FastAPI:
    """Create a minimal FastAPI app with the artifacts router."""
    app = FastAPI()
    app.include_router(router)

    if with_tenant:

        class _InjectTenant(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.tenant = SimpleNamespace(tenant_id="test-tenant")
                return await call_next(request)

        app.add_middleware(_InjectTenant)

    return app


# ── Unauthenticated (no tenant) → 401 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_artifacts_requires_auth() -> None:
    app = _make_app(with_tenant=False)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/artifacts")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_artifact_requires_auth() -> None:
    app = _make_app(with_tenant=False)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/artifacts/some-id")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_artifact_requires_auth() -> None:
    app = _make_app(with_tenant=False)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete("/artifacts/some-id")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_artifacts_with_goal_id_requires_auth() -> None:
    app = _make_app(with_tenant=False)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/artifacts?goal_id=g1")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_artifacts_with_artifact_type_requires_auth() -> None:
    app = _make_app(with_tenant=False)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/artifacts?artifact_type=screenshot")
    assert resp.status_code == 401


# ── Authenticated, no DB → graceful fallback ─────────────────────────────────


@pytest.mark.asyncio
async def test_list_artifacts_returns_empty_list_without_db() -> None:
    """No DB configured → returns [] not an error."""
    app = _make_app(with_tenant=True)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/artifacts")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_artifacts_goal_id_returns_empty_list_without_db() -> None:
    app = _make_app(with_tenant=True)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/artifacts?goal_id=goal-123")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_artifacts_artifact_type_returns_empty_list_without_db() -> None:
    app = _make_app(with_tenant=True)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/artifacts?artifact_type=screenshot")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_artifact_returns_404_without_db() -> None:
    """No DB configured → artifact not found."""
    app = _make_app(with_tenant=True)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/artifacts/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_artifact_returns_404_without_db() -> None:
    """No DB configured → artifact not found."""
    app = _make_app(with_tenant=True)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete("/artifacts/does-not-exist")
    assert resp.status_code == 404


# ── MinIOArtifactStore fallback to /tmp when aioboto3 missing ────────────────


@pytest.mark.asyncio
async def test_minio_artifact_store_falls_back_to_tmp_on_import_error(
    tmp_path,
) -> None:
    """When aioboto3 is not importable, write_bytes falls back to /tmp storage."""
    from app.rpa.artifacts import MinIOArtifactStore

    store = MinIOArtifactStore(endpoint_url="http://localhost:9000")

    # Simulate aioboto3 not installed by raising RuntimeError in _get_client
    original_get_client = store._get_client

    async def _raise_import_error():
        raise RuntimeError("aioboto3 not installed")

    store._get_client = _raise_import_error

    artifact = await store.write_bytes(
        goal_id="goal-fallback",
        name="test.png",
        content=b"fake-image-bytes",
    )

    assert artifact is not None
    assert artifact.size_bytes == len(b"fake-image-bytes")
    assert artifact.name == "test.png"
    assert artifact.uri.startswith("file://")


@pytest.mark.asyncio
async def test_minio_artifact_store_fallback_store_writes_to_tmp() -> None:
    """_RPAArtifactStoreFallback writes bytes to /tmp and returns valid RPAArtifact."""
    from pathlib import Path

    from app.rpa.artifacts import _RPAArtifactStoreFallback

    store = _RPAArtifactStoreFallback()
    content = b"hello artifact"
    artifact = await store.write_bytes(
        goal_id="test-goal", name="output.txt", content=content
    )

    assert artifact.uri.startswith("file://")
    assert artifact.size_bytes == len(content)
    assert Path(artifact.path).read_bytes() == content


def test_get_artifact_store_returns_fallback_when_no_minio_env() -> None:
    """get_artifact_store returns _RPAArtifactStoreFallback when MINIO_ENDPOINT is unset."""
    from app.rpa.artifacts import _RPAArtifactStoreFallback, get_artifact_store

    with patch.dict("os.environ", {}, clear=False):
        # Ensure MINIO_ENDPOINT is not set
        import os

        os.environ.pop("MINIO_ENDPOINT", None)
        store = get_artifact_store()
    assert isinstance(store, _RPAArtifactStoreFallback)


def test_get_artifact_store_returns_minio_when_endpoint_set() -> None:
    """get_artifact_store returns MinIOArtifactStore when MINIO_ENDPOINT is configured."""
    from app.rpa.artifacts import MinIOArtifactStore, get_artifact_store

    with patch.dict("os.environ", {"MINIO_ENDPOINT": "http://minio:9000"}):
        store = get_artifact_store()
    assert isinstance(store, MinIOArtifactStore)
