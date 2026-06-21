"""Tenant authentication middleware and security-headers middleware.

TenantMiddleware:
  - Extracts API key from ``Authorization: Bearer <key>`` or ``X-API-Key`` header.
  - Calls the injected ``key_resolver`` (DB lookup in production, fake in tests).
  - Sets ``request.state.tenant: TenantContext`` on success; returns 401 otherwise.
  - Bypasses auth for health, metrics, docs, and OpenAPI paths.

SecurityHeadersMiddleware:
  - Adds OWASP-recommended security headers to every response.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.tenancy.context import TenantContext

# Paths that do not require API-key authentication
_BYPASS_PREFIXES = (
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/tenants/signup",  # public — no auth yet to sign up
)

KeyResolver = Callable[[str], Awaitable[TenantContext | None]]


def _extract_key(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    return request.headers.get("X-API-Key") or None


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
    """Authenticate API key → inject TenantContext into request.state.tenant."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        key_resolver: KeyResolver,
        rate_limiter: object | None = None,
    ) -> None:
        super().__init__(app)
        self._resolver = key_resolver
        self._rate_limiter = rate_limiter

    async def dispatch(
        self, request: Request, call_next: Callable[..., Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in _BYPASS_PREFIXES):
            return await call_next(request)

        raw_key = _extract_key(request)
        if raw_key is None:
            return _auth_error_response()

        tenant_ctx = await self._resolver(raw_key)
        if tenant_ctx is None:
            return _auth_error_response()

        request.state.tenant = tenant_ctx

        # Optional rate limiting (wired in production via create_app)
        if self._rate_limiter is not None:
            from app.tenancy.context import PLAN_LIMITS
            from app.tenancy.rate_limiter import SlidingWindowRateLimiter

            limiter: SlidingWindowRateLimiter = self._rate_limiter  # type: ignore[assignment]
            limits = PLAN_LIMITS[tenant_ctx.plan]
            allowed, _remaining, reset_at = await limiter.check_and_record(
                path, limit=limits.requests_per_minute
            )
            if not allowed:
                return _rate_limit_response(reset_at)

        return await call_next(request)


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
        return response
