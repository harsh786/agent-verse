"""Login-session endpoints must not pretend to work.

Regression: nothing in the codebase records login sessions (no code writes the
``session:{tenant}:{id}`` keys, and bearer/API-key auth never consults them), so
GET /tenants/me/sessions always answered ``[]``, GET /auth/sessions answered
``[]`` or a fake "current" row, and DELETE /auth/sessions[/{id}] answered 204
without revoking anything — a user "logging out" a stolen session was told it
worked. Until real session tracking + enforcement exists these answer 501.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.sessions import router as sessions_router
from app.api.tenants import router as tenants_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-s", plan=PlanTier.PROFESSIONAL, api_key_id="kid-s")
_KEY = "av_test_sessions_501"
H = {"X-API-Key": _KEY}


def _app(redis: object | None = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(tenants_router)
    app.include_router(sessions_router)
    app.state.tenant_service = AsyncMock()
    app.state._rate_limiter_redis = redis
    return app


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/tenants/me/sessions"),
        ("DELETE", "/tenants/me/sessions/abc"),
        ("GET", "/auth/sessions"),
        ("DELETE", "/auth/sessions/abc"),
        ("DELETE", "/auth/sessions"),
    ],
)
@pytest.mark.parametrize("with_redis", [False, True])
def test_session_endpoints_answer_501(method: str, path: str, with_redis: bool) -> None:
    redis = AsyncMock() if with_redis else None
    client = TestClient(_app(redis), raise_server_exceptions=False)
    resp = client.request(method, path, headers=H)
    assert resp.status_code == 501, resp.text
    assert "not implemented" in resp.text.lower()
    if redis is not None:
        redis.delete.assert_not_called()


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/tenants/me/sessions"), ("GET", "/auth/sessions")],
)
def test_session_endpoints_still_require_auth(method: str, path: str) -> None:
    client = TestClient(_app(), raise_server_exceptions=False)
    assert client.request(method, path).status_code == 401
