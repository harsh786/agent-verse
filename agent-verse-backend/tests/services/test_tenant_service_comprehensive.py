"""Comprehensive tests for app/services/tenant_service.py — targeting 90%+ coverage."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.errors import ConflictError, NotFoundError
from app.services.tenant_service import TenantService, _generate_raw_key, _hash_key
from app.tenancy.context import PlanTier


# ── Utility functions ─────────────────────────────────────────────────────────

class TestUtilFunctions:
    def test_hash_key_sha256(self) -> None:
        h = _hash_key("av_free_test")
        assert len(h) == 64  # SHA-256 hex

    def test_hash_key_deterministic(self) -> None:
        assert _hash_key("same_key") == _hash_key("same_key")

    def test_hash_key_different_inputs(self) -> None:
        assert _hash_key("key_a") != _hash_key("key_b")

    def test_generate_raw_key_prefix(self) -> None:
        key = _generate_raw_key("free")
        assert key.startswith("av_free_")

    def test_generate_raw_key_enterprise(self) -> None:
        key = _generate_raw_key("enterprise")
        assert key.startswith("av_enterprise_")

    def test_generate_raw_key_unique(self) -> None:
        k1 = _generate_raw_key("free")
        k2 = _generate_raw_key("free")
        assert k1 != k2


# ── create_tenant ─────────────────────────────────────────────────────────────

class TestCreateTenant:
    async def test_returns_tenant_and_raw_key(self) -> None:
        svc = TenantService()
        result = await svc.create_tenant("Acme", "admin@acme.com")
        assert "tenant_id" in result
        assert result["name"] == "Acme"
        assert result["email"] == "admin@acme.com"
        assert result["plan"] == "free"
        assert "api_key" in result
        assert result["api_key"].startswith("av_free_")

    async def test_duplicate_email_raises_conflict(self) -> None:
        svc = TenantService()
        await svc.create_tenant("First", "dup@example.com")
        with pytest.raises(ConflictError, match="already registered"):
            await svc.create_tenant("Second", "dup@example.com")

    async def test_email_case_insensitive(self) -> None:
        svc = TenantService()
        await svc.create_tenant("A", "User@EXAMPLE.COM")
        with pytest.raises(ConflictError):
            await svc.create_tenant("B", "user@example.com")

    async def test_tenant_ids_unique(self) -> None:
        svc = TenantService()
        r1 = await svc.create_tenant("A", "a@example.com")
        r2 = await svc.create_tenant("B", "b@example.com")
        assert r1["tenant_id"] != r2["tenant_id"]

    async def test_raw_key_not_stored(self) -> None:
        svc = TenantService()
        result = await svc.create_tenant("Corp", "corp@corp.com")
        tid = result["tenant_id"]
        keys = await svc.list_api_keys(tid)
        for key in keys:
            assert "raw_key" not in key
            assert "key_hash" not in key

    async def test_initial_key_is_admin(self) -> None:
        svc = TenantService()
        result = await svc.create_tenant("Corp", "corp2@corp.com")
        tid = result["tenant_id"]
        raw_key = result["api_key"]
        ctx = await svc.resolve_api_key(raw_key)
        assert ctx is not None
        assert "admin" in ctx.roles


# ── get_tenant ────────────────────────────────────────────────────────────────

class TestGetTenant:
    async def test_get_existing_tenant(self) -> None:
        svc = TenantService()
        created = await svc.create_tenant("Corp", "info@corp.com")
        tenant = await svc.get_tenant(created["tenant_id"])
        assert tenant["name"] == "Corp"

    async def test_get_nonexistent_raises(self) -> None:
        svc = TenantService()
        with pytest.raises(NotFoundError):
            await svc.get_tenant("nonexistent-id")


# ── list_api_keys ─────────────────────────────────────────────────────────────

class TestListApiKeys:
    async def test_lists_initial_key(self) -> None:
        svc = TenantService()
        created = await svc.create_tenant("Corp", "list@corp.com")
        keys = await svc.list_api_keys(created["tenant_id"])
        assert len(keys) >= 1

    async def test_empty_for_unknown_tenant(self) -> None:
        svc = TenantService()
        keys = await svc.list_api_keys("no-such-tenant")
        assert keys == []

    async def test_key_record_fields(self) -> None:
        svc = TenantService()
        created = await svc.create_tenant("Corp", "fields@corp.com")
        keys = await svc.list_api_keys(created["tenant_id"])
        key = keys[0]
        assert "key_id" in key
        assert "name" in key
        assert "scopes" in key
        assert "is_active" in key
        assert "created_at" in key


# ── create_api_key ────────────────────────────────────────────────────────────

class TestCreateApiKey:
    async def test_creates_new_key(self) -> None:
        svc = TenantService()
        created = await svc.create_tenant("Corp", "newkey@corp.com")
        tid = created["tenant_id"]
        key = await svc.create_api_key(
            tid, name="CI Key", scopes=["read:goals"]
        )
        assert key["name"] == "CI Key"
        assert key["scopes"] == ["read:goals"]
        assert "raw_key" in key

    async def test_create_key_nonexistent_tenant_raises(self) -> None:
        svc = TenantService()
        with pytest.raises(NotFoundError):
            await svc.create_api_key("no-such-tenant", "Key", [])

    async def test_create_key_with_expiry(self) -> None:
        svc = TenantService()
        created = await svc.create_tenant("Corp", "expiry@corp.com")
        expiry = datetime.now(UTC) + timedelta(days=30)
        key = await svc.create_api_key(
            created["tenant_id"], name="Temp Key", scopes=[], expires_at=expiry
        )
        assert key["expires_at"] is not None

    async def test_created_key_is_resolvable(self) -> None:
        svc = TenantService()
        created = await svc.create_tenant("Corp", "resolvable@corp.com")
        key = await svc.create_api_key(created["tenant_id"], "Key2", [])
        ctx = await svc.resolve_api_key(key["raw_key"])
        assert ctx is not None
        assert ctx.tenant_id == created["tenant_id"]


# ── revoke_api_key ────────────────────────────────────────────────────────────

class TestRevokeApiKey:
    async def test_revoke_deactivates_key(self) -> None:
        svc = TenantService()
        created = await svc.create_tenant("Corp", "revoke@corp.com")
        tid = created["tenant_id"]
        key = await svc.create_api_key(tid, "Key", [])
        await svc.revoke_api_key(tid, key["key_id"])
        ctx = await svc.resolve_api_key(key["raw_key"])
        assert ctx is None  # revoked

    async def test_revoke_nonexistent_key_raises(self) -> None:
        svc = TenantService()
        created = await svc.create_tenant("Corp", "rev2@corp.com")
        with pytest.raises(NotFoundError):
            await svc.revoke_api_key(created["tenant_id"], "nonexistent-key")

    async def test_revoke_wrong_tenant_raises(self) -> None:
        svc = TenantService()
        c1 = await svc.create_tenant("Corp1", "t1@corp.com")
        c2 = await svc.create_tenant("Corp2", "t2@corp.com")
        key = await svc.create_api_key(c1["tenant_id"], "Key", [])
        with pytest.raises(NotFoundError):
            await svc.revoke_api_key(c2["tenant_id"], key["key_id"])


# ── resolve_api_key ───────────────────────────────────────────────────────────

class TestResolveApiKey:
    async def test_resolve_valid_key(self) -> None:
        svc = TenantService()
        created = await svc.create_tenant("Corp", "resolve@corp.com")
        ctx = await svc.resolve_api_key(created["api_key"])
        assert ctx is not None
        assert ctx.tenant_id == created["tenant_id"]
        assert ctx.plan == PlanTier.FREE

    async def test_resolve_unknown_key_returns_none(self) -> None:
        svc = TenantService()
        ctx = await svc.resolve_api_key("av_free_completely_invalid_key")
        assert ctx is None

    async def test_resolve_revoked_key_returns_none(self) -> None:
        svc = TenantService()
        created = await svc.create_tenant("Corp", "rv@corp.com")
        tid = created["tenant_id"]
        raw_key = created["api_key"]
        kid = created["api_key_id"]
        await svc.revoke_api_key(tid, kid)
        ctx = await svc.resolve_api_key(raw_key)
        assert ctx is None

    async def test_resolve_expired_key_returns_none(self) -> None:
        svc = TenantService()
        created = await svc.create_tenant("Corp", "exp@corp.com")
        tid = created["tenant_id"]
        past = datetime.now(UTC) - timedelta(hours=1)
        key = await svc.create_api_key(tid, "Expired", [], expires_at=past)
        ctx = await svc.resolve_api_key(key["raw_key"])
        assert ctx is None

    async def test_resolve_uses_redis_cache(self) -> None:
        import json
        svc = TenantService()
        created = await svc.create_tenant("Corp", "cache@corp.com")
        raw_key = created["api_key"]

        cached_data = json.dumps({
            "tenant_id": created["tenant_id"],
            "plan": "free",
            "api_key_id": created["api_key_id"],
            "roles": ["admin"],
        })

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=cached_data.encode())
        svc._redis = mock_redis

        ctx = await svc.resolve_api_key(raw_key)
        assert ctx is not None
        assert ctx.tenant_id == created["tenant_id"]
        mock_redis.get.assert_called_once()

    async def test_resolve_populates_redis_cache(self) -> None:
        svc = TenantService()
        created = await svc.create_tenant("Corp", "pop_cache@corp.com")
        raw_key = created["api_key"]

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)  # cache miss
        mock_redis.setex = AsyncMock()
        svc._redis = mock_redis

        ctx = await svc.resolve_api_key(raw_key)
        assert ctx is not None
        mock_redis.setex.assert_called_once()

    async def test_redis_error_falls_through_to_memory(self) -> None:
        svc = TenantService()
        created = await svc.create_tenant("Corp", "redis_err@corp.com")
        raw_key = created["api_key"]

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=Exception("Redis down"))
        svc._redis = mock_redis

        ctx = await svc.resolve_api_key(raw_key)
        assert ctx is not None  # fallback to memory


# ── SSO provisioning ──────────────────────────────────────────────────────────

class TestSSOProvisioning:
    async def test_create_tenant_from_sso(self) -> None:
        svc = TenantService()
        result = await svc.create_tenant_from_sso(
            sso_sub="sub-123",
            email="sso@corp.com",
            name="SSO User",
            plan="starter",
        )
        assert result["sso_sub"] == "sub-123"
        assert result["email"] == "sso@corp.com"
        assert "tenant_id" in result

    async def test_get_tenant_by_sso_sub_found_in_memory(self) -> None:
        svc = TenantService()
        await svc.create_tenant_from_sso(
            sso_sub="sub-456", email="find@corp.com", name="Find Me"
        )
        tenant = await svc.get_tenant_by_sso_sub(sso_sub="sub-456")
        assert tenant is not None
        assert tenant["sso_sub"] == "sub-456"

    async def test_get_tenant_by_sso_sub_not_found(self) -> None:
        svc = TenantService()
        result = await svc.get_tenant_by_sso_sub(sso_sub="nonexistent-sub")
        assert result is None

    async def test_get_key_by_sso_sub_returns_key(self) -> None:
        svc = TenantService()
        await svc.create_tenant_from_sso(
            sso_sub="sub-789", email="key@corp.com", name="Key User"
        )
        key = await svc.get_key_by_sso_sub(sso_sub="sub-789")
        assert key is not None

    async def test_get_key_by_sso_sub_not_found(self) -> None:
        svc = TenantService()
        key = await svc.get_key_by_sso_sub(sso_sub="ghost-sub")
        assert key is None

    async def test_create_tenant_from_sso_plans(self) -> None:
        svc = TenantService()
        for plan, expected in [
            ("free", PlanTier.FREE),
            ("starter", PlanTier.STARTER),
            ("professional", PlanTier.PROFESSIONAL),
            ("enterprise", PlanTier.ENTERPRISE),
        ]:
            result = await svc.create_tenant_from_sso(
                sso_sub=f"sub-{plan}",
                email=f"{plan}@corp.com",
                name=f"{plan.capitalize()} User",
                plan=plan,
            )
            assert result["plan"] == expected.value


# ── sync_from_db ──────────────────────────────────────────────────────────────

class TestSyncFromDb:
    async def test_sync_returns_0_when_no_db(self) -> None:
        svc = TenantService()
        count = await svc.sync_from_db()
        assert count == 0

    async def test_sync_returns_0_on_error(self) -> None:
        async def bad_factory():
            raise Exception("DB error")

        svc = TenantService(db_session_factory=bad_factory)
        count = await svc.sync_from_db()
        assert count == 0
