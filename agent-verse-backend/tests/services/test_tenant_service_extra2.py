"""Extra coverage tests for app/services/tenant_service.py — targeting 85%+ coverage."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.errors import ConflictError, NotFoundError
from app.services.tenant_service import TenantService, _generate_raw_key, _hash_key
from app.tenancy.context import PlanTier, TenantContext


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def test_hash_key_deterministic():
    key = "my-secret-key"
    assert _hash_key(key) == _hash_key(key)
    assert len(_hash_key(key)) == 64  # SHA-256 hex


def test_generate_raw_key_has_plan_prefix():
    key = _generate_raw_key("enterprise")
    assert key.startswith("av_enterprise_")


def test_generate_raw_key_default_plan():
    key = _generate_raw_key()
    assert key.startswith("av_free_")


# ---------------------------------------------------------------------------
# create_tenant / get_tenant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_tenant_success():
    svc = TenantService()
    result = await svc.create_tenant("Acme Corp", "acme@example.com")
    assert result["name"] == "Acme Corp"
    assert result["email"] == "acme@example.com"
    assert result["plan"] == "free"
    assert result["api_key"].startswith("av_")
    assert len(result["tenant_id"]) == 32


@pytest.mark.asyncio
async def test_create_tenant_duplicate_email_raises():
    svc = TenantService()
    await svc.create_tenant("First", "dup@example.com")
    with pytest.raises(ConflictError):
        await svc.create_tenant("Second", "dup@example.com")


@pytest.mark.asyncio
async def test_get_tenant_not_found_raises():
    svc = TenantService()
    with pytest.raises(NotFoundError):
        await svc.get_tenant("nonexistent-tenant-id")


@pytest.mark.asyncio
async def test_get_tenant_success():
    svc = TenantService()
    created = await svc.create_tenant("Tenant A", "a@example.com")
    tenant = await svc.get_tenant(created["tenant_id"])
    assert tenant["name"] == "Tenant A"


# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_api_keys_empty():
    svc = TenantService()
    keys = await svc.list_api_keys("no-tenant")
    assert keys == []


@pytest.mark.asyncio
async def test_list_api_keys_after_create():
    svc = TenantService()
    created = await svc.create_tenant("Keyed Tenant", "keyed@example.com")
    tid = created["tenant_id"]
    keys = await svc.list_api_keys(tid)
    assert len(keys) == 1
    assert keys[0]["name"] == "Default"
    # Raw key must never appear
    for k in keys:
        assert "key_hash" not in k
        assert "raw_key" not in k


@pytest.mark.asyncio
async def test_create_api_key_tenant_not_found():
    svc = TenantService()
    with pytest.raises(NotFoundError):
        await svc.create_api_key("ghost-tid", "MyKey", [])


@pytest.mark.asyncio
async def test_create_api_key_success():
    svc = TenantService()
    created = await svc.create_tenant("KeyCreator", "kc@example.com")
    tid = created["tenant_id"]
    result = await svc.create_api_key(tid, "Analytics Key", ["read"])
    assert result["name"] == "Analytics Key"
    assert result["scopes"] == ["read"]
    assert result["raw_key"].startswith("av_")
    assert result["is_active"] is True


@pytest.mark.asyncio
async def test_create_api_key_with_expiry():
    svc = TenantService()
    created = await svc.create_tenant("ExpiryTenant", "exp@example.com")
    tid = created["tenant_id"]
    expires = datetime.now(UTC) + timedelta(days=30)
    result = await svc.create_api_key(tid, "Expiring Key", [], expires_at=expires)
    assert result["expires_at"] is not None


@pytest.mark.asyncio
async def test_revoke_api_key_success():
    svc = TenantService()
    created = await svc.create_tenant("RevokerTenant", "rev@example.com")
    tid = created["tenant_id"]
    kid = created["api_key_id"]
    await svc.revoke_api_key(tid, kid)
    # Key should now be inactive — resolve returns None
    result = await svc.resolve_api_key(created["api_key"])
    assert result is None


@pytest.mark.asyncio
async def test_revoke_api_key_not_found():
    svc = TenantService()
    created = await svc.create_tenant("RevokeFail", "rf@example.com")
    tid = created["tenant_id"]
    with pytest.raises(NotFoundError):
        await svc.revoke_api_key(tid, "ghost-key-id")


@pytest.mark.asyncio
async def test_revoke_api_key_wrong_tenant():
    svc = TenantService()
    t1 = await svc.create_tenant("T1", "t1@example.com")
    t2 = await svc.create_tenant("T2", "t2@example.com")
    with pytest.raises(NotFoundError):
        await svc.revoke_api_key(t2["tenant_id"], t1["api_key_id"])


# ---------------------------------------------------------------------------
# resolve_api_key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_api_key_valid():
    svc = TenantService()
    created = await svc.create_tenant("ResolveTenant", "resolve@example.com")
    ctx = await svc.resolve_api_key(created["api_key"])
    assert ctx is not None
    assert ctx.tenant_id == created["tenant_id"]


@pytest.mark.asyncio
async def test_resolve_api_key_invalid():
    svc = TenantService()
    ctx = await svc.resolve_api_key("av_free_notavalidkey")
    assert ctx is None


@pytest.mark.asyncio
async def test_resolve_api_key_expired():
    svc = TenantService()
    created = await svc.create_tenant("ExpiredTenant", "expired@example.com")
    tid = created["tenant_id"]
    # Create a key that expired yesterday
    expires = datetime.now(UTC) - timedelta(days=1)
    new_key = await svc.create_api_key(tid, "Expired", [], expires_at=expires)
    ctx = await svc.resolve_api_key(new_key["raw_key"])
    assert ctx is None


@pytest.mark.asyncio
async def test_resolve_api_key_with_redis_cache_hit():
    svc = TenantService()
    created = await svc.create_tenant("CacheTenant", "cache@example.com")
    tid = created["tenant_id"]

    cached_data = json.dumps({
        "tenant_id": tid,
        "plan": "free",
        "api_key_id": created["api_key_id"],
        "roles": ["admin"],
    })
    mock_redis = AsyncMock()
    mock_redis.get.return_value = cached_data
    svc._redis = mock_redis

    ctx = await svc.resolve_api_key(created["api_key"])
    assert ctx is not None
    assert ctx.tenant_id == tid
    mock_redis.get.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_api_key_with_redis_cache_miss_then_populate():
    svc = TenantService()
    created = await svc.create_tenant("CachePopulateTenant", "cp@example.com")

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # Cache miss
    mock_redis.setex = AsyncMock()
    svc._redis = mock_redis

    ctx = await svc.resolve_api_key(created["api_key"])
    assert ctx is not None
    # Cache should have been populated
    mock_redis.setex.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_api_key_redis_error_falls_through():
    svc = TenantService()
    created = await svc.create_tenant("RedisErrorTenant", "re@example.com")

    mock_redis = AsyncMock()
    mock_redis.get.side_effect = RuntimeError("Redis connection refused")
    svc._redis = mock_redis

    # Should fall through to in-memory lookup
    ctx = await svc.resolve_api_key(created["api_key"])
    assert ctx is not None


@pytest.mark.asyncio
async def test_resolve_api_key_inactive_key():
    svc = TenantService()
    created = await svc.create_tenant("InactiveTenant", "inactive@example.com")
    tid = created["tenant_id"]
    kid = created["api_key_id"]

    # Manually deactivate without going through revoke
    svc._keys[kid]["is_active"] = False

    ctx = await svc.resolve_api_key(created["api_key"])
    assert ctx is None


# ---------------------------------------------------------------------------
# get_tenant_by_sso_sub
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_tenant_by_sso_sub_found_in_memory():
    svc = TenantService()
    svc._tenants["t1"] = {
        "tenant_id": "t1",
        "name": "SSO User",
        "email": "sso@example.com",
        "plan": "starter",
        "sso_sub": "sub:abc123",
    }
    result = await svc.get_tenant_by_sso_sub(sso_sub="sub:abc123")
    assert result is not None
    assert result["tenant_id"] == "t1"


@pytest.mark.asyncio
async def test_get_tenant_by_sso_sub_not_found():
    svc = TenantService()
    result = await svc.get_tenant_by_sso_sub(sso_sub="sub:nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_tenant_by_sso_sub_db_lookup():
    """When not in memory, queries DB."""
    from contextlib import asynccontextmanager

    mock_row = ("tid-db", "DB User", "dbuser@example.com", "free", "sub:dbuser")
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=mock_row)))

    @asynccontextmanager
    async def _db():
        yield mock_session

    svc = TenantService(db_session_factory=_db)
    result = await svc.get_tenant_by_sso_sub(sso_sub="sub:dbuser")
    assert result is not None
    assert result["tenant_id"] == "tid-db"


@pytest.mark.asyncio
async def test_get_tenant_by_sso_sub_db_exception():
    from contextlib import asynccontextmanager

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))

    @asynccontextmanager
    async def _db():
        yield mock_session

    svc = TenantService(db_session_factory=_db)
    result = await svc.get_tenant_by_sso_sub(sso_sub="sub:dberror")
    assert result is None


# ---------------------------------------------------------------------------
# get_key_by_sso_sub
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_key_by_sso_sub_with_key_record():
    svc = TenantService()
    svc._tenants["t1"] = {
        "tenant_id": "t1",
        "name": "SSO User",
        "email": "s@x.com",
        "plan": "free",
        "sso_sub": "sub:t1",
        "api_key_id": "kid-1",
    }
    svc._keys["kid-1"] = {
        "key_id": "kid-1",
        "tenant_id": "t1",
        "name": "SSO auto",
        "is_active": True,
    }
    result = await svc.get_key_by_sso_sub(sso_sub="sub:t1")
    assert result is not None
    assert result["key_id"] == "kid-1"


@pytest.mark.asyncio
async def test_get_key_by_sso_sub_minimal_record():
    """When key is not in _keys, returns minimal record from tenant."""
    svc = TenantService()
    svc._tenants["t2"] = {
        "tenant_id": "t2",
        "name": "SSO User2",
        "email": "s2@x.com",
        "plan": "free",
        "sso_sub": "sub:t2",
        "api_key_id": "kid-ghost",
    }
    # kid-ghost not in _keys
    result = await svc.get_key_by_sso_sub(sso_sub="sub:t2")
    assert result is not None
    assert result["key_id"] == "kid-ghost"


@pytest.mark.asyncio
async def test_get_key_by_sso_sub_no_api_key_id():
    """When tenant has no api_key_id, returns None."""
    svc = TenantService()
    svc._tenants["t3"] = {
        "tenant_id": "t3",
        "sso_sub": "sub:t3",
    }
    result = await svc.get_key_by_sso_sub(sso_sub="sub:t3")
    assert result is None


@pytest.mark.asyncio
async def test_get_key_by_sso_sub_not_found():
    svc = TenantService()
    result = await svc.get_key_by_sso_sub(sso_sub="sub:unknown")
    assert result is None


# ---------------------------------------------------------------------------
# create_tenant_from_sso
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_tenant_from_sso_no_db():
    svc = TenantService()
    result = await svc.create_tenant_from_sso(
        sso_sub="sub:jit123",
        email="jit@example.com",
        name="JIT User",
        plan="starter",
    )
    assert result["email"] == "jit@example.com"
    assert result["sso_sub"] == "sub:jit123"
    assert result["plan"] == "starter"
    # Should be in in-memory store
    assert result["tenant_id"] in svc._tenants


@pytest.mark.asyncio
async def test_create_tenant_from_sso_enterprise_plan():
    svc = TenantService()
    result = await svc.create_tenant_from_sso(
        sso_sub="sub:ent",
        email="ent@example.com",
        name="Enterprise JIT",
        plan="enterprise",
    )
    assert result["plan"] == "enterprise"


@pytest.mark.asyncio
async def test_create_tenant_from_sso_unknown_plan_defaults_starter():
    svc = TenantService()
    result = await svc.create_tenant_from_sso(
        sso_sub="sub:unk",
        email="unk@example.com",
        name="Unknown Plan",
        plan="unknown_plan",
    )
    assert result["plan"] == "starter"


# ---------------------------------------------------------------------------
# sync_from_db
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_from_db_no_db():
    svc = TenantService()
    count = await svc.sync_from_db()
    assert count == 0


@pytest.mark.asyncio
async def test_sync_from_db_exception_returns_zero():
    from contextlib import asynccontextmanager

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))

    @asynccontextmanager
    async def _db():
        yield mock_session

    svc = TenantService(db_session_factory=_db)
    count = await svc.sync_from_db()
    assert count == 0


# ---------------------------------------------------------------------------
# DB helper methods (no-op path when db is None)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_db_create_tenant_no_db():
    svc = TenantService()
    # Should be a no-op (no exception)
    await svc._db_create_tenant("tid", "Name", "email@x.com", "free")


@pytest.mark.asyncio
async def test_db_create_tenant_with_api_key_no_db():
    svc = TenantService()
    await svc._db_create_tenant_with_api_key(
        tenant_id="tid", name="N", email="e@x.com", plan="free",
        key_id="kid", key_hash="hash",
    )


@pytest.mark.asyncio
async def test_db_create_api_key_no_db():
    svc = TenantService()
    await svc._db_create_api_key("kid", "tid", "name", "hash", [], None)


@pytest.mark.asyncio
async def test_db_revoke_api_key_no_db():
    svc = TenantService()
    await svc._db_revoke_api_key("kid", "tid")


# ---------------------------------------------------------------------------
# resolve_api_key — tenant deleted after key created
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_api_key_tenant_missing():
    svc = TenantService()
    created = await svc.create_tenant("GhostTenant", "ghost@example.com")
    # Delete tenant from memory
    del svc._tenants[created["tenant_id"]]
    ctx = await svc.resolve_api_key(created["api_key"])
    assert ctx is None


# ---------------------------------------------------------------------------
# Key with explicit roles
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_api_key_with_roles():
    svc = TenantService()
    created = await svc.create_tenant("RoleTenant", "roles@example.com")
    tid = created["tenant_id"]
    key_result = await svc.create_api_key(tid, "Admin Key", [], expires_at=None)
    # Set roles on the key
    svc._keys[key_result["key_id"]]["roles"] = ["admin", "operator"]
    ctx = await svc.resolve_api_key(key_result["raw_key"])
    assert ctx is not None
    assert "admin" in ctx.roles


# ---------------------------------------------------------------------------
# resolve_api_key — expiry without timezone (naive datetime path)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_api_key_expiry_no_tz():
    """When expires_at is stored as naive ISO string, tz is added before comparison."""
    svc = TenantService()
    created = await svc.create_tenant("NaiveTZTenant", "naivetz@example.com")
    tid = created["tenant_id"]
    # Create key expiring yesterday
    past = datetime.now(UTC) - timedelta(days=1)
    new_key = await svc.create_api_key(tid, "NaiveTZ", [], expires_at=past)

    # Strip timezone from stored value to simulate naive datetime string
    svc._keys[new_key["key_id"]]["expires_at"] = past.replace(tzinfo=None).isoformat()

    ctx = await svc.resolve_api_key(new_key["raw_key"])
    assert ctx is None


# ---------------------------------------------------------------------------
# resolve_api_key — Redis setex failure doesn't break resolution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_api_key_redis_setex_failure():
    svc = TenantService()
    created = await svc.create_tenant("RedisSetexFail", "rsf@example.com")

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # Cache miss
    mock_redis.setex = AsyncMock(side_effect=RuntimeError("Redis write failed"))
    svc._redis = mock_redis

    ctx = await svc.resolve_api_key(created["api_key"])
    assert ctx is not None  # Resolution succeeds despite cache write failure


# ---------------------------------------------------------------------------
# DB persistence helpers — with working DB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_db_create_tenant_with_db():
    from contextlib import asynccontextmanager

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    @asynccontextmanager
    async def _db():
        yield mock_session

    svc = TenantService(db_session_factory=_db)
    # Should not raise — DB path executes
    await svc._db_create_tenant("tid", "Name", "e@x.com", "free")


@pytest.mark.asyncio
async def test_db_create_api_key_with_db():
    from contextlib import asynccontextmanager

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    @asynccontextmanager
    async def _db():
        yield mock_session

    svc = TenantService(db_session_factory=_db)
    await svc._db_create_api_key("kid", "tid", "MyKey", "keyhash", ["read"], None)


@pytest.mark.asyncio
async def test_db_revoke_api_key_with_db():
    from contextlib import asynccontextmanager

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    @asynccontextmanager
    async def _db():
        yield mock_session

    svc = TenantService(db_session_factory=_db)
    await svc._db_revoke_api_key("kid", "tid")
    assert mock_session.execute.called


@pytest.mark.asyncio
async def test_db_create_tenant_exception_handled():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _db():
        raise RuntimeError("DB down")
        yield  # pragma: no cover

    svc = TenantService(db_session_factory=_db)
    # Should not raise — logged as warning
    await svc._db_create_tenant("tid", "Name", "e@x.com", "free")


# ---------------------------------------------------------------------------
# create_tenant_from_sso — with DB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_tenant_from_sso_with_db():
    from contextlib import asynccontextmanager

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    @asynccontextmanager
    async def _db():
        yield mock_session

    svc = TenantService(db_session_factory=_db)
    result = await svc.create_tenant_from_sso(
        sso_sub="sub:withdb",
        email="withdb@example.com",
        name="With DB User",
        plan="professional",
    )
    assert result["email"] == "withdb@example.com"
    assert result["plan"] == "professional"
    # DB execute should have been called
    assert mock_session.execute.call_count >= 1


@pytest.mark.asyncio
async def test_create_tenant_from_sso_db_exception():
    """DB exception during SSO creation is handled gracefully."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _db():
        raise RuntimeError("DB unavailable")
        yield  # pragma: no cover

    svc = TenantService(db_session_factory=_db)
    result = await svc.create_tenant_from_sso(
        sso_sub="sub:dberr",
        email="dberr@example.com",
        name="DB Error User",
    )
    # Should still succeed with in-memory data
    assert result["email"] == "dberr@example.com"
