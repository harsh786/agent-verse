"""Regression tests: scope and IP-allowlist enforcement fail closed.

1. A key minted with explicit scopes (e.g. ``["goals:read"]``) passed every
   route with no registered scope (``required is None -> allowed``), keeping its
   role's full rights on /grants, /trust, /billing, ...
2. ``/auth/`` was exempt from ScopeEnforcementMiddleware as a whole, so the
   authenticated /auth/* routes (MFA disable, sessions) skipped the tenant IP
   allowlist.
3. The IP allowlist failed open on a DB error (``[]`` == "no allowlist"), and
   without Redis the DB was never consulted at all.
4. ``_load_scopes`` swallowed DB errors and fell back to the (broader) role scopes.
5. /auth/refresh was not in the bypass list, so refreshing with an expired
   access token 401'd before the handler ran.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import scope_enforcement
from app.auth.ip_allowlist import IPAllowlistCache, IPAllowlistUnavailableError
from app.auth.scope_enforcement import ScopeEnforcementMiddleware, ScopeLookupUnavailableError
from app.services.tenant_service import TenantService
from app.tenancy.middleware import TenantMiddleware


def _app(svc: TenantService, *, redis: Any = None) -> FastAPI:
    app = FastAPI()
    app.state.tenant_service = svc
    app.state._rate_limiter_redis = redis
    app.add_middleware(ScopeEnforcementMiddleware)
    app.add_middleware(TenantMiddleware, key_resolver=svc.resolve_api_key)

    @app.get("/goals")
    async def list_goals() -> dict[str, str]:
        return {"ok": "read"}

    @app.post("/grants")
    async def mint_grant() -> dict[str, str]:
        return {"ok": "grant"}

    @app.get("/trust/audit")
    async def trust_audit() -> dict[str, str]:
        return {"ok": "audit"}

    @app.get("/tenants/stream-token")
    async def stream_token() -> dict[str, str]:
        return {"token": "t"}

    @app.post("/auth/mfa/disable")
    async def mfa_disable() -> dict[str, str]:
        return {"ok": "disabled"}

    @app.post("/auth/refresh")
    async def refresh() -> dict[str, str]:
        return {"ok": "refreshed"}

    return app


async def _key(svc: TenantService, scopes: list[str]) -> tuple[str, str]:
    t = await svc.create_tenant(name="Acme", email=f"a{len(svc._tenants)}@acme.test")
    key = await svc.create_api_key(tenant_id=t["tenant_id"], name="k", scopes=scopes)
    return t["tenant_id"], key["raw_key"]


@pytest.fixture(autouse=True)
def _clear_local_allowlist() -> Any:
    scope_enforcement._local_ip_allowlist_cache.clear()
    yield
    scope_enforcement._local_ip_allowlist_cache.clear()


# ── 1. scoped key on unregistered routes ─────────────────────────────────────


@pytest.mark.asyncio
async def test_scoped_key_is_denied_on_unregistered_routes() -> None:
    svc = TenantService()
    _, raw = await _key(svc, ["goals:read"])
    client = TestClient(_app(svc), raise_server_exceptions=False)
    h = {"X-API-Key": raw}
    assert client.get("/goals", headers=h).status_code == 200
    minted = client.post("/grants", headers=h)
    assert minted.status_code == 403
    assert minted.json()["error"] == "INSUFFICIENT_SCOPE"
    assert client.get("/trust/audit", headers=h).status_code == 403


@pytest.mark.asyncio
async def test_scoped_key_may_still_get_a_stream_token() -> None:
    svc = TenantService()
    _, raw = await _key(svc, ["goals:read"])
    client = TestClient(_app(svc), raise_server_exceptions=False)
    assert client.get("/tenants/stream-token", headers={"X-API-Key": raw}).status_code == 200


@pytest.mark.asyncio
async def test_unscoped_key_keeps_role_behaviour_on_unregistered_routes() -> None:
    svc = TenantService()
    _, raw = await _key(svc, [])
    client = TestClient(_app(svc), raise_server_exceptions=False)
    assert client.post("/grants", headers={"X-API-Key": raw}).status_code == 200


# ── 2+3. IP allowlist on /auth/*, fail closed, no-Redis DB path ──────────────


def _patch_cidrs(monkeypatch: pytest.MonkeyPatch, result: Any) -> AsyncMock:
    mock = (
        AsyncMock(side_effect=result)
        if isinstance(result, Exception)
        else AsyncMock(return_value=result)
    )
    monkeypatch.setattr(IPAllowlistCache, "get_cidrs", mock)
    return mock


@pytest.mark.asyncio
async def test_ip_allowlist_applies_to_authenticated_auth_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = TenantService()
    _, raw = await _key(svc, [])
    _patch_cidrs(monkeypatch, ["203.0.113.0/24"])  # TestClient peer is "testclient"
    client = TestClient(_app(svc), raise_server_exceptions=False)
    resp = client.post("/auth/mfa/disable", headers={"X-API-Key": raw})
    assert resp.status_code == 403
    assert resp.json()["error"] == "IP_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_ip_allowlist_is_enforced_without_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = TenantService()
    _, raw = await _key(svc, [])
    mock = _patch_cidrs(monkeypatch, ["203.0.113.0/24"])
    client = TestClient(_app(svc, redis=None), raise_server_exceptions=False)
    assert client.get("/goals", headers={"X-API-Key": raw}).status_code == 403
    assert mock.await_count == 1


@pytest.mark.asyncio
async def test_ip_allowlist_db_error_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = TenantService()
    _, raw = await _key(svc, [])
    _patch_cidrs(monkeypatch, IPAllowlistUnavailableError("db down"))
    client = TestClient(_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals", headers={"X-API-Key": raw})
    assert resp.status_code == 503
    assert resp.json()["error"] == "IP_ALLOWLIST_UNAVAILABLE"


@pytest.mark.asyncio
async def test_ip_allowlist_db_error_uses_last_known_good_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    svc = TenantService()
    tid, raw = await _key(svc, [])
    scope_enforcement._local_ip_allowlist_cache[tid] = (["203.0.113.0/24"], time.monotonic())
    _patch_cidrs(monkeypatch, IPAllowlistUnavailableError("db down"))
    import fakeredis

    client = TestClient(_app(svc, redis=fakeredis.FakeAsyncRedis()), raise_server_exceptions=False)
    assert client.get("/goals", headers={"X-API-Key": raw}).status_code == 403


@pytest.mark.asyncio
async def test_get_cidrs_raises_on_db_error_instead_of_returning_empty() -> None:
    session = AsyncMock()
    session.__aenter__ = AsyncMock(side_effect=RuntimeError("db down"))
    session.__aexit__ = AsyncMock(return_value=False)
    with pytest.raises(IPAllowlistUnavailableError):
        await IPAllowlistCache(None).get_cidrs("t1", db_factory=MagicMock(return_value=session))


@pytest.mark.asyncio
async def test_get_cidrs_without_redis_or_db_is_empty() -> None:
    assert await IPAllowlistCache(None).get_cidrs("t1", db_factory=None) == []


# ── 4. scope lookup DB error ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_scopes_db_error_raises() -> None:
    session = AsyncMock()
    session.__aenter__ = AsyncMock(side_effect=RuntimeError("db down"))
    session.__aexit__ = AsyncMock(return_value=False)
    with pytest.raises(ScopeLookupUnavailableError):
        await ScopeEnforcementMiddleware._load_scopes(
            db_factory=MagicMock(return_value=session),
            tenant_id="t1",
            key_id="k1",
            roles=("admin",),
        )


@pytest.mark.asyncio
async def test_scope_lookup_db_error_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = TenantService()
    _, raw = await _key(svc, [])
    _patch_cidrs(monkeypatch, [])
    monkeypatch.setattr(
        ScopeEnforcementMiddleware,
        "_load_scopes",
        AsyncMock(side_effect=ScopeLookupUnavailableError("db down")),
    )
    client = TestClient(_app(svc), raise_server_exceptions=False)
    resp = client.get("/goals", headers={"X-API-Key": raw})
    assert resp.status_code == 503
    assert resp.json()["error"] == "SCOPE_LOOKUP_UNAVAILABLE"


# ── 5. /auth/refresh bypass ──────────────────────────────────────────────────


def test_auth_refresh_needs_no_api_key() -> None:
    client = TestClient(_app(TenantService()), raise_server_exceptions=False)
    assert client.post("/auth/refresh").status_code == 200


def test_csp_connect_src_does_not_allow_arbitrary_websocket_hosts() -> None:
    """``connect-src 'self' ws: wss:`` let a page open a socket to ANY host."""
    from app.tenancy.middleware import SecurityHeadersMiddleware

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/x")
    async def x() -> dict[str, str]:
        return {}

    csp = TestClient(app).get("/x").headers["Content-Security-Policy"]
    connect = next(d for d in csp.split(";") if d.strip().startswith("connect-src"))
    assert connect.split()[1:] == ["'self'"]
