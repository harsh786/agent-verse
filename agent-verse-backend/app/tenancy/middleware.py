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
            allowed, _, _ = await limiter.check_and_record(_TENANT_RATE_BUCKET, limit=rpm_limit)
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
    "/auth/refresh",  # refresh-token exchange: the (expired) access token cannot auth it
    "/auth/userinfo",  # validates the Keycloak JWT itself (401 on an invalid one)
    "/integrations/",  # integration webhooks use their own auth (Slack sig, Zapier secret)
    "/billing/webhook",  # Razorpay webhook — authenticated by HMAC signature, not API key
    "/wf-hooks/",  # workflow webhook triggers — authenticated by the signed token in the path
    "/scim/v2",  # SCIM 2.0 provisioning — IdPs send their own hashed bearer token
    # (require_scim_auth checks it against scim_tokens), never a tenant API key.
    # Without this bypass every SCIM request from an IdP (Okta, Azure AD, ...)
    # is rejected here with a generic 401 before it ever reaches SCIM auth.
    "/v1/gateway/",  # channel webhooks (telegram/whatsapp/slack) use per-channel signature auth
    # The routes below were missing, so their third-party / anonymous callers got
    # a generic 401 here before the handler's own auth could run (unreachable):
    "/.well-known/",  # JWKS + A2A agent cards — public discovery documents by definition
    "/auth/google/",  # Google OIDC login start + callback — PKCE state + Google code exchange
    "/triggers/webhooks/",  # typed webhook delivery — tenant resolved from the path token,
    # signature verified per trigger (app.api.triggers.receive_typed_webhook)
    # Inbound channel webhooks — each verifies its own signature / shared secret
    # (app.api.channels.ingestion). Listed individually so /channels/mappings
    # (tenant CRUD) stays behind API-key auth.
    "/channels/slack/",
    "/channels/teams/",
    "/channels/discord/",
    "/channels/email/",
    "/channels/sms/",
    "/channels/voice/",
    "/channels/forms/",
    "/channels/meeting/",
)

KeyResolver = Callable[[str], Awaitable[TenantContext | None]]

# Rate-limit bucket name: the plan limit is per tenant (TenantScopedStore already
# namespaces the key by tenant), shared by every path the tenant calls.
_TENANT_RATE_BUCKET = "api"


def _key_scope_denial(request: Request, ctx: TenantContext) -> JSONResponse | None:
    """403 when the endpoint's scope is outside the API key's OWN scopes.

    A key created with explicit scopes may use only those scopes. Its roles are
    still enforced by ScopeEnforcementMiddleware (which runs after this one), so
    the effective permission is the intersection of the two. Keys without
    explicit scopes (``ctx.scopes == ()``) are unaffected.

    An endpoint with NO registered scope is denied to a scoped key (fail
    closed): it used to be allowed, so a key minted with ``scopes=["goals:read"]``
    could call every unregistered route (/grants, /trust, /billing, /skills, ...)
    with its role's full rights. Only the scope-neutral session endpoints
    (``SCOPE_NEUTRAL_ENDPOINTS``) stay open to every key.
    """
    if not ctx.scopes:
        return None
    from app.auth.scope_enforcement import (
        EXEMPT_PATH_PREFIXES,
        SCOPE_NEUTRAL_ENDPOINTS,
        ScopeEnforcementMiddleware,
    )

    path = request.url.path
    if any(path.startswith(p) for p in EXEMPT_PATH_PREFIXES):
        return None
    if (request.method, path.rstrip("/") or "/") in SCOPE_NEUTRAL_ENDPOINTS:
        return None
    required = ScopeEnforcementMiddleware._required_scope(request.method, path)
    if required is not None and required in ctx.scopes:
        return None
    detail = (
        f"Insufficient scope: requires {required} (not granted to this API key)"
        if required is not None
        else "This endpoint has no scope that an API key with explicit scopes can hold."
    )
    return JSONResponse(
        status_code=403,
        content={
            "error": "INSUFFICIENT_SCOPE",
            "detail": detail,
            "required_scope": required,
            "granted_scopes": sorted(ctx.scopes),
        },
    )


