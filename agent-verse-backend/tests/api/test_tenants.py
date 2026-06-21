"""Tests for /tenants endpoints (signup, me, keys CRUD)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import _hash_key
from app.api.tenants import router as tenants_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

# ── helpers ───────────────────────────────────────────────────────────────────

_CTX = TenantContext(tenant_id="tid-test", plan=PlanTier.STARTER, api_key_id="kid-1")
_VALID_KEY = "ak_test_abc123"


def _make_app(fake_service: Any) -> FastAPI:
    """Wire a minimal app with the tenants router + fake service."""

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(tenants_router)
    app.state.tenant_service = fake_service
    return app


# ── signup ────────────────────────────────────────────────────────────────────

def test_signup_returns_201_with_api_key() -> None:
    svc = AsyncMock()
    svc.create_tenant.return_value = {
        "tenant_id": "tid-new",
        "name": "Acme Corp",
        "email": "admin@acme.com",
        "plan": "free",
        "api_key": "ak_free_newkey",
        "api_key_id": "kid-new",
    }
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/tenants/signup", json={"name": "Acme Corp", "email": "admin@acme.com"})
    assert resp.status_code == 201
    body = resp.json()
    assert "api_key" in body
    assert "tenant_id" in body
    svc.create_tenant.assert_called_once()


def test_signup_missing_fields_returns_422() -> None:
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/tenants/signup", json={"name": "Acme"})  # missing email
    assert resp.status_code == 422


def test_signup_duplicate_email_returns_409() -> None:
    from app.core.errors import ConflictError

    svc = AsyncMock()
    svc.create_tenant.side_effect = ConflictError("Email already registered")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post("/tenants/signup", json={"name": "Dupe", "email": "dupe@example.com"})
    assert resp.status_code == 409


# ── me ─────────────────────────────────────────────────────────────────────────

def test_get_me_requires_auth() -> None:
    svc = AsyncMock()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/tenants/me")
    assert resp.status_code == 401


def test_get_me_returns_tenant_info() -> None:
    svc = AsyncMock()
    svc.get_tenant.return_value = {
        "id": "tid-test",
        "name": "Test Corp",
        "email": "test@test.com",
        "plan": "starter",
    }
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/tenants/me", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["id"] == "tid-test"


# ── API keys ──────────────────────────────────────────────────────────────────

def test_create_api_key_returns_201_with_raw_key() -> None:
    svc = AsyncMock()
    svc.create_api_key.return_value = {
        "id": "kid-new",
        "name": "CI Key",
        "api_key": "ak_starter_newraw",
        "scopes": [],
        "expires_at": None,
    }
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/keys",
        json={"name": "CI Key"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "api_key" in body  # raw key only returned at creation


def test_list_api_keys_hides_raw_key() -> None:
    svc = AsyncMock()
    svc.list_api_keys.return_value = [
        {"id": "kid-1", "name": "Main", "scopes": [], "expires_at": None, "is_active": True}
    ]
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/tenants/me/keys", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    keys = resp.json()
    assert isinstance(keys, list)
    for k in keys:
        assert "api_key" not in k  # raw key never in list response


def test_revoke_api_key_returns_204() -> None:
    svc = AsyncMock()
    svc.revoke_api_key.return_value = None
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.delete("/tenants/me/keys/kid-1", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204


def test_revoke_nonexistent_key_returns_404() -> None:
    from app.core.errors import NotFoundError

    svc = AsyncMock()
    svc.revoke_api_key.side_effect = NotFoundError("Key not found")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.delete("/tenants/me/keys/ghost", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


# ── utility ──────────────────────────────────────────────────────────────────

def test_hash_key_produces_64_char_hex() -> None:
    h = _hash_key("ak_test_something")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_key_is_deterministic() -> None:
    assert _hash_key("same") == _hash_key("same")


def test_hash_key_different_for_different_inputs() -> None:
    assert _hash_key("a") != _hash_key("b")
