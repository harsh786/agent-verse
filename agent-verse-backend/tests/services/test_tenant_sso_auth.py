"""SVC-13: SSO auth must honour the tenant's is_active from shared state.

get_tenant_by_sso_sub used to answer from the provisioning pod's memory before
the DB query that filters ``is_active``, so a deactivated tenant's SSO users kept
logging in on that pod; get_key_by_sso_sub was memory-only, so every other pod
issued contexts with a ghost ``sso:{sub}`` api_key_id.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.tenant_service import KeyStoreUnavailableError, TenantService


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.fail_delete = False

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        if key.startswith("sso_sub:"):
            assert ttl <= 60, "SSO auth cache must be short-lived"
        self.data[key] = value

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value

    async def delete(self, *keys: str) -> int:
        if self.fail_delete:
            raise ConnectionError("redis down")
        n = 0
        for k in keys:
            n += 1 if self.data.pop(k, None) is not None else 0
        return n


def _db_returning(rows: list[Any]) -> Any:
    """A session factory whose successive execute() calls return *rows* in order."""
    results = iter(rows)
    session = AsyncMock()

    async def _execute(*_a: Any, **_k: Any) -> Any:
        row = next(results, None)
        res = MagicMock()
        res.fetchone = MagicMock(return_value=row)
        res.first = MagicMock(return_value=row)
        res.scalar_one_or_none = MagicMock(return_value=row[0] if row else None)
        res.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        return res

    session.execute = AsyncMock(side_effect=_execute)

    @asynccontextmanager
    async def _begin() -> AsyncIterator[None]:
        yield None

    session.begin = MagicMock(side_effect=lambda: _begin())

    @asynccontextmanager
    async def _factory() -> AsyncIterator[Any]:
        yield session

    return _factory


async def test_db_mode_ignores_pod_memory_for_deactivated_tenant() -> None:
    """The provisioning pod still holds the tenant in memory; the DB says it is
    inactive (no row matches ``is_active``) -> not found, so the login is refused."""
    svc = TenantService(db_session_factory=_db_returning([None]))
    svc._tenants["t1"] = {
        "tenant_id": "t1",
        "name": "SSO",
        "email": "s@x.com",
        "plan": "free",
        "sso_sub": "kc|dead",
        "api_key_id": "k1",
    }
    assert await svc.get_tenant_by_sso_sub(sso_sub="kc|dead") is None
    assert await svc.get_key_by_sso_sub(sso_sub="kc|dead") is None


async def test_db_mode_returns_real_primary_key_id() -> None:
    """A pod that never provisioned the tenant gets the persisted key id from the DB."""
    tenant_row = ("t2", "SSO", "s2@x.com", "starter", "kc|live")
    svc = TenantService(db_session_factory=_db_returning([tenant_row, None, ("key-real",)]))
    rec = await svc.get_tenant_by_sso_sub(sso_sub="kc|live")
    assert rec is not None
    assert rec["tenant_id"] == "t2"
    assert rec["plan"] == "starter"
    assert rec["api_key_id"] == "key-real"


async def test_db_mode_lookup_is_cached_briefly_in_redis() -> None:
    tenant_row = ("t3", "SSO", "s3@x.com", "free", "kc|c")
    svc = TenantService(db_session_factory=_db_returning([tenant_row, None, ("k3",)]))
    redis = _FakeRedis()
    svc.set_redis(redis)
    first = await svc.get_tenant_by_sso_sub(sso_sub="kc|c")
    # The DB factory is exhausted: a second lookup must come from the cache.
    second = await svc.get_tenant_by_sso_sub(sso_sub="kc|c")
    assert first == second
    assert any(json.loads(v).get("tenant_id") == "t3" for v in redis.data.values())


async def test_deactivate_in_memory_then_sso_login_is_refused() -> None:
    svc = TenantService()
    t = await svc.create_tenant_from_sso(sso_sub="kc|mem", email="m@x.com", name="M")
    assert (await svc.get_tenant_by_sso_sub(sso_sub="kc|mem"))["tenant_id"] == t["tenant_id"]

    await svc.deactivate_tenant(t["tenant_id"])

    assert await svc.get_tenant_by_sso_sub(sso_sub="kc|mem") is None
    assert await svc.get_key_by_sso_sub(sso_sub="kc|mem") is None
    assert await svc.resolve_api_key(t["api_key"]) is None

    from app.auth.keycloak import resolve_tenant_from_jwt

    payload = {"sub": "kc|mem", "email": "m@x.com", "realm_access": {"roles": ["admin"]}}
    with patch("app.auth.keycloak.validate_jwt", AsyncMock(return_value=payload)):
        assert await resolve_tenant_from_jwt("tok", svc) is None


async def test_deactivate_clears_shared_auth_caches() -> None:
    svc = TenantService()
    redis = _FakeRedis()
    svc.set_redis(redis)
    t = await svc.create_tenant_from_sso(sso_sub="kc|r", email="r@x.com", name="R")
    assert await svc.resolve_api_key(t["api_key"]) is not None  # populates api_key:{hash}
    assert any(k.startswith("api_key:") for k in redis.data)

    await svc.deactivate_tenant(t["tenant_id"])

    assert not any(k.startswith(("api_key:", "sso_sub:", "tenant:")) for k in redis.data)


async def test_deactivate_reports_failed_cache_invalidation() -> None:
    svc = TenantService()
    redis = _FakeRedis()
    svc.set_redis(redis)
    t = await svc.create_tenant_from_sso(sso_sub="kc|f", email="f@x.com", name="F")
    redis.fail_delete = True
    with pytest.raises(KeyStoreUnavailableError):
        await svc.deactivate_tenant(t["tenant_id"])