def _extract_key(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    # X-API-Key header (standard path)
    header_key = request.headers.get("X-API-Key")
    if header_key:
        return header_key
    # No ?api_key= query parameter: a key in a URL lands in access logs, proxy
    # logs and browser history. EventSource/WebSocket clients use a short-lived
    # read-only ?token= stream token instead (see _stream_token_allowed).
    return None


def _stream_token_context(request: Request, claims: dict[str, Any]) -> TenantContext:
    """Build a minimal, read-only TenantContext from a verified stream token.

    The SSE endpoints that accept stream tokens are not scope-gated, so no roles
    are granted (least privilege). The tenant's real plan is looked up from the
    in-memory tenant cache when available, defaulting to ``free``.
    """
    from app.tenancy.context import PlanTier

    tenant_id = str(claims["tenant_id"])
    plan_value = "free"
    svc = getattr(getattr(request.app, "state", None), "tenant_service", None)
    tenants = getattr(svc, "_tenants", None)
    if isinstance(tenants, dict):
        plan_value = str((tenants.get(tenant_id) or {}).get("plan", "free"))
    try:
        plan = PlanTier(plan_value)
    except ValueError:
        plan = PlanTier.FREE
    return TenantContext(
        tenant_id=tenant_id,
        plan=plan,
        api_key_id=str(claims.get("key_id", "")),
        roles=(),
    )


# Last path segments of the endpoints EventSource connects to.
_STREAM_SEGMENTS = frozenset({"stream", "events"})


def _stream_token_allowed(request: Request) -> bool:
    """Whether a ``?token=`` stream token may authenticate this request.

    The token exists because EventSource cannot send headers, so it travels in
    the URL — and therefore into access logs, proxy logs and browser history. It
    was documented as read-only but authenticated ANY method on ANY path, with
    ``roles=()`` as the only restraint; every endpoint that checks just for a
    tenant (most of them) accepted it for writes. It is now honoured only for
    safe methods on streaming endpoints — exactly what EventSource needs — and
    ignored everywhere else, so normal authentication applies.
    """
    if request.method not in ("GET", "HEAD"):
        return False
    last = request.url.path.rstrip("/").rsplit("/", 1)[-1]
    return last in _STREAM_SEGMENTS


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
    except Exception as exc:
        # Unauthenticated (fail closed: the JWT then fails API-key resolution
        # → 401), but never silently — a tenant-store outage looked like a bad token.
        from app.observability.logging import get_logger

        get_logger(__name__).warning("sso_resolution_failed", error=str(exc)[:200])
        return None


# Endpoints a tenant with MFA enabled must reach BEFORE it holds an X-MFA-Token
# (the second factor itself). Without this, enforcement 401'd /auth/mfa/verify
# too, so no tenant with MFA enabled could ever obtain a token.
_MFA_EXEMPT_ENDPOINTS = frozenset({("POST", "/auth/mfa/verify"), ("GET", "/auth/mfa/status")})


def _mfa_exempt(request: Request) -> bool:
    return (request.method, request.url.path.rstrip("/")) in _MFA_EXEMPT_ENDPOINTS


def _mfa_error(code: str, message: str, status_code: int = 401) -> JSONResponse:
    return JSONResponse(
        content={"error": {"code": code, "message": message, "retryable": status_code == 503}},
        status_code=status_code,
    )


async def _mfa_denial(request: Request, tenant_id: str) -> JSONResponse | None:
    """Enforce the X-MFA-Token for a tenant with MFA enabled; fail closed.

    MFA state or session storage that cannot be read is a 503 — never "MFA not
    enabled" (the store used to fall back to an empty cache entry on a DB
    error, silently switching enforcement off).
    """
    from app.api.mfa import MFAStateUnavailableError, _mfa_db_store, check_mfa_session

    required = "MFA verification required. Include X-MFA-Token header."
    try:
        mfa_state = await _mfa_db_store.get(tenant_id)
        if not mfa_state.get("enabled"):
            return None
        mfa_token = request.headers.get("X-MFA-Token", "")
        if not mfa_token:
            return _mfa_error("MFA_REQUIRED", required)
        verdict = await check_mfa_session(request.app, mfa_token, tenant_id)
    except MFAStateUnavailableError:
        return _mfa_error(
            "MFA_UNAVAILABLE", "MFA state is temporarily unavailable; retry shortly.", 503
        )
    if verdict == "expired":
        return _mfa_error("MFA_SESSION_EXPIRED", "MFA session expired. Please re-authenticate.")
    if verdict != "valid":
        return _mfa_error("MFA_REQUIRED", required)
    return None


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

        # Short-lived stream token (?token=): EventSource cannot send headers, so
        # SSE clients exchange their API key for a read-only, ~10-minute token and
        # pass THAT in the stream URL — keeping the permanent api_key out of URLs,
        # access logs, and proxy caches. A valid token authenticates the tenant
        # directly; an invalid/expired one falls through to normal auth (→ 401).
        stream_token = request.query_params.get("token")
        if stream_token and _stream_token_allowed(request):
            from app.auth.stream_tokens import verify_stream_token

            claims = verify_stream_token(stream_token)
            if claims is not None:
                request.state.tenant = _stream_token_context(request, claims)
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
        if _settings.mfa_enforcement_enabled and not _mfa_exempt(request):
            mfa_denied = await _mfa_denial(request, tenant_ctx.tenant_id)
            if mfa_denied is not None:
                return mfa_denied

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

            # One bucket per TENANT. This was keyed by the request path, so every
            # distinct URL (/goals/1, /goals/2, ...) got its own fresh plan-sized
            # bucket and a tenant could multiply its quota without bound.
            allowed, remaining, reset_at = await limiter.check_and_record(
                _TENANT_RATE_BUCKET, limit=rl_limit
            )
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

        denied = _key_scope_denial(request, tenant_ctx)
        if denied is not None:
            return denied

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
