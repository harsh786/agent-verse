"""Regression tests for API-key revoke/create failure handling and the IP allowlist API.

3. ``TenantService._db_revoke_api_key`` swallowed every DB error and returned
   None, so ``DELETE /tenants/me/keys/{id}`` answered 204 while the key stayed
   active in the DB (auth is DB-authoritative → still valid on every pod).

2. ``POST /tenants/me/ip-allowlist`` wrote the legacy ``ip_allowlist`` table, but
   enforcement (``IPAllowlistCache``) reads ``ip_allowlist_entries`` — an added
   CIDR was never enforced — and the enforcement cache was never invalidated.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.auth import scope_enforcement
from app.db.models.auth import IPAllowlistEntry
from app.services.tenant_service import KeyStoreUnavailableError, TenantService, _hash_key
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_KEY = "ak_test_revoke"
_CTX = TenantContext(
    tenant_id="tid-rv", plan=PlanTier.STARTER, api_key_id="kid-admin", roles=("admin",)
)


def _app(svc: Any, *, db: Any = None, redis: Any = None, ctx: TenantContext = _CTX) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(tenants_router)
    app.state.tenant_service = svc
    app.state.db_session_factory = db
    app.state._rate_limiter_redis = redis
    return app


def _failing_db() -> Any:
    @asynccontextmanager
    async def _db() -> Any:
        raise ConnectionError("db down")
        yield  # pragma: no cover

    return _db


# ── 3. revoke must not report success when it did not happen ─────────────────


@pytest.mark.asyncio
async def test_db_revoke_failure_raises_instead_of_returning_none() -> None:
    svc = TenantService(db_session_factory=_failing_db())
    with pytest.raises(KeyStoreUnavailableError):
        await svc._db_revoke_api_key("kid", "tid")


@pytest.mark.asyncio
async def test_revoke_failure_keeps_in_memory_key_active_and_raises() -> None:
    svc = TenantService()
    t = await svc.create_tenant(name="Acme", email="rv@acme.test")
    kid = t["api_key_id"]
    svc._db = _failing_db()
    with pytest.raises(KeyStoreUnavailableError):
        await svc.revoke_api_key(t["tenant_id"], kid)
    # Nothing was revoked, so nothing may claim it was.
    assert svc._keys[kid]["is_active"] is True


def test_revoke_route_returns_503_when_store_fails() -> None:
    svc = AsyncMock()
    svc.revoke_api_key.side_effect = KeyStoreUnavailableError("key store down")
    client = TestClient(_app(svc), raise_server_exceptions=False)
    resp = client.delete("/tenants/me/keys/kid-x", headers={"X-API-Key": _KEY})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "KEY_STORE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_revoke_invalidates_shared_cache_so_other_replicas_reject() -> None:
    """Replica B cached the key context in the shared Redis; replica A revokes.

    B's next resolve must not be served from the stale cache entry."""
    redis = fakeredis.FakeAsyncRedis()
    pod_a = TenantService()
    pod_a.set_redis(redis)
    t = await pod_a.create_tenant(name="Acme", email="pods@acme.test")
    raw = t["api_key"]

    pod_b = TenantService()
    pod_b.set_redis(redis)
    # Simulate B having served (and cached) this key before the revoke.
    await pod_a.resolve_api_key(raw)
    assert await redis.get(f"api_key:{_hash_key(raw)}") is not None
    assert await pod_b.resolve_api_key(raw) is not None  # served from shared cache

    await pod_a.revoke_api_key(t["tenant_id"], t["api_key_id"])
    assert await redis.get(f"api_key:{_hash_key(raw)}") is None
    assert await pod_b.resolve_api_key(raw) is None


@pytest.mark.asyncio
async def test_revoke_reports_failure_when_shared_cache_cannot_be_invalidated() -> None:
    svc = TenantService()
    t = await svc.create_tenant(name="Acme", email="cache@acme.test")
    broken = MagicMock()
    broken.delete = AsyncMock(side_effect=ConnectionError("redis down"))
    svc.set_redis(broken)
    with pytest.raises(KeyStoreUnavailableError):
        await svc.revoke_api_key(t["tenant_id"], t["api_key_id"])


@pytest.mark.asyncio
async def test_create_key_persist_failure_raises_and_leaves_no_ghost_key() -> None:
    svc = TenantService()
    t = await svc.create_tenant(name="Acme", email="ghost@acme.test")
    before = set(svc._keys)
    svc._db = _failing_db()
    with pytest.raises(KeyStoreUnavailableError):
        await svc.create_api_key(t["tenant_id"], "k", ["goals:read"])
    assert set(svc._keys) == before


