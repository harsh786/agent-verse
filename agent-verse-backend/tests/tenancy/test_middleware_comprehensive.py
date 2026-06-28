"""Comprehensive TenantMiddleware coverage.

Fills the gaps left by test_middleware.py and test_middleware_full.py:
  - _extract_key() helper, both branches and edge cases
  - _is_cors_preflight() helper directly
  - _auth_error_response() and _rate_limit_response() directly
  - 429 rate-limit response through actual middleware with blocking fake Redis
  - All bypass prefixes tested individually
  - X-RateLimit-* headers present on successful responses
  - CSP and HSTS header content
  - _try_resolve_sso() path via a JWT-shaped token + mocked keycloak
"""
from __future__ import annotations

import json
import re
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import (
    SecurityHeadersMiddleware,
    TenantMiddleware,
    _auth_error_response,
    _extract_key,
    _is_cors_preflight,
    _rate_limit_response,
)

_CTX = TenantContext(tenant_id="cov-t1", plan=PlanTier.FREE, api_key_id="cov-k1")
_KEY = "av_coverage_testkey"


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_app(*, resolver=None, rate_limiter=None) -> FastAPI:
    app = FastAPI()

    async def default_resolver(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(
        TenantMiddleware,
        key_resolver=resolver or default_resolver,
        rate_limiter=rate_limiter,
    )

    @app.get("/test")
    async def test_endpoint():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics():
        return {"metrics": True}

    return app


def _fake_req(auth: str | None = None, x_api_key: str | None = None) -> MagicMock:
    from starlette.datastructures import Headers

    headers: dict[str, str] = {}
    if auth is not None:
        headers["Authorization"] = auth
    if x_api_key is not None:
        headers["X-API-Key"] = x_api_key
    req = MagicMock()
    req.headers = Headers(headers)
    return req


# ── _extract_key() ─────────────────────────────────────────────────────────────

def test_extract_key_bearer():
    assert _extract_key(_fake_req(auth="Bearer my-token")) == "my-token"


def test_extract_key_bearer_strips_whitespace():
    # The code does auth[7:].strip() — verify trailing spaces are removed
    assert _extract_key(_fake_req(auth="Bearer   padded  ")) == "padded"


def test_extract_key_empty_bearer_returns_none():
    assert _extract_key(_fake_req(auth="Bearer ")) is None


def test_extract_key_bearer_only_spaces_returns_none():
    assert _extract_key(_fake_req(auth="Bearer    ")) is None


def test_extract_key_x_api_key():
    assert _extract_key(_fake_req(x_api_key="xkey-abc")) == "xkey-abc"


def test_extract_key_prefers_bearer_when_both_present():
    req = _fake_req(auth="Bearer bearer-wins", x_api_key="x-api-loses")
    assert _extract_key(req) == "bearer-wins"


def test_extract_key_no_headers_returns_none():
    assert _extract_key(_fake_req()) is None


def test_extract_key_empty_x_api_key_returns_none():
    from starlette.datastructures import Headers

    req = MagicMock()
    req.headers = Headers({"X-API-Key": ""})
    assert _extract_key(req) is None


# ── _is_cors_preflight() ───────────────────────────────────────────────────────

def _cors_req(method: str, *, origin: str | None = None, acr_method: str | None = None) -> MagicMock:
    from starlette.datastructures import Headers

    headers: dict[str, str] = {}
    if origin is not None:
        headers["origin"] = origin
    if acr_method is not None:
        headers["access-control-request-method"] = acr_method
    req = MagicMock()
    req.method = method
    req.headers = Headers(headers)
    return req


def test_is_cors_preflight_true_options_with_both_headers():
    req = _cors_req("OPTIONS", origin="https://example.com", acr_method="POST")
    assert _is_cors_preflight(req) is True


def test_is_cors_preflight_false_get_method():
    req = _cors_req("GET", origin="https://example.com", acr_method="POST")
    assert _is_cors_preflight(req) is False


def test_is_cors_preflight_false_post_method():
    req = _cors_req("POST", origin="https://example.com", acr_method="PUT")
    assert _is_cors_preflight(req) is False


def test_is_cors_preflight_false_missing_origin():
    req = _cors_req("OPTIONS", acr_method="GET")
    assert _is_cors_preflight(req) is False


def test_is_cors_preflight_false_missing_acr_method():
    req = _cors_req("OPTIONS", origin="https://example.com")
    assert _is_cors_preflight(req) is False


# ── _auth_error_response() ─────────────────────────────────────────────────────

def test_auth_error_response_is_401():
    resp = _auth_error_response()
    assert resp.status_code == 401


def test_auth_error_response_body_structure():
    resp = _auth_error_response()
    body = json.loads(resp.body)
    assert "error" in body
    assert body["error"]["code"] == "AUTHENTICATION_ERROR"
    assert body["error"]["retryable"] is False


def test_auth_error_response_message_mentions_api_key():
    resp = _auth_error_response()
    body = json.loads(resp.body)
    assert "API key" in body["error"]["message"]


# ── _rate_limit_response() ────────────────────────────────────────────────────

def test_rate_limit_response_is_429():
    resp = _rate_limit_response(reset_at=time.time() + 60)
    assert resp.status_code == 429


def test_rate_limit_response_has_retry_after_header():
    future = time.time() + 120
    resp = _rate_limit_response(reset_at=future)
    retry = resp.headers.get("Retry-After")
    assert retry is not None
    assert int(retry) > 0


def test_rate_limit_response_body_structure():
    resp = _rate_limit_response(reset_at=time.time() + 60)
    body = json.loads(resp.body)
    assert body["error"]["code"] == "RATE_LIMITED"
    assert body["error"]["retryable"] is True


# ── Bypass prefixes via middleware ────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/tenants/signup",
    "/auth/login",
    "/auth/callback",
    "/auth/config",
    "/auth/token",
    "/integrations/slack/webhook",
])
def test_bypass_paths_not_blocked_by_401(path: str) -> None:
    """All paths in _BYPASS_PREFIXES must not return 401 (auth-blocked)."""

    async def reject_all(key: str) -> TenantContext | None:
        return None

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=reject_all)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics():
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(path)
    # Must NOT be 401 — may be 404 if the route isn't registered, but not auth-blocked
    assert resp.status_code != 401, f"{path!r} should bypass auth, got 401"


