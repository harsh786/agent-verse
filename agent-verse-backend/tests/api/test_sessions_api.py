"""a10-F240-01: self-service login-session endpoints (unit, fake store).

They answered 501 because nothing recorded sessions; SAML-01 added the
Postgres-backed session store, so they now list / revoke the caller's own
sessions. An API-key caller holds no login session: 409, never an empty list
("no other sessions") and never a revocation. Real-Postgres coverage:
tests/integration/test_user_sessions_pg.py::test_self_service_session_management.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.sessions import router as sessions_router
from app.api.tenants import router as tenants_router
from app.auth.user_sessions import SessionStoreUnavailableError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-s", plan=PlanTier.PROFESSIONAL, api_key_id="kid-s")
_KEY = "av_test_sessions_key"
H = {"X-API-Key": _KEY}
_TOKEN = "avs_" + "t" * 20
SH = {"Authorization": f"Bearer {_TOKEN}"}
_USER_CTX = TenantContext(
    tenant_id="tid-s",
    plan=PlanTier.PROFESSIONAL,
    api_key_id="user:u1",
    roles=("viewer",),
    user_id="u1",
)


class _Store:
    def __init__(self, *, fail: bool = False, revoked: int = 1) -> None:
        self.fail = fail
        self.revoked = revoked
        self.calls: list[tuple[str, Any]] = []

    async def resolve(self, token: str) -> TenantContext | None:
        return _USER_CTX if token == _TOKEN else None

    async def list_user_sessions(self, tenant_id: str, user_id: str, *, current_token: str):
        self.calls.append(("list", (tenant_id, user_id, current_token)))
        if self.fail:
            raise SessionStoreUnavailableError("down")
        return [{"session_id": "s1", "auth_method": "saml", "current": True}]

    async def revoke_own_sessions(self, tenant_id: str, user_id: str, **kw: Any) -> int:
        self.calls.append(("revoke", (tenant_id, user_id, kw)))
        if self.fail:
            raise SessionStoreUnavailableError("down")
        return self.revoked


def _app(store: _Store | None = None, redis: object | None = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(tenants_router)
    app.include_router(sessions_router)
    app.state.tenant_service = AsyncMock()
    app.state._rate_limiter_redis = redis
    app.state.user_session_store = store or _Store()
    return app


_ROUTES = [
    ("GET", "/tenants/me/sessions"),
    ("DELETE", "/tenants/me/sessions/abc"),
    ("GET", "/auth/sessions"),
    ("DELETE", "/auth/sessions/abc"),
    ("DELETE", "/auth/sessions"),
]


@pytest.mark.parametrize(("method", "path"), _ROUTES)
@pytest.mark.parametrize("with_redis", [False, True])
def test_api_key_callers_get_409_and_nothing_is_revoked(
    method: str, path: str, with_redis: bool
) -> None:
    redis = AsyncMock() if with_redis else None
    store = _Store()
    client = TestClient(_app(store, redis), raise_server_exceptions=False)
    resp = client.request(method, path, headers=H)
    assert resp.status_code == 409, resp.text
    assert "/tenants/me/keys" in resp.text
    assert store.calls == []
    if redis is not None:
        redis.delete.assert_not_called()


@pytest.mark.parametrize(("method", "path"), _ROUTES)
def test_session_endpoints_require_auth(method: str, path: str) -> None:
    client = TestClient(_app(), raise_server_exceptions=False)
    assert client.request(method, path).status_code == 401


@pytest.mark.parametrize("path", ["/auth/sessions", "/tenants/me/sessions"])
def test_list_returns_the_callers_sessions(path: str) -> None:
    store = _Store()
    resp = TestClient(_app(store)).get(path, headers=SH)
    assert resp.status_code == 200, resp.text
    assert resp.json() == [{"session_id": "s1", "auth_method": "saml", "current": True}]
    assert store.calls == [("list", ("tid-s", "u1", _TOKEN))]


@pytest.mark.parametrize("path", ["/auth/sessions/s9", "/tenants/me/sessions/s9"])
def test_revoke_one_is_scoped_to_the_caller(path: str) -> None:
    store = _Store()
    assert TestClient(_app(store)).delete(path, headers=SH).status_code == 204
    assert store.calls == [("revoke", ("tid-s", "u1", {"session_id": "s9"}))]


def test_revoking_an_unknown_or_foreign_session_is_404() -> None:
    resp = TestClient(_app(_Store(revoked=0))).delete("/auth/sessions/x", headers=SH)
    assert resp.status_code == 404


def test_revoke_all_others_keeps_the_current_token() -> None:
    store = _Store(revoked=3)
    resp = TestClient(_app(store)).delete("/auth/sessions", headers=SH)
    assert resp.status_code == 200 and resp.json() == {"revoked": 3}
    assert store.calls == [("revoke", ("tid-s", "u1", {"keep_token": _TOKEN}))]


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/auth/sessions"), ("DELETE", "/auth/sessions/x"), ("DELETE", "/auth/sessions")],
)
def test_store_outage_is_503(method: str, path: str) -> None:
    client = TestClient(_app(_Store(fail=True)), raise_server_exceptions=False)
    assert client.request(method, path, headers=SH).status_code == 503
