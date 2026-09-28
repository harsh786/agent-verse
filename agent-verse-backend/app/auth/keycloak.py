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
    import os as _os

    return _os.environ.get("SSO_ENABLED", "false").lower() in ("true", "1", "yes")


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
    """Deprecated: the plan tier is NOT derived from IdP roles any more.

    Mapping ``admin`` → enterprise let anyone holding a Keycloak realm role
    self-grant a paid tier. The plan comes from the tenant record (billing).
    Always returns ``"free"``, the tier a JIT-provisioned tenant starts on.
    """
    del roles
    return "free"


def map_realm_roles(roles: list[str]) -> tuple[str, ...]:
    """Map Keycloak realm roles to AgentVerse RBAC roles (least privilege).

    Only the known RBAC roles pass through; a user with none of them is a
    ``viewer``. The SSO TenantContext used to carry NO roles, so every write
    was 403 (unless the legacy allow-all flag was on, which allowed everything).
    """
    from app.tenancy.rbac import VALID_ROLES

    mapped = tuple(sorted({r for r in roles if r in VALID_ROLES}))
    return mapped or ("viewer",)


async def resolve_tenant_from_jwt(token: str, tenant_service: Any) -> Any | None:
    """Validate JWT and resolve/create a TenantContext from the claims.

    Maps Keycloak users to AgentVerse tenants by SSO subject (JIT-provisioning
    on first login). Returns ``None`` for an invalid token; a tenant-store error
    propagates (the middleware treats it as unauthenticated).
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
    roles = map_realm_roles(extract_roles(payload))

    if not sub:
        return None

    tenant = await _get_or_provision_tenant(
        sub=sub, email=email, name=name, tenant_service=tenant_service
    )
    if tenant is None:
        return None

    try:
        plan = PlanTier(str(tenant.get("plan") or "free"))
    except ValueError:
        plan = PlanTier.FREE

    # Look up the real DB key record so api_key_id is a genuine persisted key,
    # not the ephemeral ghost "sso:{sub[:16]}" string.
    real_key_id = str(tenant.get("api_key_id") or f"sso:{sub[:16]}")
    try:
        key_record = await tenant_service.get_key_by_sso_sub(sso_sub=sub)
        if key_record and key_record.get("key_id"):
            real_key_id = key_record["key_id"]
    except Exception as exc:
        logger.debug("sso_key_lookup_failed", error=str(exc))

    return TenantContext(
        tenant_id=str(tenant["tenant_id"]),
        plan=plan,
        api_key_id=real_key_id,
        roles=roles,
    )


async def _get_or_provision_tenant(
    sub: str, email: str, name: str, tenant_service: Any
) -> dict[str, Any] | None:
    """Get the tenant owned by this SSO subject, or JIT-provision one.

    A lookup error propagates: treating it as "not found" provisioned a second
    tenant for an existing user on any DB blip. An e-mail that already belongs
    to a (non-SSO) tenant is refused — never linked by e-mail.
    """
    from app.core.errors import ConflictError

    existing = await tenant_service.get_tenant_by_sso_sub(sso_sub=sub)
    if existing:
        return dict(existing)
    try:
        new_tenant = await tenant_service.create_tenant_from_sso(
            sso_sub=sub, email=email, name=name or email
        )
    except ConflictError:
        logger.warning("sso_tenant_email_conflict", sub=sub[:16])
        return None
    logger.info("sso_tenant_provisioned", tenant_id=new_tenant.get("tenant_id"))
    return dict(new_tenant)