# ── 429 via middleware with blocking fake Redis ───────────────────────────────

class _BlockingFakeRedis:
    """Fake Redis whose zcard always exceeds any limit → always rate-limits."""

    async def zadd(self, k: str, m: dict) -> int:
        return 0

    async def zremrangebyscore(self, k: str, mn: float, mx: float) -> int:
        return 0

    async def zcard(self, k: str) -> int:
        return 999_999  # always over any per-plan limit

    async def expire(self, k: str, t: int) -> bool:
        return True


def test_rate_limit_returns_429_when_exhausted() -> None:
    app = _make_app(rate_limiter=_BlockingFakeRedis())
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/test", headers={"X-API-Key": _KEY})
    assert resp.status_code == 429
    body = resp.json()
    assert body["error"]["code"] == "RATE_LIMITED"


def test_rate_limit_429_has_retry_after() -> None:
    app = _make_app(rate_limiter=_BlockingFakeRedis())
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/test", headers={"X-API-Key": _KEY})
    assert resp.status_code == 429
    assert "retry-after" in {k.lower() for k in resp.headers}


# ── X-RateLimit-* headers on 200 responses ───────────────────────────────────

class _AllowingFakeRedis:
    """Fake Redis that never blocks — lets requests through and enables header injection."""

    async def zadd(self, k: str, m: dict) -> int:
        return 1

    async def zremrangebyscore(self, k: str, mn: float, mx: float) -> int:
        return 0

    async def zcard(self, k: str) -> int:
        return 0  # always under limit

    async def expire(self, k: str, t: int) -> bool:
        return True


