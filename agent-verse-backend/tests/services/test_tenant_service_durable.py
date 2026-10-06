"""Tests for Phase 1c — durable TenantService with Redis cache."""
from typing import Any
from unittest.mock import AsyncMock

import pytest


def test_unused_fail_open_tenant_read_through_is_gone():
    """a08-F194-01: ``get_tenant_cached`` had no caller and its DB read answered
    None on any error (a DB outage read as "no such tenant"). Tenant reads go
    through the DB-authoritative ``get_tenant`` only."""
    from app.services.tenant_service import TenantService

    assert not hasattr(TenantService, "get_tenant_cached")
    assert not hasattr(TenantService, "_get_tenant_from_db")


class TestTenantCacheInvalidation:
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



def _admin_request(admin_key: str) -> Any:
    """A request carrying only an X-Admin-Key header (no tenant context)."""
    from types import SimpleNamespace

    return SimpleNamespace(state=SimpleNamespace(), headers={"x-admin-key": admin_key})


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
        """Admin endpoints must reject an invalid X-Admin-Key with 403 (QA-6: never
        401, which the web client treats as an expired session and logs out)."""
        import os

        from fastapi import HTTPException

        from app.api.admin import _require_admin

        os.environ["PLATFORM_ADMIN_KEY"] = "secret-key"
        try:
            with pytest.raises(HTTPException) as exc:
                _require_admin(_admin_request("wrong-key"))
            assert exc.value.status_code == 403
        finally:
            del os.environ["PLATFORM_ADMIN_KEY"]

    def test_admin_returns_503_when_key_unconfigured(self):
        """Admin endpoints must return 503 when PLATFORM_ADMIN_KEY is not set."""
        import os

        from fastapi import HTTPException

        from app.api.admin import _require_admin

        os.environ.pop("PLATFORM_ADMIN_KEY", None)
        with pytest.raises(HTTPException) as exc:
            _require_admin(_admin_request("any-key"))
        assert exc.value.status_code == 503

    def test_plan_change_request_schema(self):
        from app.api.admin import PlanChangeRequest

        req = PlanChangeRequest(plan="professional")
        assert req.plan == "professional"
