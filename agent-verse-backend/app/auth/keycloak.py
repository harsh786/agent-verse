"""Keycloak OIDC integration for AgentVerse SSO.

Uses python-jose for JWT validation (open-source, no Keycloak SDK required).
Validates JWT tokens issued by Keycloak without making a network call per request
(uses cached public keys — JWKS).

Flow:
1. Frontend redirects user to Keycloak login page
2. Keycloak issues an access token (JWT) after successful login
3. Frontend sends JWT as `Authorization: Bearer <jwt>` header
4. This middleware validates the JWT signature using Keycloak's public keys
5. Claims (sub, email, realm_access.roles) are extracted and mapped to TenantContext

Open-source deps only: python-jose, httpx (already in requirements)
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Cache JWKS for up to 1 hour to avoid hammering Keycloak
_jwks_cache: dict[str, Any] = {}
_jwks_cache_ttl = 3600.0
_jwks_fetched_at = 0.0


def _keycloak_url() -> str:
    from app.core.config import get_settings
    return get_settings().keycloak_url


def _realm() -> str:
    from app.core.config import get_settings
    return get_settings().keycloak_realm


def _client_id() -> str:
    from app.core.config import get_settings
    return get_settings().keycloak_client_id


def _sso_enabled() -> bool:
    from app.core.config import get_settings
    return get_settings().sso_enabled


def jwks_uri() -> str:
    return f"{_keycloak_url()}/realms/{_realm()}/protocol/openid-connect/certs"


def token_endpoint() -> str:
    return f"{_keycloak_url()}/realms/{_realm()}/protocol/openid-connect/token"


def authorization_endpoint() -> str:
    return f"{_keycloak_url()}/realms/{_realm()}/protocol/openid-connect/auth"


def userinfo_endpoint() -> str:
    return f"{_keycloak_url()}/realms/{_realm()}/protocol/openid-connect/userinfo"


async def get_jwks() -> dict[str, Any]:
    """Fetch Keycloak's public keys (JWKS) with caching."""
    global _jwks_cache, _jwks_fetched_at

    now = time.monotonic()
    if _jwks_cache and (now - _jwks_fetched_at) < _jwks_cache_ttl:
        return _jwks_cache

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(jwks_uri())
            resp.raise_for_status()
            _jwks_cache = resp.json()
            _jwks_fetched_at = now
            logger.info("keycloak_jwks_refreshed", uri=jwks_uri())
            return _jwks_cache
    except Exception as exc:
        logger.warning("keycloak_jwks_fetch_failed", error=str(exc))
        if _jwks_cache:
            return _jwks_cache  # Return stale cache on failure
        raise


async def validate_jwt(token: str) -> dict[str, Any]:
    """Validate a Keycloak-issued JWT and return the claims payload.

    Raises:
        ValueError: If token is invalid, expired, or from wrong issuer
    """
    try:
        from jose import ExpiredSignatureError, JWTError
        from jose import jwt as _jwt
    except ImportError as exc:
        raise ImportError(
            "python-jose required for SSO: pip install 'python-jose[cryptography]'"
        ) from exc

    jwks = await get_jwks()
    issuer = f"{_keycloak_url()}/realms/{_realm()}"

    try:
        # python-jose handles JWKS key lookup and RS256 validation automatically
        payload: dict[str, Any] = _jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=_client_id(),
            issuer=issuer,
        )
        return payload
    except ExpiredSignatureError as exc:
        raise ValueError("SSO token has expired. Please log in again.") from exc
    except JWTError as exc:
        raise ValueError(f"Invalid SSO token: {exc}") from exc


def extract_roles(payload: dict[str, Any]) -> list[str]:
    """Extract realm-level roles from Keycloak JWT claims."""
    realm_access = payload.get("realm_access", {})
    return realm_access.get("roles", [])


def map_roles_to_plan(roles: list[str]) -> str:
    """Map Keycloak roles to AgentVerse plan tiers."""
    if "admin" in roles:
        return "enterprise"
    if "operator" in roles:
        return "professional"
    if "viewer" in roles:
        return "starter"
    return "free"


async def resolve_tenant_from_jwt(
    token: str, tenant_service: Any
) -> Any | None:
    """Validate JWT and resolve/create a TenantContext from the claims.

    Maps Keycloak users to AgentVerse tenants by email.
    Creates a new tenant record on first login (JIT provisioning).
    """
    from app.tenancy.context import PlanTier, TenantContext

    try:
        payload = await validate_jwt(token)
    except ValueError as exc:
        logger.warning("jwt_validation_failed", error=str(exc))
        return None

    sub: str = payload.get("sub", "")
    email: str = payload.get("email", "") or payload.get("preferred_username", sub)
    name: str = payload.get("name", "") or email.split("@")[0]
    roles = extract_roles(payload)
    plan_str = map_roles_to_plan(roles)

    if not sub:
        return None

    # Look up or provision tenant from email/sub
    tenant_id = await _get_or_provision_tenant(
        sub=sub, email=email, name=name, plan=plan_str,
        tenant_service=tenant_service
    )

    if not tenant_id:
        return None

    try:
        plan = PlanTier(plan_str)
    except ValueError:
        plan = PlanTier.FREE

    return TenantContext(
        tenant_id=tenant_id,
        plan=plan,
        api_key_id=f"sso:{sub[:16]}",
    )


async def _get_or_provision_tenant(
    sub: str, email: str, name: str, plan: str, tenant_service: Any
) -> str | None:
    """Get existing tenant by SSO subject, or create one (JIT provisioning)."""
    try:
        # Try to find existing tenant by sso_sub
        existing = await tenant_service.get_tenant_by_sso_sub(sso_sub=sub)
        if existing:
            return str(existing["tenant_id"])

        # JIT provision: create tenant for this SSO user on first login
        new_tenant = await tenant_service.create_tenant_from_sso(
            sso_sub=sub, email=email, name=name or email, plan=plan
        )
        if new_tenant:
            logger.info("sso_tenant_provisioned", email=email, plan=plan)
            return str(new_tenant["tenant_id"])
    except Exception as exc:
        logger.warning("sso_tenant_lookup_failed", error=str(exc), sub=sub[:16])
    return None