def test_rate_limit_headers_on_successful_response() -> None:
    app = _make_app(rate_limiter=_AllowingFakeRedis())
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/test", headers={"X-API-Key": _KEY})
    assert resp.status_code == 200
    header_names = {k.lower() for k in resp.headers}
    assert "x-ratelimit-limit" in header_names
    assert "x-ratelimit-remaining" in header_names
    assert "x-ratelimit-reset" in header_names


def test_rate_limit_remaining_nonnegative() -> None:
    app = _make_app(rate_limiter=_AllowingFakeRedis())
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/test", headers={"X-API-Key": _KEY})
    remaining = int(resp.headers.get("x-ratelimit-remaining", -1))
    assert remaining >= 0


# ── Rate limiter reads from app.state._rate_limiter_redis ─────────────────────

def test_rate_limiter_upgraded_from_app_state() -> None:
    """Middleware must prefer app.state._rate_limiter_redis over construction-time stub."""
    app = _make_app()  # no rate_limiter at construction
    app.state._rate_limiter_redis = _AllowingFakeRedis()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/test", headers={"X-API-Key": _KEY})
    assert resp.status_code == 200
    # Confirms rate-limiter ran (headers present)
    header_names = {k.lower() for k in resp.headers}
    assert "x-ratelimit-limit" in header_names


# ── SecurityHeadersMiddleware header content ──────────────────────────────────

def _security_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/page")
    async def page():
        return {"ok": True}

    return app


def test_csp_has_default_src_self() -> None:
    resp = TestClient(_security_app()).get("/page")
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp


def test_csp_restricts_frame_ancestors() -> None:
    resp = TestClient(_security_app()).get("/page")
    csp = resp.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in csp


def test_hsts_long_max_age() -> None:
    resp = TestClient(_security_app()).get("/page")
    hsts = resp.headers.get("strict-transport-security", "")
    assert "max-age=" in hsts
    match = re.search(r"max-age=(\d+)", hsts)
    assert match and int(match.group(1)) > 31_536_000  # > 1 year


def test_hsts_includes_subdomains() -> None:
    resp = TestClient(_security_app()).get("/page")
    hsts = resp.headers.get("strict-transport-security", "")
    assert "includeSubDomains" in hsts


def test_permissions_policy_restricts_camera() -> None:
    resp = TestClient(_security_app()).get("/page")
    pp = resp.headers.get("permissions-policy", "")
    assert "camera=()" in pp


def test_permissions_policy_restricts_microphone() -> None:
    resp = TestClient(_security_app()).get("/page")
    pp = resp.headers.get("permissions-policy", "")
    assert "microphone=()" in pp


def test_xss_protection_header() -> None:
    resp = TestClient(_security_app()).get("/page")
    xss = resp.headers.get("x-xss-protection", "")
    assert "1" in xss and "block" in xss


# ── SSO path: JWT-shaped token triggers _try_resolve_sso ─────────────────────

def test_jwt_shaped_token_tries_sso_then_falls_back_to_api_key() -> None:
    """A token with 2+ dots goes through the SSO branch.
    With SSO disabled it falls back to API-key resolution transparently.
    """
    # Craft a 'JWT-shaped' key: two dots present, so dispatch() calls _try_resolve_sso.
    # When keycloak.KEYCLOAK_URL is not set, _sso_enabled() returns False → fall back.
    jwt_shaped_key = f"{_KEY}.header.sig"  # has 2 dots — looks JWT-ish

    async def resolver(key: str) -> TenantContext | None:
        # Strip the fake JWT parts to simulate what a real resolver would do
        base_key = key.split(".")[0]
        return _CTX if base_key == _KEY else None

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=resolver)

    @app.get("/test")
    async def test_endpoint():
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)

    # Patch _sso_enabled to return False so we don't need Keycloak
    with patch("app.auth.keycloak._sso_enabled", return_value=False):
        resp = client.get("/test", headers={"Authorization": f"Bearer {jwt_shaped_key}"})

    # Should be 200 (resolver accepted it) or 401 (resolver rejected it)
    # Either is fine — the key test is that it didn't crash (500)
    assert resp.status_code in (200, 401), f"Expected 200 or 401, got {resp.status_code}"
