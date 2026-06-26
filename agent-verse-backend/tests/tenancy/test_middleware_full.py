"""Full coverage for TenantMiddleware and SecurityHeadersMiddleware."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="mid-t1", plan=PlanTier.PROFESSIONAL, api_key_id="mk1")
_VALID_KEY = "av_test_middlewarekey"


def _make_app() -> FastAPI:
    app = FastAPI()

    async def resolver(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=resolver)
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/test")
    async def test_endpoint() -> dict:
        return {"ok": True}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


def test_middleware_allows_bypass_paths() -> None:
    """Paths in the bypass list (/health, /docs, etc.) require no auth."""
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.status_code == 200


def test_middleware_blocks_without_key() -> None:
    """Protected endpoints without an API key return 401."""
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/test")
    assert resp.status_code == 401


def test_middleware_allows_bearer_token() -> None:
    """Valid Bearer token in Authorization header is accepted."""
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/test", headers={"Authorization": f"Bearer {_VALID_KEY}"})
    assert resp.status_code == 200


def test_middleware_allows_x_api_key() -> None:
    """Valid X-API-Key header is accepted."""
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/test", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200


def test_middleware_blocks_invalid_key() -> None:
    """An unrecognised API key returns 401."""
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/test", headers={"X-API-Key": "invalid-key-xyz"})
    assert resp.status_code == 401


def test_security_headers_present() -> None:
    """SecurityHeadersMiddleware adds all required OWASP security headers."""
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/health")
    h = {k.lower(): v for k, v in resp.headers.items()}
    assert h.get("x-frame-options") == "DENY"
    assert h.get("x-content-type-options") == "nosniff"
    assert "strict-transport-security" in h
    assert "referrer-policy" in h
    assert "permissions-policy" in h
    assert "x-xss-protection" in h


def test_middleware_empty_bearer_returns_401() -> None:
    """'Authorization: Bearer ' with empty token returns 401."""
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/test", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401


def test_middleware_rate_limiter_read_from_app_state() -> None:
    """Middleware reads rate-limiter from app.state._rate_limiter_redis at dispatch time."""

    class _FakeRedis:
        """Minimal in-memory stub implementing the sorted-set ops used by the rate limiter."""

        async def get(self, k: str) -> None:
            return None

        async def set(self, k: str, v: str, *, ex: int | None = None) -> None:
            pass

        async def zadd(self, k: str, m: dict) -> int:
            return 0

        async def zremrangebyscore(self, k: str, mn: float, mx: float) -> int:
            return 0

        async def zcard(self, k: str) -> int:
            return 0

        async def expire(self, k: str, t: int) -> bool:
            return True

    app = _make_app()
    app.state._rate_limiter_redis = _FakeRedis()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/test", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    # Rate-limit response headers should be attached
    assert "x-ratelimit-limit" in {k.lower() for k in resp.headers}
