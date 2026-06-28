"""SSO authentication endpoints for Keycloak integration.

Provides:
- GET /auth/login     — Redirect to Keycloak login page
- GET /auth/callback  — Handle OAuth2 authorization code callback
- GET /auth/userinfo  — Return current user info from JWT
- POST /auth/logout   — Invalidate Keycloak session
- GET /auth/config    — Return SSO configuration for frontend
"""
from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/auth", tags=["auth"])

async def _check_auth_rate_limit(request: Request) -> None:
    """Redis-backed sliding-window rate limiter for auth endpoints.

    Falls back to no-op when Redis is unavailable (preserves availability).
    10 requests per 60 seconds per client IP, enforced across all replicas.
    """
    client_ip: str = (
        request.client.host if request.client else "unknown"
    )
    redis = getattr(request.app.state, "_rate_limiter_redis", None)
    if redis is None:
        # No Redis wired yet (startup / test) — allow all requests
        return

    key = f"auth_rl:{client_ip}"
    now_ms = int(time.time() * 1000)
    window_ms = 60_000
    max_requests = 10

    try:
        pipe = redis.pipeline() if hasattr(redis, "pipeline") else None
        if pipe is not None:
            pipe.zremrangebyscore(key, 0, now_ms - window_ms)
            pipe.zadd(key, {str(now_ms): now_ms})
            pipe.zcard(key)
            pipe.expire(key, 120)
            results = await pipe.execute()
            count = results[2]
        else:
            await redis.zremrangebyscore(key, 0, now_ms - window_ms)
            await redis.zadd(key, {str(now_ms): now_ms})
            count = await redis.zcard(key)
            await redis.expire(key, 120)

        if count > max_requests:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=429,
                detail="Too many authentication requests. Please wait before trying again.",
                headers={"Retry-After": "60"},
            )
    except Exception as exc:
        # Import here to avoid circular
        from app.observability.logging import get_logger as _gl
        _gl(__name__).warning("auth_rate_limit_redis_error", error=str(exc))
        # On Redis error, allow the request (prefer availability over blocking)


def _default_redirect_uri() -> str:
    """Build the default OAuth redirect URI from the FRONTEND_URL env var."""
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
    return f"{frontend_url}/auth/callback"


@router.get("/config")
async def get_sso_config() -> dict[str, Any]:
    """Return SSO configuration so the frontend knows where to redirect."""
    from app.auth.keycloak import (
        _client_id,
        _keycloak_url,
        _realm,
        _sso_enabled,
        authorization_endpoint,
        token_endpoint,
    )
    enabled = _sso_enabled()
    return {
        "sso_enabled": enabled,
        "provider": "keycloak" if enabled else None,
        "keycloak_url": _keycloak_url() if enabled else None,
        "realm": _realm() if enabled else None,
        "client_id": _client_id() if enabled else None,
        "authorization_endpoint": authorization_endpoint() if enabled else None,
        "token_endpoint": token_endpoint() if enabled else None,
    }


@router.get("/login")
async def sso_login(
    redirect_uri: str = "",  # No default — require explicit or fall back to env var
    state: str = "",
) -> RedirectResponse:
    """Redirect to Keycloak login page."""
    if not redirect_uri:
        redirect_uri = _default_redirect_uri()
    import urllib.parse

    from app.auth.keycloak import _client_id, authorization_endpoint
    params = {
        "response_type": "code",
        "client_id": _client_id(),
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
    }
    url = f"{authorization_endpoint()}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=url)


@router.post("/token")
async def exchange_token(
    request: Request,
    code: str,
    redirect_uri: str = "",  # No default — require explicit or fall back to env var
) -> dict[str, Any]:
    """Exchange authorization code for access + refresh tokens."""
    await _check_auth_rate_limit(request)
    if not redirect_uri:
        redirect_uri = _default_redirect_uri()
    from app.auth.keycloak import _client_id, token_endpoint
    from app.core.config import get_settings

    _settings = get_settings()
    client_secret = _settings.keycloak_client_secret
    if not client_secret:
        if _settings.environment == "production":
            raise HTTPException(
                status_code=503,
                detail="SSO not configured: KEYCLOAK_CLIENT_SECRET is required in production",
            )
        client_secret = "agentverse-dev-secret"  # dev-only fallback

    import httpx
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            token_endpoint(),
            data={
                "grant_type": "authorization_code",
                "client_id": _client_id(),
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )

    if not resp.is_success:
        raise HTTPException(
            status_code=401,
            detail=f"Token exchange failed: {resp.text[:200]}"
        )

    data = resp.json()
    return {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expires_in": data.get("expires_in"),
        "token_type": "Bearer",
    }


@router.post("/refresh")
async def refresh_token(request: Request, refresh_token_value: str) -> dict[str, Any]:
    """Refresh an expired access token using a refresh token."""
    await _check_auth_rate_limit(request)
    from app.auth.keycloak import _client_id, token_endpoint
    from app.core.config import get_settings

    _settings = get_settings()
    client_secret = _settings.keycloak_client_secret
    if not client_secret:
        if _settings.environment == "production":
            raise HTTPException(
                status_code=503,
                detail="SSO not configured: KEYCLOAK_CLIENT_SECRET is required in production",
            )
        client_secret = "agentverse-dev-secret"  # dev-only fallback

    import httpx
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            token_endpoint(),
            data={
                "grant_type": "refresh_token",
                "client_id": _client_id(),
                "client_secret": client_secret,
                "refresh_token": refresh_token_value,
            },
        )

    if not resp.is_success:
        raise HTTPException(status_code=401, detail="Refresh token invalid or expired")

    data = resp.json()
    return {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expires_in": data.get("expires_in"),
        "token_type": "Bearer",
    }


@router.get("/userinfo")
async def get_userinfo(request: Request) -> dict[str, Any]:
    """Return current user information from validated JWT."""
    from app.auth.keycloak import _sso_enabled, extract_roles, validate_jwt

    if not _sso_enabled():
        raise HTTPException(400, "SSO not enabled")

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")

    token = auth[7:].strip()
    try:
        payload = await validate_jwt(token)
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc

    roles = extract_roles(payload)
    return {
        "sub": payload.get("sub"),
        "email": payload.get("email"),
        "name": payload.get("name"),
        "preferred_username": payload.get("preferred_username"),
        "roles": roles,
        "email_verified": payload.get("email_verified", False),
    }
