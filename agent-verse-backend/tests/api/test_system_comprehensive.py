"""Comprehensive tests for /system endpoints — targets 30% → 70%+ coverage."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.system import router as system_router
from app.tenancy.middleware import SecurityHeadersMiddleware


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(system_router)
    return app


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_all_healthy() -> None:
    app = _make_app()
    registry = MagicMock()
    registry.run = AsyncMock(
        return_value=(True, {"db": "ok", "redis": "ok"})
    )
    app.state.health = registry
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["checks"]["db"] == "ok"


def test_health_partial_failure() -> None:
    app = _make_app()
    registry = MagicMock()
    registry.run = AsyncMock(
        return_value=(False, {"db": "ok", "redis": "timeout"})
    )
    app.state.health = registry
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unhealthy"
    assert body["checks"]["redis"] == "timeout"


def test_health_all_down() -> None:
    app = _make_app()
    registry = MagicMock()
    registry.run = AsyncMock(
        return_value=(False, {"db": "error", "redis": "error"})
    )
    app.state.health = registry
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# /metrics (Prometheus)
# ---------------------------------------------------------------------------

def test_metrics_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.observability.metrics.render_metrics",
        lambda: (b"# HELP foo bar\nfoo 1.0\n", "text/plain; version=0.0.4"),
    )
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


def test_metrics_returns_content() -> None:
    # render_metrics is imported at module level in system.py, so patch it there
    import app.api.system as system_module
    original = system_module.render_metrics
    system_module.render_metrics = lambda: (b"process_cpu_seconds_total 1.0\n", "text/plain; version=0.0.4")
    try:
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/metrics")
        assert b"process_cpu_seconds_total" in resp.content
    finally:
        system_module.render_metrics = original


# ---------------------------------------------------------------------------
# /.well-known/jwks.json
# ---------------------------------------------------------------------------

def test_jwks_no_redis_no_service() -> None:
    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    body = resp.json()
    assert "keys" in body
    assert isinstance(body["keys"], list)


def test_jwks_with_redis_cache_hit() -> None:
    import json

    app = _make_app()
    cached_jwks = json.dumps({"keys": [{"kty": "RSA", "kid": "key-1"}]}).encode()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=cached_jwks)
    app.state._rate_limiter_redis = redis

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["keys"]) == 1
    assert body["keys"][0]["kid"] == "key-1"


def test_jwks_with_redis_cache_miss() -> None:
    app = _make_app()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    app.state._rate_limiter_redis = redis

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
    assert "keys" in resp.json()


def test_jwks_redis_error_graceful() -> None:
    """Redis errors should be caught — fallback to building keys."""
    app = _make_app()
    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=Exception("Redis down"))
    app.state._rate_limiter_redis = redis

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/.well-known/jwks.json")
    assert resp.status_code == 200


def test_jwks_with_agent_identity_service(monkeypatch) -> None:
    async def _build_jwks_stub(db_factory):
        return [{"kty": "RSA", "kid": "k1"}]

    monkeypatch.setattr("app.auth.agent_identity._build_jwks", _build_jwks_stub)

    app = _make_app()
    db_factory = AsyncMock()
    svc = MagicMock()
    svc._db = db_factory
    app.state.agent_identity_service = svc

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/.well-known/jwks.json")
    assert resp.status_code == 200
