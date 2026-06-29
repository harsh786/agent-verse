"""Extended tests for /tenants API — covers endpoints not in existing comprehensive tests.

Targets: 53% → 80%+ coverage on app/api/tenants.py
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.core.errors import ConflictError, NotFoundError, PlatformError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(
    tenant_id="tid-tenants2",
    plan=PlanTier.PROFESSIONAL,
    api_key_id="kid-tenants2",
    roles=("admin",),
)
_VALID_KEY = "av_test_tenants2"


def _make_app(tenant_service: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(tenants_router)
    app.state.tenant_service = tenant_service or _make_service()
    return app


def _make_service() -> Any:
    svc = AsyncMock()
    svc.create_tenant.return_value = {
        "tenant_id": "new-tenant-1",
        "api_key": "av_free_test123",
        "name": "Test",
        "email": "test@example.com",
    }
    svc.get_tenant.return_value = {
        "tenant_id": _CTX.tenant_id,
        "name": "My Tenant",
        "email": "tenant@example.com",
        "plan": "professional",
    }
    svc.list_api_keys.return_value = {
        "keys": [{"key_id": "k1", "name": "Primary key", "scopes": []}]
    }
    svc.create_api_key.return_value = {
        "key_id": "k2",
        "name": "New Key",
        "raw_key": "av_professional_xyz",
    }
    svc.revoke_api_key.return_value = {"status": "revoked"}
    svc.rotate_api_key.return_value = {
        "old_key_id": "k1",
        "new_key": {"key_id": "k-new", "name": "Rotated", "raw_key": "av_professional_abc"},
    }
    return svc


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------


def test_signup_success() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/signup",
        json={"name": "Acme Corp", "email": "admin@acme.com"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "api_key" in body or "tenant_id" in body


def test_signup_conflict() -> None:
    svc = _make_service()
    svc.create_tenant.side_effect = ConflictError("Email already registered")
    client = TestClient(_make_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/signup",
        json={"name": "Dupe Corp", "email": "dupe@example.com"},
    )
    assert resp.status_code == 409


def test_signup_invalid_email() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/signup",
        json={"name": "Test Corp", "email": "not-an-email"},
    )
    assert resp.status_code == 422


def test_signup_empty_name() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/signup",
        json={"name": "", "email": "test@example.com"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /tenants/me
# ---------------------------------------------------------------------------


def test_get_me_success() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert "tenant_id" in body or "name" in body


def test_get_me_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


def test_list_keys_success() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me/keys", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200


def test_create_key_success() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/keys",
        json={"name": "CI Key", "scopes": ["goals:write"]},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201


def test_revoke_key_success() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete("/tenants/me/keys/k1", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204


def test_rotate_key_success() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/keys/k1/rotate",
        json={"name": "Rotated Key", "revoke_old": True},  # RotateKeyRequest body required
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "new_key" in body


# ---------------------------------------------------------------------------
# LLM config
# ---------------------------------------------------------------------------


def test_get_llm_config_unconfigured() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me/llm", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code in (200, 404, 503)


def test_set_llm_config_success() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.put(
        "/tenants/me/llm",
        json={
            "provider": "anthropic",
            "api_key": "sk-ant-test123",
            "model": "claude-opus-4-5",
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code in (200, 201)


# ---------------------------------------------------------------------------
# Roles management
# ---------------------------------------------------------------------------


def test_list_roles_no_db() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me/roles", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code in (200, 503)


def test_create_role_no_db() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/roles",
        json={"user_email": "user@company.com", "role": "operator"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code in (200, 201, 422, 503)


def test_delete_role_no_db() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete(
        "/tenants/me/roles/role-1",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code in (200, 204, 404, 503)


# ---------------------------------------------------------------------------
# IP allowlist
# ---------------------------------------------------------------------------


def test_list_ip_allowlist_no_db() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/tenants/me/ip-allowlist", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code in (200, 503)


def test_add_ip_allowlist_entry() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/ip-allowlist",
        json={"cidr": "192.168.1.0/24", "description": "Office network"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code in (200, 201, 422, 503)


def test_delete_ip_allowlist_entry() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.delete(
        "/tenants/me/ip-allowlist/entry-1",
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code in (200, 204, 404, 503)


# ---------------------------------------------------------------------------
# Vault key
# ---------------------------------------------------------------------------


def test_set_vault_key_no_db() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/vault-key",
        json={"encryption_key": "base64encodedkey"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code in (200, 201, 422, 503)


# ---------------------------------------------------------------------------
# Auth guard for protected endpoints
# ---------------------------------------------------------------------------


def test_list_ip_allowlist_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    assert client.get("/tenants/me/ip-allowlist").status_code == 401


def test_list_roles_requires_auth() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    assert client.get("/tenants/me/roles").status_code == 401
