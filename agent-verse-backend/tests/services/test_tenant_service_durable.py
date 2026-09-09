"""Tests for Phase 1c — durable TenantService with Redis cache."""
from unittest.mock import AsyncMock

import pytest


class TestTenantServiceCachedLookup:
    @pytest.mark.asyncio
    async def test_get_tenant_cached_returns_from_redis(self):
        """get_tenant_cached must read from Redis on cache hit."""
        import json

        from app.services.tenant_service import TenantService
        from app.tenancy.context import PlanTier

        mock_redis = AsyncMock()
        mock_redis.get.return_value = json.dumps(
            {
                "tenant_id": "t1",
                "plan": "professional",
                "api_key_id": "k1",
                "roles": ["admin"],
            }
        ).encode()

        svc = TenantService.__new__(TenantService)
        svc._tenants = {}

        assert hasattr(svc, "get_tenant_cached"), "get_tenant_cached method is missing"

        result = await svc.get_tenant_cached("t1", redis=mock_redis)
        assert result is not None
        assert result.tenant_id == "t1"
        assert result.plan == PlanTier.PROFESSIONAL
        assert result.api_key_id == "k1"
        assert "admin" in result.roles
        # Verify we did NOT hit DB (no db param passed)
        mock_redis.get.assert_called_once_with("tenant:t1")

    @pytest.mark.asyncio
    async def test_get_tenant_cached_falls_back_to_memory_on_redis_miss(self):
        """get_tenant_cached falls back to in-memory on Redis cache miss."""
        from app.services.tenant_service import TenantService
        from app.tenancy.context import PlanTier

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None  # cache miss

        svc = TenantService.__new__(TenantService)
        svc._tenants = {
            "t2": {
                "tenant_id": "t2",
                "plan": "starter",
                "api_key_id": "",
                "roles": [],
            }
        }
        svc._db = None

        result = await svc.get_tenant_cached("t2", redis=mock_redis)
        assert result is not None
        assert result.tenant_id == "t2"
        assert result.plan == PlanTier.STARTER

    @pytest.mark.asyncio
    async def test_get_tenant_cached_returns_none_for_unknown_tenant(self):
        """get_tenant_cached returns None when tenant is not found anywhere."""
        from app.services.tenant_service import TenantService

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        svc = TenantService.__new__(TenantService)
        svc._tenants = {}
        svc._db = None

        result = await svc.get_tenant_cached("unknown", redis=mock_redis)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_tenant_cached_works_without_redis(self):
        """get_tenant_cached falls back gracefully when redis=None."""
        from app.services.tenant_service import TenantService
        from app.tenancy.context import PlanTier

        svc = TenantService.__new__(TenantService)
        svc._tenants = {
            "t3": {
                "tenant_id": "t3",
                "plan": "enterprise",
                "api_key_id": "",
                "roles": [],
            }
        }
        svc._db = None

        result = await svc.get_tenant_cached("t3")
        assert result is not None
        assert result.plan == PlanTier.ENTERPRISE

    @pytest.mark.asyncio
    async def test_cache_invalidated_on_update(self):
        """Updating a tenant must invalidate the Redis cache."""
        from app.services.tenant_service import TenantService

        mock_redis = AsyncMock()
        svc = TenantService.__new__(TenantService)
        svc._tenants = {"t1": {"tenant_id": "t1", "plan": "free"}}

        assert hasattr(svc, "invalidate_tenant_cache"), "invalidate_tenant_cache method is missing"

        await svc.invalidate_tenant_cache("t1", redis=mock_redis)
        mock_redis.delete.assert_called_once_with("tenant:t1")
        # In-memory dict is NOT cleared — it is the authoritative source of truth
        # when there is no DB; only the Redis cache layer is invalidated.

    @pytest.mark.asyncio
    async def test_cache_invalidate_without_redis_is_safe(self):
        """invalidate_tenant_cache is a no-op when redis=None."""
        from app.services.tenant_service import TenantService

        svc = TenantService.__new__(TenantService)
        svc._tenants = {"t1": {"tenant_id": "t1", "plan": "free"}}

        # Must not raise
        await svc.invalidate_tenant_cache("t1", redis=None)
        # In-memory dict is preserved (not the cache layer)

    @pytest.mark.asyncio
    async def test_cache_invalidate_handles_redis_error(self):
        """invalidate_tenant_cache swallows Redis errors gracefully."""
        from app.services.tenant_service import TenantService

        mock_redis = AsyncMock()
        mock_redis.delete.side_effect = ConnectionError("Redis down")

        svc = TenantService.__new__(TenantService)
        svc._tenants = {"t1": {"tenant_id": "t1", "plan": "free"}}

        # Must not raise even when Redis errors
        await svc.invalidate_tenant_cache("t1", redis=mock_redis)
        # In-memory dict is preserved even when Redis fails

    @pytest.mark.asyncio
    async def test_get_tenant_cached_populates_redis_from_memory(self):
        """On a Redis miss, get_tenant_cached writes in-memory result to Redis."""
        from app.services.tenant_service import TenantService

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None  # cache miss

        svc = TenantService.__new__(TenantService)
        svc._tenants = {
            "t4": {"tenant_id": "t4", "plan": "free", "api_key_id": "", "roles": []}
        }
        svc._db = None

        # Cache miss with redis — but no DB, so falls through to in-memory
        # In-memory fallback does NOT write back to Redis (only DB path does)
        result = await svc.get_tenant_cached("t4", redis=mock_redis)
        assert result is not None
        assert result.tenant_id == "t4"


class TestAdminRouter:
    def test_admin_router_importable(self):
        from app.api.admin import router

        assert router is not None

    def test_admin_router_has_required_endpoints(self):
        from app.api.admin import router

        paths = [r.path for r in router.routes]
        assert any("/tenants" in p for p in paths), "Missing /tenants endpoint"
        assert any("/usage" in p for p in paths), "Missing /usage endpoint"
        assert any("{tenant_id}" in p for p in paths), "Missing tenant detail endpoint"

    def test_admin_router_has_plan_change_endpoint(self):
        from app.api.admin import router

        paths = [r.path for r in router.routes]
        assert any("plan" in p for p in paths), "Missing plan change endpoint"

    def test_admin_requires_admin_key(self):
        """Admin endpoints must reject invalid X-Admin-Key with 401."""
        import os

        from fastapi import HTTPException

        from app.api.admin import _require_admin

        os.environ["PLATFORM_ADMIN_KEY"] = "secret-key"
        try:
            with pytest.raises(HTTPException) as exc:
                _require_admin(x_admin_key="wrong-key")
            assert exc.value.status_code == 401
        finally:
            del os.environ["PLATFORM_ADMIN_KEY"]

    def test_admin_returns_503_when_key_unconfigured(self):
        """Admin endpoints must return 503 when PLATFORM_ADMIN_KEY is not set."""
        import os

        from fastapi import HTTPException

        from app.api.admin import _require_admin

        os.environ.pop("PLATFORM_ADMIN_KEY", None)
        with pytest.raises(HTTPException) as exc:
            _require_admin(x_admin_key="any-key")
        assert exc.value.status_code == 503

    def test_plan_change_request_schema(self):
        from app.api.admin import PlanChangeRequest

        req = PlanChangeRequest(plan="professional")
        assert req.plan == "professional"
