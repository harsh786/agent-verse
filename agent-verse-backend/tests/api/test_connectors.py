"""Tests for /connectors API endpoints."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware
from app.api.connectors import router as connectors_router

_CTX = TenantContext(tenant_id="tid-test", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_professional_testkey"


def _make_app(fake_registry: Any) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(connectors_router)
    app.state.mcp_registry = fake_registry
    return app


def test_register_connector_returns_201() -> None:
    reg = AsyncMock()
    reg.register.return_value = "srv-abc"
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.post(
        "/connectors",
        json={
            "name": "github",
            "url": "http://localhost:9000",
            "auth_type": "bearer",
            "auth_config": {"token": "ghp_xxx"},
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert resp.json()["server_id"] == "srv-abc"


def test_list_connectors_returns_200() -> None:
    reg = AsyncMock()
    reg.list_servers.return_value = []
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.get("/connectors", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_unregister_connector_returns_204() -> None:
    reg = AsyncMock()
    reg.unregister.return_value = True
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.delete("/connectors/srv-abc", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204


def test_unregister_missing_connector_returns_404() -> None:
    reg = AsyncMock()
    reg.unregister.return_value = False
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.delete("/connectors/ghost", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


def test_get_catalog_returns_all_connectors() -> None:
    reg = AsyncMock()
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.get("/connectors/catalog", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    names = {c["name"] for c in resp.json()}
    assert "github" in names
    assert "slack" in names


def test_register_connector_requires_auth() -> None:
    reg = AsyncMock()
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.post(
        "/connectors",
        json={"name": "x", "url": "http://localhost", "auth_type": "bearer", "auth_config": {}},
    )
    assert resp.status_code == 401
