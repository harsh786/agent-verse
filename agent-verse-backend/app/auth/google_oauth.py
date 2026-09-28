"""Google OIDC login — Authorization Code + PKCE flow.

Endpoints:
  GET /auth/google/login  → redirect to Google with PKCE
  GET /auth/google/callback → exchange code, upsert user, mint JWT
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/auth/google", tags=["auth"])

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
_REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
_FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

# In-memory PKCE state store — replaced with Redis helpers when Redis is available
_pkce_store: dict[str, dict[str, Any]] = {}


def _pkce_redis_key(state: str) -> str:
    return f"pkce_state:{state}"


async def _pkce_store_set(state: str, data: dict[str, Any], redis: Any = None) -> None:
    """Persist PKCE state to Redis (with 5-min TTL) or in-memory fallback."""
    if redis is not None:
        import contextlib
        import json as _json

        with contextlib.suppress(Exception):
            await redis.set(_pkce_redis_key(state), _json.dumps(data), ex=300)
    _pkce_store[state] = data


async def _pkce_store_pop(state: str, redis: Any = None) -> dict[str, Any] | None:
    """Retrieve-and-delete PKCE state from Redis or in-memory fallback."""
    if redis is not None:
        import json as _json

        try:
            raw = await redis.get(_pkce_redis_key(state))
            if raw:
                await redis.delete(_pkce_redis_key(state))
                return _json.loads(raw)
        except Exception:
            pass
    return _pkce_store.pop(state, None)


def _generate_pkce() -> tuple[str, str]:
    """Generate code_verifier and code_challenge for PKCE."""
    import base64

    verifier = secrets.token_urlsafe(64)
    challenge = hashlib.sha256(verifier.encode()).digest()
    challenge_b64 = base64.urlsafe_b64encode(challenge).rstrip(b"=").decode()
    return verifier, challenge_b64


@router.get("/login")
async def google_login(request: Request) -> RedirectResponse:
    """Redirect user to Google consent screen."""
    if not _CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google OAuth not configured")

    state = secrets.token_urlsafe(32)
    verifier, challenge = _generate_pkce()
    _redis = getattr(getattr(request, "app", None), "state", None)
    _redis = (getattr(_redis, "redis", None) or getattr(_redis, "_redis", None)) if _redis else None
    await _pkce_store_set(state, {"verifier": verifier, "created_at": time.time()}, redis=_redis)

    params = {
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return RedirectResponse(f"{_GOOGLE_AUTH_URL}?{urlencode(params)}")


@router.get("/callback")
async def google_callback(
    code: str = Query(...),
    state: str = Query(...),
    request: Request = None,  # type: ignore[assignment]
) -> JSONResponse:
    """Exchange auth code for tokens, upsert user, mint AgentVerse JWT."""
    _redis = getattr(getattr(request, "app", None), "state", None)
    _redis = (getattr(_redis, "redis", None) or getattr(_redis, "_redis", None)) if _redis else None
    pkce = await _pkce_store_pop(state, redis=_redis)
    if not pkce:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    # Exchange code for tokens
    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "client_id": _CLIENT_ID,
                "client_secret": _CLIENT_SECRET,
                "code": code,
                "redirect_uri": _REDIRECT_URI,
                "grant_type": "authorization_code",
                "code_verifier": pkce["verifier"],
            },
        )

    if token_resp.status_code != 200:
        logger.warning("google_token_exchange_failed", status=token_resp.status_code)
        raise HTTPException(status_code=400, detail="OAuth token exchange failed")

    token_data = token_resp.json()
    access_token = token_data.get("access_token")

    # Fetch user info
    async with httpx.AsyncClient(timeout=10) as client:
        user_resp = await client.get(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if user_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch user info")

    user_info = user_resp.json()
    email = user_info.get("email", "")
    google_sub = user_info.get("sub", "")
    name = user_info.get("name", "")
    picture = user_info.get("picture", "")

    if not email:
        raise HTTPException(status_code=400, detail="Email not provided by Google")
    # upsert_google_user links by email: an unverified address would let a
    # Google account claim an existing AgentVerse user with that email.
    if user_info.get("email_verified") is not True:
        raise HTTPException(status_code=400, detail="Google account email is not verified")

    app_state = getattr(request.app, "state", None) if request else None
    db_factory = getattr(app_state, "db_session_factory", None) if app_state else None
    if db_factory is None:
        raise HTTPException(status_code=503, detail="Google sign-in requires the database")
    try:
        from app.auth.user_service import upsert_google_user

        user_id, tenant_id = await upsert_google_user(
            db_factory=db_factory,
            email=email,
            google_sub=google_sub,
            name=name,
            picture_url=picture,
        )
    except Exception as exc:
        logger.warning("google_user_upsert_failed", error=str(exc)[:100])
        raise HTTPException(status_code=503, detail="Could not record the Google user") from exc

    # NOT IMPLEMENTED: AgentVerse has no session credential for a Google-
    # authenticated user that TenantMiddleware accepts. The callback used to
    # import a non-existent ``app.auth.jwt_service``, swallow the ImportError and
    # answer 200 with a "note" (and a tenant_id derived from the email's local
    # part when the upsert failed). Say so honestly instead of faking a login.
    logger.info("google_login_verified_no_session", user_id=user_id, tenant_id=tenant_id)
    return JSONResponse(
        status_code=501,
        content={
            "error": "NOT_IMPLEMENTED",
            "detail": (
                "Google sign-in verified this account, but issuing an AgentVerse "
                "session for it is not implemented. Use an API key or Keycloak SSO."
            ),
            "user_id": user_id,
            "tenant_id": tenant_id,
        },
    )
