"""Comprehensive tests for app/auth/keycloak.py — OIDC/SSO integration."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# URL builders (require settings mock)
# ---------------------------------------------------------------------------

_SETTINGS = MagicMock()
_SETTINGS.keycloak_url = "https://sso.example.com"
_SETTINGS.keycloak_realm = "agentverse"
_SETTINGS.keycloak_client_id = "agentverse-app"


def _patch_settings():
    return patch("app.core.config.get_settings", return_value=_SETTINGS)


def test_jwks_uri():
    with _patch_settings():
        from app.auth.keycloak import jwks_uri
        uri = jwks_uri()
    assert uri == "https://sso.example.com/realms/agentverse/protocol/openid-connect/certs"


def test_token_endpoint():
    with _patch_settings():
        from app.auth.keycloak import token_endpoint
        url = token_endpoint()
    assert url == "https://sso.example.com/realms/agentverse/protocol/openid-connect/token"


def test_authorization_endpoint():
    with _patch_settings():
        from app.auth.keycloak import authorization_endpoint
        url = authorization_endpoint()
    assert "auth" in url
    assert "agentverse" in url


def test_userinfo_endpoint():
    with _patch_settings():
        from app.auth.keycloak import userinfo_endpoint
        url = userinfo_endpoint()
    assert "userinfo" in url


# ---------------------------------------------------------------------------
# _sso_enabled
# ---------------------------------------------------------------------------


def test_sso_enabled_false_by_default():
    from app.auth.keycloak import _sso_enabled
    with patch.dict("os.environ", {"SSO_ENABLED": "false"}):
        assert _sso_enabled() is False


def test_sso_enabled_true_when_env_set():
    from app.auth.keycloak import _sso_enabled
    with patch.dict("os.environ", {"SSO_ENABLED": "true"}):
        assert _sso_enabled() is True


def test_sso_enabled_with_various_truthy_values():
    from app.auth.keycloak import _sso_enabled
    for val in ("1", "yes", "true"):
        with patch.dict("os.environ", {"SSO_ENABLED": val}):
            assert _sso_enabled() is True


def test_sso_enabled_false_with_false_values():
    from app.auth.keycloak import _sso_enabled
    for val in ("false", "0", "no", ""):
        with patch.dict("os.environ", {"SSO_ENABLED": val}):
            assert _sso_enabled() is False


# ---------------------------------------------------------------------------
# get_jwks — caching
# ---------------------------------------------------------------------------


async def test_get_jwks_fetches_and_caches():
    mock_jwks = {"keys": [{"kty": "RSA", "kid": "key1"}]}
    mock_resp = MagicMock()
    mock_resp.json.return_value = mock_jwks
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    import app.auth.keycloak as kc
    # Reset cache state
    kc._jwks_cache = {}
    kc._jwks_fetched_at = 0.0

    with (
        _patch_settings(),
        patch("app.auth.keycloak.httpx.AsyncClient", return_value=mock_client),
    ):
        result = await kc.get_jwks()

    assert result == mock_jwks
    assert kc._jwks_cache == mock_jwks


async def test_get_jwks_uses_cache_if_fresh():
    import app.auth.keycloak as kc
    cached_jwks = {"keys": [{"kty": "RSA", "kid": "cached-key"}]}
    kc._jwks_cache = cached_jwks
    kc._jwks_fetched_at = time.monotonic()  # just now

    with (
        _patch_settings(),
        patch("app.auth.keycloak.httpx.AsyncClient") as mock_http,
    ):
        result = await kc.get_jwks()

    # No HTTP call should be made
    mock_http.assert_not_called()
    assert result == cached_jwks


async def test_get_jwks_returns_stale_cache_on_fetch_failure():
    import app.auth.keycloak as kc
    stale_jwks = {"keys": [{"kty": "RSA", "kid": "stale-key"}]}
    kc._jwks_cache = stale_jwks
    kc._jwks_fetched_at = 0.0  # Expired

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=Exception("network error"))

    with (
        _patch_settings(),
        patch("app.auth.keycloak.httpx.AsyncClient", return_value=mock_client),
    ):
        result = await kc.get_jwks()

    # Returns stale cache
    assert result == stale_jwks


async def test_get_jwks_raises_when_no_cache_and_fetch_fails():
    import app.auth.keycloak as kc
    kc._jwks_cache = {}
    kc._jwks_fetched_at = 0.0

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=Exception("network error"))

    with (
        _patch_settings(),
        patch("app.auth.keycloak.httpx.AsyncClient", return_value=mock_client),
        pytest.raises(Exception),
    ):
        await kc.get_jwks()


# ---------------------------------------------------------------------------
# extract_roles
# ---------------------------------------------------------------------------


def test_extract_roles_from_realm_access():
    from app.auth.keycloak import extract_roles
    payload = {"realm_access": {"roles": ["admin", "operator"]}}
    assert extract_roles(payload) == ["admin", "operator"]


def test_extract_roles_empty_payload():
    from app.auth.keycloak import extract_roles
    assert extract_roles({}) == []


def test_extract_roles_missing_roles_key():
    from app.auth.keycloak import extract_roles
    assert extract_roles({"realm_access": {}}) == []


# ---------------------------------------------------------------------------
# map_roles_to_plan
# ---------------------------------------------------------------------------


def test_map_roles_to_plan_admin():
    from app.auth.keycloak import map_roles_to_plan
    assert map_roles_to_plan(["admin"]) == "enterprise"


def test_map_roles_to_plan_operator():
    from app.auth.keycloak import map_roles_to_plan
    assert map_roles_to_plan(["operator"]) == "professional"


def test_map_roles_to_plan_viewer():
    from app.auth.keycloak import map_roles_to_plan
    assert map_roles_to_plan(["viewer"]) == "starter"


def test_map_roles_to_plan_unknown_defaults_to_free():
    from app.auth.keycloak import map_roles_to_plan
    assert map_roles_to_plan([]) == "free"
    assert map_roles_to_plan(["some_other_role"]) == "free"


def test_map_roles_to_plan_admin_takes_precedence():
    from app.auth.keycloak import map_roles_to_plan
    assert map_roles_to_plan(["admin", "viewer"]) == "enterprise"


# ---------------------------------------------------------------------------
# validate_jwt
# ---------------------------------------------------------------------------


async def test_validate_jwt_success():
    from app.auth.keycloak import validate_jwt
    mock_payload = {
        "sub": "user-sub-123",
        "email": "user@example.com",
        "realm_access": {"roles": ["admin"]},
    }

    with (
        _patch_settings(),
        patch("app.auth.keycloak.get_jwks", AsyncMock(return_value={"keys": []})),
        patch("jose.jwt.decode", return_value=mock_payload),
    ):
        payload = await validate_jwt("valid.jwt.token")

    assert payload["sub"] == "user-sub-123"


async def test_validate_jwt_expired_raises_value_error():
    from jose import ExpiredSignatureError
    from app.auth.keycloak import validate_jwt

    with (
        _patch_settings(),
        patch("app.auth.keycloak.get_jwks", AsyncMock(return_value={"keys": []})),
        patch("jose.jwt.decode", side_effect=ExpiredSignatureError("expired")),
    ):
        with pytest.raises(ValueError, match="expired"):
            await validate_jwt("expired.jwt.token")


async def test_validate_jwt_invalid_raises_value_error():
    from jose import JWTError
    from app.auth.keycloak import validate_jwt

    with (
        _patch_settings(),
        patch("app.auth.keycloak.get_jwks", AsyncMock(return_value={"keys": []})),
        patch("jose.jwt.decode", side_effect=JWTError("bad signature")),
    ):
        with pytest.raises(ValueError, match="Invalid SSO token"):
            await validate_jwt("bad.jwt.token")


# ---------------------------------------------------------------------------
# _get_or_provision_tenant
# ---------------------------------------------------------------------------


async def test_get_or_provision_tenant_finds_existing():
    from app.auth.keycloak import _get_or_provision_tenant

    tenant_svc = AsyncMock()
    tenant_svc.get_tenant_by_sso_sub = AsyncMock(
        return_value={"tenant_id": "existing-tenant-id"}
    )

    result = await _get_or_provision_tenant(
        sub="sub-123",
        email="user@corp.com",
        name="User",
        plan="enterprise",
        tenant_service=tenant_svc,
    )

    assert result == "existing-tenant-id"
    tenant_svc.create_tenant_from_sso.assert_not_called()


async def test_get_or_provision_tenant_creates_new_on_first_login():
    from app.auth.keycloak import _get_or_provision_tenant

    tenant_svc = AsyncMock()
    tenant_svc.get_tenant_by_sso_sub = AsyncMock(return_value=None)
    tenant_svc.create_tenant_from_sso = AsyncMock(
        return_value={"tenant_id": "new-tenant-id"}
    )

    result = await _get_or_provision_tenant(
        sub="new-sub",
        email="new@corp.com",
        name="New User",
        plan="free",
        tenant_service=tenant_svc,
    )

    assert result == "new-tenant-id"
    tenant_svc.create_tenant_from_sso.assert_called_once()


async def test_get_or_provision_tenant_returns_none_on_error():
    from app.auth.keycloak import _get_or_provision_tenant

    tenant_svc = AsyncMock()
    tenant_svc.get_tenant_by_sso_sub = AsyncMock(side_effect=Exception("DB down"))

    result = await _get_or_provision_tenant(
        sub="sub-err",
        email="err@corp.com",
        name="Err",
        plan="free",
        tenant_service=tenant_svc,
    )

    assert result is None


# ---------------------------------------------------------------------------
# resolve_tenant_from_jwt
# ---------------------------------------------------------------------------


async def test_resolve_tenant_from_jwt_success():
    from app.auth.keycloak import resolve_tenant_from_jwt

    mock_payload = {
        "sub": "user-abc",
        "email": "user@corp.com",
        "name": "User ABC",
        "realm_access": {"roles": ["operator"]},
    }

    tenant_svc = AsyncMock()
    tenant_svc.get_tenant_by_sso_sub = AsyncMock(return_value={"tenant_id": "t99"})
    tenant_svc.get_key_by_sso_sub = AsyncMock(return_value={"key_id": "key-real-99"})

    with (
        _patch_settings(),
        patch("app.auth.keycloak.validate_jwt", AsyncMock(return_value=mock_payload)),
    ):
        ctx = await resolve_tenant_from_jwt("valid.token", tenant_svc)

    assert ctx is not None
    assert str(ctx.tenant_id) == "t99"
    assert str(ctx.api_key_id) == "key-real-99"


async def test_resolve_tenant_from_jwt_invalid_token_returns_none():
    from app.auth.keycloak import resolve_tenant_from_jwt

    tenant_svc = AsyncMock()

    with (
        _patch_settings(),
        patch("app.auth.keycloak.validate_jwt", AsyncMock(side_effect=ValueError("bad"))),
    ):
        ctx = await resolve_tenant_from_jwt("bad.token", tenant_svc)

    assert ctx is None


async def test_resolve_tenant_from_jwt_no_sub_returns_none():
    from app.auth.keycloak import resolve_tenant_from_jwt

    mock_payload = {"email": "user@corp.com", "realm_access": {"roles": []}}

    tenant_svc = AsyncMock()

    with (
        _patch_settings(),
        patch("app.auth.keycloak.validate_jwt", AsyncMock(return_value=mock_payload)),
    ):
        ctx = await resolve_tenant_from_jwt("token.no.sub", tenant_svc)

    assert ctx is None


async def test_resolve_tenant_from_jwt_uses_sso_fallback_key_id():
    """When get_key_by_sso_sub fails, falls back to 'sso:{sub[:16]}' format."""
    from app.auth.keycloak import resolve_tenant_from_jwt

    mock_payload = {
        "sub": "user-fallback-sub",
        "email": "fallback@corp.com",
        "realm_access": {"roles": ["viewer"]},
    }

    tenant_svc = AsyncMock()
    tenant_svc.get_tenant_by_sso_sub = AsyncMock(return_value={"tenant_id": "t-fb"})
    tenant_svc.get_key_by_sso_sub = AsyncMock(side_effect=Exception("key lookup failed"))

    with (
        _patch_settings(),
        patch("app.auth.keycloak.validate_jwt", AsyncMock(return_value=mock_payload)),
    ):
        ctx = await resolve_tenant_from_jwt("fb.token", tenant_svc)

    assert ctx is not None
    assert ctx.api_key_id.startswith("sso:")
