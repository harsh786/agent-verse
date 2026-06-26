"""Tests for API key rotation endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-rotate", plan=PlanTier.STARTER, api_key_id="kid-old")
_VALID_KEY = "ak_test_rotate"


def _make_app(fake_service: AsyncMock) -> FastAPI:
    """Minimal app with tenants router and in-memory auth."""
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(tenants_router)
    app.state.tenant_service = fake_service
    return app


# ── rotate_key ────────────────────────────────────────────────────────────────

def test_rotate_key_creates_new_revokes_old() -> None:
    """POST /tenants/me/keys/{id}/rotate returns 201, new key data, and revokes old."""
    svc = AsyncMock()
    svc.create_api_key.return_value = {
        "key_id": "kid-new",
        "raw_key": "av_starter_newkey",
        "name": "Rotated Key",
        "scopes": [],
        "is_active": True,
        "expires_at": None,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/keys/kid-old/rotate",
        json={"revoke_old": True},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "new_key" in data
    assert data["old_key_id"] == "kid-old"
    assert data["old_revoked"] is True
    svc.create_api_key.assert_called_once()
    svc.revoke_api_key.assert_called_once()


def test_rotate_key_without_revoke_keeps_old() -> None:
    """When revoke_old=False the old key is NOT revoked."""
    svc = AsyncMock()
    svc.create_api_key.return_value = {
        "key_id": "kid-new2",
        "raw_key": "av_starter_key2",
        "name": "Rotated Key",
        "scopes": [],
        "is_active": True,
        "expires_at": None,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/keys/kid-old/rotate",
        json={"revoke_old": False},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["old_revoked"] is False
    # revoke_api_key should NOT have been called
    svc.revoke_api_key.assert_not_called()


def test_rotate_key_default_revoke_is_true() -> None:
    """Omitting revoke_old defaults to True."""
    svc = AsyncMock()
    svc.create_api_key.return_value = {
        "key_id": "kid-new3",
        "raw_key": "av_starter_key3",
        "name": "Rotated Key",
        "scopes": [],
        "is_active": True,
        "expires_at": None,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/keys/kid-old/rotate",
        json={},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert resp.json()["old_revoked"] is True
    svc.revoke_api_key.assert_called_once()


def test_rotate_key_requires_auth() -> None:
    """Missing API key returns 401."""
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/tenants/me/keys/kid-old/rotate", json={})
    assert resp.status_code == 401


# ── HSTS header ───────────────────────────────────────────────────────────────

def test_hsts_header_present() -> None:
    """Every authenticated response must carry the Strict-Transport-Security header."""
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/tenants/me/llm", headers={"X-API-Key": _VALID_KEY})
    lower_headers = {k.lower() for k in resp.headers}
    assert "strict-transport-security" in lower_headers
    assert "max-age=63072000" in resp.headers.get("strict-transport-security", "").lower()


def test_hsts_header_present_on_public_endpoint() -> None:
    """HSTS is added by SecurityHeadersMiddleware even on public (bypassed) paths."""
    svc = AsyncMock()
    app = _make_app(svc)

    from fastapi import Request

    @app.get("/tenants/signup/ping")
    async def _public(_: Request) -> dict[str, str]:
        return {"ok": "true"}

    client = TestClient(app, raise_server_exceptions=False)
    # /tenants/signup is bypassed by TenantMiddleware but SecurityHeadersMiddleware still runs.
    resp = client.get("/tenants/signup/ping")
    assert "strict-transport-security" in {k.lower() for k in resp.headers}
