"""Tenant authentication middleware and security-headers middleware.

TenantMiddleware:
  - Extracts API key from ``Authorization: Bearer <key>`` or ``X-API-Key`` header.
  - Calls the injected ``key_resolver`` (DB lookup in production, fake in tests).
  - Sets ``request.state.tenant: TenantContext`` on success; returns 401 otherwise.
  - Bypasses auth for health, metrics, docs, and OpenAPI paths.
  - Checks rate limit BEFORE forwarding the request; adds X-RateLimit-* headers
    to the response when a Redis-compatible ``rate_limiter`` client is provided.

SecurityHeadersMiddleware:
  - Adds OWASP-recommended security headers (including HSTS) to every response.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.tenancy.context import TenantContext

# ---------------------------------------------------------------------------
# H4: In-process rate-limit fallback (used when Redis is unavailable)
# ---------------------------------------------------------------------------
# Maps tenant_id → (window_start: float, count: int)
_fallback_counters: dict[str, tuple[float, int]] = {}
_FALLBACK_RPM_LIMIT = 120  # conservative cap applied on top of the plan limit


async def _check_rate_limit_with_fallback(tenant_id: str, redis: Any, rpm_limit: int) -> bool:
    """Check rate limit, using in-process counter when Redis is unavailable.

    Returns True if the request should be allowed, False if it should be
    rate-limited.  Never fails open — always enforces at least
    ``min(rpm_limit, _FALLBACK_RPM_LIMIT)`` even without Redis.
    """
    if redis is not None:
        try:
            from app.tenancy.rate_limiter import SlidingWindowRateLimiter
            from app.tenancy.store import TenantScopedStore

            store = TenantScopedStore(redis=redis, tenant_id=tenant_id)
            limiter = SlidingWindowRateLimiter(store=store)
            allowed, _, _ = await limiter.check_and_record("api", limit=rpm_limit)
            return allowed
        except Exception:
            pass  # Redis error — fall through to in-process fallback

    # In-process fallback: conservative sliding window without Redis
    import time as _time

    now = _time.monotonic()
    window_start, count = _fallback_counters.get(tenant_id, (now, 0))
    if now - window_start > 60:  # new 60-second window
        _fallback_counters[tenant_id] = (now, 1)
        return True
    effective_limit = min(rpm_limit, _FALLBACK_RPM_LIMIT)
    if count >= effective_limit:
        return False  # fail-closed: enforce limit even without Redis
    _fallback_counters[tenant_id] = (window_start, count + 1)
    return True


# Paths that do not require API-key authentication
_BYPASS_PREFIXES = (
    "/health",
    "/metrics",
    "/status",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/tenants/signup",  # public — no auth yet to sign up
    "/auth/login",  # SSO redirect initiation
    "/auth/callback",  # SSO OAuth2 callback
    "/auth/config",  # frontend SSO config discovery
    "/auth/token",  # authorization code exchange
    "/integrations/",  # integration webhooks use their own auth (Slack sig, Zapier secret)
    "/billing/webhook",  # Razorpay webhook — authenticated by HMAC signature, not API key
)

KeyResolver = Callable[[str], Awaitable[TenantContext | None]]


def _extract_key(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    # X-API-Key header (standard path)
    header_key = request.headers.get("X-API-Key")
    if header_key:
        return header_key
    # api_key query param — needed for EventSource (SSE) which cannot set headers
    query_key = request.query_params.get("api_key")
    if query_key:
        return query_key
    return None


def _is_cors_preflight(request: Request) -> bool:
    return (
        request.method == "OPTIONS"
        and "origin" in request.headers
        and "access-control-request-method" in request.headers
    )


async def _try_resolve_sso(request: Request) -> TenantContext | None:
    """Attempt to resolve a Keycloak JWT Bearer token to a TenantContext.

    Returns None if SSO is disabled, if the token isn't a JWT, or if
    validation fails — allowing the API key flow to handle it instead.
    """
    from app.auth.keycloak import _sso_enabled, resolve_tenant_from_jwt

    if not _sso_enabled():
        return None

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None

    token = auth[7:].strip()
    if not token or len(token) < 20:
        return None

    # Only try JWT validation if it looks like a JWT (3 base64 segments separated by dots)
    # API keys typically have no dots.
    if token.count(".") < 2:
        return None  # Not a JWT — let API key flow handle it

    tenant_service = getattr(request.app.state, "tenant_service", None)
    if tenant_service is None:
        return None

    try:
        return await resolve_tenant_from_jwt(token, tenant_service)
    except Exception:
        return None  # Fall through to API key auth


def _auth_error_response() -> JSONResponse:
    return JSONResponse(
        content={
            "error": {
                "code": "AUTHENTICATION_ERROR",
                "message": (
                    "Missing or invalid API key. "
                    "Pass it as 'Authorization: Bearer <key>' or 'X-API-Key: <key>'."
                ),
                "retryable": False,
            }
        },
        status_code=401,
    )


def _rate_limit_response(reset_at: float) -> JSONResponse:
    resp = JSONResponse(
        content={
            "error": {
                "code": "RATE_LIMITED",
                "message": "Rate limit exceeded. Please slow down.",
                "retryable": True,
            }
        },
        status_code=429,
    )
    resp.headers["Retry-After"] = str(int(reset_at))
    return resp


class TenantMiddleware(BaseHTTPMiddleware):
    """Authenticate API key → inject TenantContext into request.state.tenant.

    When *rate_limiter* is provided it must be a Redis-compatible async client
    (e.g. ``redis.asyncio.Redis`` or the in-memory ``_FakeRedis`` stub).  A
    per-tenant :class:`~app.tenancy.store.TenantScopedStore` and
    :class:`~app.tenancy.rate_limiter.SlidingWindowRateLimiter` are created on
    each request so limits are correctly isolated per tenant.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        key_resolver: KeyResolver,
        rate_limiter: Any | None = None,
    ) -> None:
        super().__init__(app)
        self._resolver = key_resolver
        self._rate_limiter = rate_limiter  # Redis-compatible client (not a limiter instance)

    async def dispatch(
        self, request: Request, call_next: Callable[..., Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if _is_cors_preflight(request) or any(path.startswith(p) for p in _BYPASS_PREFIXES):
            return await call_next(request)

        raw_key = _extract_key(request)
        if raw_key is None:
            return _auth_error_response()

        # Try SSO JWT first when the token looks like a JWT (has 2+ dots)
        tenant_ctx: TenantContext | None = None
        if raw_key.count(".") >= 2:
            tenant_ctx = await _try_resolve_sso(request)

        # Fall back to API key resolution
        if tenant_ctx is None:
            tenant_ctx = await self._resolver(raw_key)

        if tenant_ctx is None:
            return _auth_error_response()

        request.state.tenant = tenant_ctx

        # ── MFA enforcement (when enabled) ───────────────────────────────────
        from app.core.config import get_settings as _get_settings

        _settings = _get_settings()
        if _settings.mfa_enforcement_enabled:
            import time as _mfa_time

            try:
                from app.api.mfa import _mfa_db_store, _mfa_verified_sessions

                mfa_state = await _mfa_db_store.get(tenant_ctx.tenant_id)
                if mfa_state.get("enabled"):
                    mfa_token = request.headers.get("X-MFA-Token", "")
                    if not mfa_token:
                        return JSONResponse(
                            content={
                                "error": {
                                    "code": "MFA_REQUIRED",
                                    "message": (
                                        "MFA verification required. Include X-MFA-Token header."
                                    ),
                                    "retryable": False,
                                }
                            },
                            status_code=401,
                        )
                    session_valid = _mfa_verified_sessions.get(mfa_token)
                    if not session_valid or session_valid.get("tenant_id") != tenant_ctx.tenant_id:
                        return JSONResponse(
                            content={
                                "error": {
                                    "code": "MFA_REQUIRED",
                                    "message": (
                                        "MFA verification required. Include X-MFA-Token header."
                                    ),
                                    "retryable": False,
                                }
                            },
                            status_code=401,
                        )
                    if _mfa_time.monotonic() - session_valid.get("created_at", 0) > 3600:
                        del _mfa_verified_sessions[mfa_token]
                        return JSONResponse(
                            content={
                                "error": {
                                    "code": "MFA_SESSION_EXPIRED",
                                    "message": ("MFA session expired. Please re-authenticate."),
                                    "retryable": False,
                                }
                            },
                            status_code=401,
                        )
            except ImportError:
                pass  # MFA module not available, skip enforcement

        # ── Rate limiting (check BEFORE processing; headers added AFTER) ──────
        rl_limit: int | None = None
        rl_remaining: int | None = None
        rl_reset: float | None = None

        # Prefer app.state._rate_limiter_redis (upgraded to real Redis in the lifespan)
        # over the construction-time stub so multi-replica rate limiting works without
        # restarting the process.
        rate_redis = getattr(request.app.state, "_rate_limiter_redis", None) or self._rate_limiter
        if rate_redis is not None:
            from app.tenancy.context import PLAN_LIMITS
            from app.tenancy.rate_limiter import SlidingWindowRateLimiter
            from app.tenancy.store import TenantScopedStore

            # Build a per-tenant store so each tenant has its own rate-limit bucket.
            store = TenantScopedStore(redis=rate_redis, tenant_id=tenant_ctx.tenant_id)
            limiter = SlidingWindowRateLimiter(store=store)
            limits = PLAN_LIMITS[tenant_ctx.plan]
            rl_limit = limits.requests_per_minute

            allowed, remaining, reset_at = await limiter.check_and_record(path, limit=rl_limit)
            rl_remaining = remaining
            rl_reset = reset_at

            if not allowed:
                return _rate_limit_response(reset_at)
        else:
            # H4: No Redis — use in-process fallback instead of failing open
            import time as _time

            from app.tenancy.context import PLAN_LIMITS

            limits = PLAN_LIMITS[tenant_ctx.plan]
            rl_limit = limits.requests_per_minute
            if not await _check_rate_limit_with_fallback(
                tenant_ctx.tenant_id, None, rpm_limit=rl_limit
            ):
                return _rate_limit_response(_time.time() + 60)

        response = await call_next(request)

        # Attach informational X-RateLimit-* headers to the response.
        if rl_limit is not None and rl_remaining is not None and rl_reset is not None:
            response.headers["X-RateLimit-Limit"] = str(rl_limit)
            response.headers["X-RateLimit-Remaining"] = str(max(0, rl_remaining))
            response.headers["X-RateLimit-Reset"] = str(int(rl_reset))

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add OWASP security headers to every response."""

    async def dispatch(
        self, request: Request, call_next: Callable[..., Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Fix 1: HSTS — instruct browsers to always use HTTPS for this domain.
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )
        # Content-Security-Policy to restrict resource loading
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self'; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none'"
        )
        return response
