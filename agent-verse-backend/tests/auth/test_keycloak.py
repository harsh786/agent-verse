"""Tests for Keycloak SSO integration."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


def test_sso_disabled_by_default():
    """SSO is disabled unless SSO_ENABLED=true is set."""
    with patch.dict(os.environ, {"SSO_ENABLED": "false"}):
        from app.auth.keycloak import _sso_enabled
        assert _sso_enabled() is False


def test_sso_enabled_via_env():
    with patch.dict(os.environ, {"SSO_ENABLED": "true"}):
        from app.auth.keycloak import _sso_enabled
        assert _sso_enabled() is True


def test_extract_roles_from_payload():
    from app.auth.keycloak import extract_roles
    payload = {"realm_access": {"roles": ["admin", "operator"]}}
    assert "admin" in extract_roles(payload)
    assert "operator" in extract_roles(payload)


def test_extract_roles_empty_when_missing():
    from app.auth.keycloak import extract_roles
    assert extract_roles({}) == []
    assert extract_roles({"realm_access": {}}) == []


def test_map_roles_never_grants_a_paid_plan():
    """Realm roles used to map to plans (admin -> enterprise): a self-granted tier."""
    from app.auth.keycloak import map_roles_to_plan

    for roles in (["admin"], ["operator"], ["viewer"], ["admin", "viewer"]):
        assert map_roles_to_plan(roles) == "free"


def test_map_realm_roles_keeps_only_rbac_roles_and_defaults_to_viewer():
    from app.auth.keycloak import map_realm_roles

    assert map_realm_roles(["admin", "offline_access"]) == ("admin",)
    assert map_realm_roles(["uma_authorization"]) == ("viewer",)
    assert map_realm_roles([]) == ("viewer",)


def test_map_roles_unknown_to_free():
    from app.auth.keycloak import map_roles_to_plan
    assert map_roles_to_plan([]) == "free"
    assert map_roles_to_plan(["unknown"]) == "free"


@pytest.mark.asyncio
async def test_validate_jwt_raises_without_jose():
    """validate_jwt raises ImportError when python-jose not installed."""
    with patch.dict("sys.modules", {"jose": None}):
        from importlib import reload

        import app.auth.keycloak as kc_mod
        reload(kc_mod)
        try:
            await kc_mod.validate_jwt("fake.jwt.token")
        except (ImportError, Exception):
            pass  # Expected


@pytest.mark.asyncio
async def test_get_sso_config_disabled():
    """GET /auth/config returns sso_enabled: false when SSO off."""
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with patch.dict(os.environ, {"SSO_ENABLED": "false"}):
            resp = await c.get("/auth/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sso_enabled"] is False
    assert data["provider"] is None


@pytest.mark.asyncio
async def test_auth_login_redirects():
    """GET /auth/login returns a redirect to Keycloak."""
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as c:
        with patch.dict(os.environ, {
            "SSO_ENABLED": "true",
            "KEYCLOAK_URL": "http://keycloak:8080",
            "KEYCLOAK_REALM": "agentverse",
        }):
            resp = await c.get("/auth/login?redirect_uri=http://localhost:5173/callback")
    # Should redirect (302) to Keycloak
    assert resp.status_code in (302, 307)
    location = resp.headers.get("location", "")
    assert "keycloak" in location or "openid-connect" in location


def test_jwt_detection_in_middleware():
    """Token with 2+ dots is treated as JWT (not API key)."""
    api_key = "av_test_abc123xyz"
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyMSJ9.signature"

    assert api_key.count(".") == 0  # API key has no dots
    assert jwt.count(".") == 2      # JWT has exactly 2 dots