def test_rotate_reports_old_key_not_revoked_on_failure() -> None:
    svc = AsyncMock()
    svc.create_api_key.return_value = {"key_id": "new", "raw_key": "r"}
    svc.revoke_api_key.side_effect = KeyStoreUnavailableError("down")
    client = TestClient(_app(svc), raise_server_exceptions=False)
    resp = client.post(
        "/tenants/me/keys/old/rotate", json={"revoke_old": True}, headers={"X-API-Key": _KEY}
    )
    assert resp.status_code == 201
    assert resp.json()["old_revoked"] is False
    assert "revoke_error" in resp.json()


def test_restricted_key_cannot_mint_a_broader_key() -> None:
    narrow = TenantContext(
        tenant_id="tid-rv",
        plan=PlanTier.STARTER,
        api_key_id="kid-narrow",
        roles=("admin",),
        scopes=("tenancy:write", "tenancy:read"),
    )
    svc = AsyncMock()
    svc.create_api_key.return_value = {"key_id": "k"}
    client = TestClient(_app(svc, ctx=narrow), raise_server_exceptions=False)
    h = {"X-API-Key": _KEY}

    resp = client.post("/tenants/me/keys", json={"name": "x", "scopes": ["goals:write"]}, headers=h)
    assert resp.status_code == 403
    svc.create_api_key.assert_not_called()

    # No scopes requested → the new key inherits the caller's restriction.
    resp = client.post("/tenants/me/keys", json={"name": "x"}, headers=h)
    assert resp.status_code == 201
    assert svc.create_api_key.call_args.kwargs["scopes"] == ["tenancy:write", "tenancy:read"]


# ── 2. IP allowlist API uses the enforced table + invalidates the cache ───────


class _RecordingSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def execute(self, *_a: Any, **_k: Any) -> Any:
        return MagicMock()

    @asynccontextmanager
    async def begin(self) -> Any:
        yield self


def _recording_db(session: _RecordingSession) -> Any:
    @asynccontextmanager
    async def _db() -> Any:
        yield session

    return _db


def test_create_allowlist_entry_writes_the_enforced_table() -> None:
    session = _RecordingSession()
    client = TestClient(_app(AsyncMock(), db=_recording_db(session)))
    resp = client.post(
        "/tenants/me/ip-allowlist",
        json={"cidr": "10.0.0.0/8", "description": "office"},
        headers={"X-API-Key": _KEY},
    )
    assert resp.status_code == 201
    assert len(session.added) == 1
    row = session.added[0]
    assert isinstance(row, IPAllowlistEntry)
    assert IPAllowlistEntry.__tablename__ == "ip_allowlist_entries"
    assert (row.tenant_id, row.cidr, row.label, row.is_active) == (
        "tid-rv",
        "10.0.0.0/8",
        "office",
        True,
    )


@pytest.mark.asyncio
async def test_create_allowlist_entry_invalidates_enforcement_cache() -> None:
    redis = fakeredis.FakeAsyncRedis()
    # Enforcement had cached "no allowlist" for this tenant (60 s TTL).
    await redis.setex("ip_wl:tid-rv", 60, json.dumps([]))
    scope_enforcement._local_ip_allowlist_cache["tid-rv"] = (["1.2.3.4/32"], 0.0)
    session = _RecordingSession()
    client = TestClient(_app(AsyncMock(), db=_recording_db(session), redis=redis))
    resp = client.post(
        "/tenants/me/ip-allowlist", json={"cidr": "10.0.0.0/8"}, headers={"X-API-Key": _KEY}
    )
    assert resp.status_code == 201
    assert await redis.get("ip_wl:tid-rv") is None
    assert "tid-rv" not in scope_enforcement._local_ip_allowlist_cache


@pytest.mark.asyncio
async def test_delete_allowlist_entry_invalidates_enforcement_cache() -> None:
    redis = fakeredis.FakeAsyncRedis()
    await redis.setex("ip_wl:tid-rv", 60, json.dumps(["10.0.0.0/8"]))
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = MagicMock()
    session.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def _begin() -> Any:
        yield session

    session.begin = _begin

    @asynccontextmanager
    async def _db() -> Any:
        yield session

    client = TestClient(_app(AsyncMock(), db=_db, redis=redis))
    resp = client.delete("/tenants/me/ip-allowlist/e1", headers={"X-API-Key": _KEY})
    assert resp.status_code == 204
    session.delete.assert_awaited_once()
    assert await redis.get("ip_wl:tid-rv") is None
