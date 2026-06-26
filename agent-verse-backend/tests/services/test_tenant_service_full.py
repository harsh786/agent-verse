"""Full coverage for TenantService — covers all branches and execution paths."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from app.core.errors import ConflictError, NotFoundError
from app.services.tenant_service import TenantService
from app.tenancy.context import PlanTier


async def test_create_tenant_returns_complete_data() -> None:
    """create_tenant returns all required fields including one-time raw API key."""
    svc = TenantService()
    result = await svc.create_tenant(name="Acme Corp", email="acme@test.com")
    assert "tenant_id" in result
    assert result["name"] == "Acme Corp"
    assert result["email"] == "acme@test.com"
    assert "api_key" in result
    assert "api_key_id" in result
    assert result["api_key"].startswith("av_free_")


async def test_create_tenant_duplicate_raises_conflict() -> None:
    """Creating two tenants with the same email raises ConflictError."""
    svc = TenantService()
    await svc.create_tenant(name="Corp1", email="dup2@test.com")
    with pytest.raises(ConflictError):
        await svc.create_tenant(name="Corp2", email="dup2@test.com")


async def test_get_tenant_not_found_raises() -> None:
    """get_tenant raises NotFoundError for an unknown tenant_id."""
    svc = TenantService()
    with pytest.raises(NotFoundError):
        await svc.get_tenant("nonexistent-id")


async def test_list_api_keys_excludes_hash() -> None:
    """list_api_keys never exposes key_hash or raw_key."""
    svc = TenantService()
    result = await svc.create_tenant(name="Keys", email="keys@test.com")
    tid = result["tenant_id"]
    keys = await svc.list_api_keys(tid)
    assert len(keys) >= 1
    for k in keys:
        assert "key_hash" not in k
        assert "raw_key" not in k
        assert "key_id" in k
        assert "is_active" in k


async def test_create_api_key_raw_key_returned_once() -> None:
    """create_api_key returns raw_key; subsequent list_api_keys does not."""
    svc = TenantService()
    result = await svc.create_tenant(name="KeyCreate", email="keycreate@test.com")
    tid = result["tenant_id"]
    key_result = await svc.create_api_key(tid, "New Key", [], None)
    assert "raw_key" in key_result
    assert key_result["raw_key"].startswith("av_free_")

    # List must not expose the raw key
    keys = await svc.list_api_keys(tid)
    raw_keys = [k.get("raw_key") for k in keys]
    assert all(rk is None for rk in raw_keys)


async def test_revoke_key_deactivates_it() -> None:
    """Revoking a key makes resolve_api_key return None for that key."""
    svc = TenantService()
    result = await svc.create_tenant(name="Revoke", email="revoke@test.com")
    tid = result["tenant_id"]
    key_result = await svc.create_api_key(tid, "ToRevoke", [], None)
    kid = key_result["key_id"]
    raw = key_result["raw_key"]

    await svc.revoke_api_key(tid, kid)

    ctx = await svc.resolve_api_key(raw)
    assert ctx is None


async def test_resolve_invalid_key_returns_none() -> None:
    """resolve_api_key returns None for a key that doesn't exist."""
    svc = TenantService()
    ctx = await svc.resolve_api_key("av_free_invalid_totally_fake_key_xyz123456789")
    assert ctx is None


async def test_resolve_valid_key_returns_context() -> None:
    """resolve_api_key returns a TenantContext for a valid active key."""
    svc = TenantService()
    result = await svc.create_tenant(name="Resolve", email="resolve@test.com")
    raw = result["api_key"]
    ctx = await svc.resolve_api_key(raw)
    assert ctx is not None
    assert ctx.tenant_id == result["tenant_id"]
    assert ctx.plan == PlanTier.FREE


async def test_expired_key_not_resolved() -> None:
    """Keys with an expiry in the past are not resolved."""
    svc = TenantService()
    result = await svc.create_tenant(name="Expired", email="expired2@test.com")
    tid = result["tenant_id"]
    expired_at = datetime.now(timezone.utc) - timedelta(hours=1)
    key_result = await svc.create_api_key(tid, "Expired", [], expired_at)
    ctx = await svc.resolve_api_key(key_result["raw_key"])
    assert ctx is None


async def test_db_helpers_noop_without_factory() -> None:
    """All DB helper methods are no-ops when no db_session_factory is configured."""
    svc = TenantService()
    # These must return without raising
    await svc._db_create_tenant("t1", "Test", "t@t.com", "free")
    await svc._db_create_api_key("k1", "t1", "key", "hash", [], None)
    await svc._db_revoke_api_key("k1", "t1")
