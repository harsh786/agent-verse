"""Regression tests: MFA state and sessions fail closed and work across replicas.

1. ``MFAStore.get`` swallowed DB / decrypt errors and returned the (empty)
   cache entry — ``enabled: False`` — so enforcement switched itself off.
2. ``MFAStore.save`` updated the cache first and swallowed DB errors.
3. X-MFA-Token sessions lived in a per-process dict: a token minted on one
   replica was rejected by every other one.
4. With enforcement on, ``/auth/mfa/verify`` itself demanded an X-MFA-Token, so
   a tenant with MFA enabled could never obtain one.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import mfa
from app.api.mfa import (
    MFAStateUnavailableError,
    MFAStore,
    _mfa_db_store,
    _mfa_verified_sessions,
    check_mfa_session,
    issue_mfa_session,
)
from app.services.tenant_service import TenantService
from app.tenancy.middleware import TenantMiddleware


def _failing_factory() -> Any:
    session = MagicMock()
    session.__aenter__ = AsyncMock(side_effect=RuntimeError("db down"))
    session.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=session)


@pytest.fixture(autouse=True)
def _reset() -> Any:
    _mfa_db_store._cache.clear()
    _mfa_verified_sessions.clear()
    saved_db = _mfa_db_store._db
    yield
    _mfa_db_store._db = saved_db
    _mfa_db_store._cache.clear()
    _mfa_verified_sessions.clear()


@pytest.mark.asyncio
async def test_get_raises_on_db_error_instead_of_reading_disabled() -> None:
    store = MFAStore()
    store.set_db(_failing_factory())
    with pytest.raises(MFAStateUnavailableError):
        await store.get("t1")


@pytest.mark.asyncio
async def test_save_raises_on_db_error_and_leaves_cache_untouched() -> None:
    store = MFAStore()
    store.set_db(_failing_factory())
    with pytest.raises(MFAStateUnavailableError):
        await store.save("t1", {"enabled": True, "secret": "S", "recovery_codes_hashed": []})
    assert "t1" not in store._cache


@pytest.mark.asyncio
async def test_sessions_are_shared_across_replicas_via_redis() -> None:
    redis = fakeredis.FakeAsyncRedis()
    replica_a = SimpleNamespace(state=SimpleNamespace(_redis=redis))
    replica_b = SimpleNamespace(state=SimpleNamespace(_redis=redis))
    token = await issue_mfa_session(replica_a, "t1", "totp")
    assert await check_mfa_session(replica_b, token, "t1") == "valid"
    assert await check_mfa_session(replica_b, token, "other-tenant") == "invalid"
    assert _mfa_verified_sessions == {}, "no process-local copy when Redis is wired"
    keys = [k.decode() for k in await redis.keys("*")]
    assert keys and all(token not in k for k in keys), "only a digest of the token is stored"
    ttl = await redis.ttl(keys[0])
    assert 0 < ttl <= 3600


@pytest.mark.asyncio
async def test_session_lookup_redis_error_raises() -> None:
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
    app = SimpleNamespace(state=SimpleNamespace(_redis=redis))
    with pytest.raises(MFAStateUnavailableError):
        await check_mfa_session(app, "tok", "t1")


# ── enforcement middleware ───────────────────────────────────────────────────


async def _app_with_mfa_tenant(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, str, str]:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "mfa_enforcement_enabled", True)
    svc = TenantService()
    t = await svc.create_tenant(name="Acme", email="mfa@acme.test")
    key = await svc.create_api_key(tenant_id=t["tenant_id"], name="k", scopes=[])
    app = FastAPI()
    app.state.tenant_service = svc
    app.add_middleware(TenantMiddleware, key_resolver=svc.resolve_api_key)
    app.include_router(mfa.router)

    @app.get("/goals")
    async def goals() -> dict[str, str]:
        return {"ok": "goals"}

    return TestClient(app, raise_server_exceptions=False), t["tenant_id"], key["raw_key"]


@pytest.mark.asyncio
async def test_verify_is_reachable_without_a_token_and_its_token_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, tid, raw = await _app_with_mfa_tenant(monkeypatch)
    secret = pyotp.random_base32()
    _mfa_db_store._cache[tid] = {
        "enabled": True,
        "secret": secret,
        "pending_secret": None,
        "recovery_codes_hashed": [],
    }
    h = {"X-API-Key": raw}
    assert client.get("/goals", headers=h).json()["error"]["code"] == "MFA_REQUIRED"
    resp = client.post("/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()}, headers=h)
    assert resp.status_code == 200, resp.text
    token = resp.json()["session_token"]
    assert client.get("/goals", headers={**h, "X-MFA-Token": token}).status_code == 200


@pytest.mark.asyncio
async def test_enforcement_fails_closed_when_mfa_state_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, raw = await _app_with_mfa_tenant(monkeypatch)
    _mfa_db_store.set_db(_failing_factory())
    resp = client.get("/goals", headers={"X-API-Key": raw})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "MFA_UNAVAILABLE"


@pytest.mark.asyncio
async def test_mfa_endpoints_return_503_when_state_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, raw = await _app_with_mfa_tenant(monkeypatch)
    monkeypatch.setattr(
        _mfa_db_store, "get", AsyncMock(side_effect=MFAStateUnavailableError("db down"))
    )
    resp = client.get("/auth/mfa/status", headers={"X-API-Key": raw})
    assert resp.status_code == 503
