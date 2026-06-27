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
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def _default_redirect_uri() -> str:
    """Build the default OAuth redirect URI from the FRONTEND_URL env var."""
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
    return f"{frontend_url}/auth/callback"


@router.get("/config")
async def get_sso_config() -> dict[str, Any]:
    """Return SSO configuration so the frontend knows where to redirect."""
    from app.auth.keycloak import (
        _sso_enabled, _keycloak_url, _realm, _client_id,
        authorization_endpoint, token_endpoint,
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
    from app.auth.keycloak import authorization_endpoint, _client_id

    import urllib.parse
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
    code: str,
    redirect_uri: str = "",  # No default — require explicit or fall back to env var
) -> dict[str, Any]:
    """Exchange authorization code for access + refresh tokens."""
    if not redirect_uri:
        redirect_uri = _default_redirect_uri()
    from app.auth.keycloak import token_endpoint, _client_id

    client_secret = os.getenv("KEYCLOAK_CLIENT_SECRET", "agentverse-dev-secret")

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
async def refresh_token(refresh_token_value: str) -> dict[str, Any]:
    """Refresh an expired access token using a refresh token."""
    from app.auth.keycloak import token_endpoint, _client_id

    client_secret = os.getenv("KEYCLOAK_CLIENT_SECRET", "agentverse-dev-secret")

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
    from app.auth.keycloak import _sso_enabled, validate_jwt, extract_roles

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
