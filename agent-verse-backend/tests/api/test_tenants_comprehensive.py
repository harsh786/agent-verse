"""Comprehensive tests for /tenants API endpoints — targets 41% → 70%+ coverage."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.core.errors import ConflictError, NotFoundError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-tenants", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_test_tenants_comp"


def _make_app(tenant_service: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(tenants_router)
    app.state.tenant_service = tenant_service or AsyncMock()
    return app


def _default_svc() -> Any:
    svc = AsyncMock()
    svc.create_tenant.return_value = {
        "tenant_id": "tid-new",
        "name": "Acme Corp",
        "api_key": "av_free_abc123",
    }
    svc.get_tenant.return_value = {
        "tenant_id": _CTX.tenant_id,
        "name": "Acme Corp",
        "plan": "professional",
    }
    svc.list_api_keys.return_value = [{"key_id": "kid-1", "name": "default"}]
    svc.create_api_key.return_value = {
        "key_id": "kid-new",
        "raw_key": "av_pro_xyz789",
        "name": "new-key",
    }
    svc.revoke_api_key.return_value = None
    return svc


# ---------------------------------------------------------------------------
# signup
# ---------------------------------------------------------------------------

def test_signup_success() -> None:
    svc = _default_svc()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/signup",
        json={"name": "Acme Corp", "email": "admin@acme.com"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "api_key" in body or "tenant_id" in body


def test_signup_conflict() -> None:
    svc = AsyncMock()
    svc.create_tenant.side_effect = ConflictError("Email already registered")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/signup",
        json={"name": "Acme", "email": "admin@acme.com"},
    )
    assert resp.status_code == 409


def test_signup_invalid_email() -> None:
    svc = _default_svc()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/signup",
        json={"name": "Test", "email": "not-an-email"},
    )
    assert resp.status_code == 422


def test_signup_empty_name() -> None:
    svc = _default_svc()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/signup",
        json={"name": "", "email": "test@test.com"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /tenants/me
# ---------------------------------------------------------------------------

def test_get_me_success() -> None:
    svc = _default_svc()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/tenants/me", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == _CTX.tenant_id


def test_get_me_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /tenants/me/keys
# ---------------------------------------------------------------------------

def test_list_keys_success() -> None:
    svc = _default_svc()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/tenants/me/keys", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_list_keys_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me/keys")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /tenants/me/keys
# ---------------------------------------------------------------------------

def test_create_key_success() -> None:
    svc = _default_svc()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/keys",
        json={"name": "ci-key", "scopes": ["goals:submit"]},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "raw_key" in body or "key_id" in body


def test_create_key_empty_name_invalid() -> None:
    svc = _default_svc()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/keys",
        json={"name": ""},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


def test_create_key_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post("/tenants/me/keys", json={"name": "test"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /tenants/me/keys/{key_id}
# ---------------------------------------------------------------------------

def test_revoke_key_success() -> None:
    svc = _default_svc()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.delete("/tenants/me/keys/kid-1", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204


def test_revoke_key_not_found() -> None:
    svc = AsyncMock()
    svc.revoke_api_key.side_effect = NotFoundError("Key not found")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.delete("/tenants/me/keys/nonexistent", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


def test_revoke_key_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/tenants/me/keys/kid-1")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /tenants/me/keys/{key_id}/rotate
# ---------------------------------------------------------------------------

def test_rotate_key_success() -> None:
    svc = _default_svc()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/keys/kid-1/rotate",
        json={"name": "Rotated Key"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "new_key" in body
    assert body["old_key_id"] == "kid-1"


def test_rotate_key_no_revoke() -> None:
    svc = _default_svc()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/keys/kid-1/rotate",
        json={"name": "New Key", "revoke_old": False},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert resp.json()["old_revoked"] is False


# ---------------------------------------------------------------------------
# GET /tenants/me/llm
# ---------------------------------------------------------------------------

def test_get_llm_config_unconfigured() -> None:
    svc = _default_svc()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/tenants/me/llm", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json()["configured"] is False


def test_get_llm_config_configured() -> None:
    svc = _default_svc()
    app = _make_app(svc)
    app.state._llm_configs = {
        _CTX.tenant_id: {
            "provider": "anthropic",
            "default_model": "claude-opus-4-5",
            "masked_key": "sk-ant-abc...xyz",
        }
    }
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/tenants/me/llm", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["provider"] == "anthropic"
    assert "api_key" not in body
    assert "encrypted_key" not in body


# ---------------------------------------------------------------------------
# PUT /tenants/me/llm
# ---------------------------------------------------------------------------

def test_set_llm_config_success() -> None:
    svc = _default_svc()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.put(
        "/tenants/me/llm",
        json={
            "provider": "anthropic",
            "api_key": "sk-ant-test12345678",
            "default_model": "claude-opus-4-5",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "anthropic"
    assert body["configured"] is True


def test_set_llm_config_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.put(
        "/tenants/me/llm",
        json={"provider": "openai", "api_key": "sk-test123"},
    )
    assert resp.status_code == 401


def test_set_llm_config_missing_api_key() -> None:
    svc = _default_svc()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.put(
        "/tenants/me/llm",
        json={"provider": "openai"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /tenants/me/roles (no DB)
# ---------------------------------------------------------------------------

def test_list_roles_no_db() -> None:
    svc = _default_svc()
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.get("/tenants/me/roles", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert resp.json() == []
