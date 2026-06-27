"""Tests for SSO JIT tenant provisioning."""
import pytest
from app.services.tenant_service import TenantService


@pytest.mark.asyncio
async def test_create_tenant_from_sso_returns_api_key():
    svc = TenantService()
    tenant = await svc.create_tenant_from_sso(
        sso_sub="keycloak|user123",
        email="user@example.com",
        name="Test User",
    )
    assert "api_key" in tenant
    assert tenant["api_key"].startswith("av_")
    assert tenant["sso_sub"] == "keycloak|user123"
    assert tenant["email"] == "user@example.com"


@pytest.mark.asyncio
async def test_get_tenant_by_sso_sub_in_memory():
    svc = TenantService()
    tenant = await svc.create_tenant_from_sso(
        sso_sub="k|abc",
        email="a@b.com",
        name="Test",
    )
    found = await svc.get_tenant_by_sso_sub(sso_sub="k|abc")
    assert found is not None
    assert found["tenant_id"] == tenant["tenant_id"]


@pytest.mark.asyncio
async def test_get_tenant_by_sso_sub_not_found():
    svc = TenantService()
    result = await svc.get_tenant_by_sso_sub(sso_sub="nonexistent")
    assert result is None
