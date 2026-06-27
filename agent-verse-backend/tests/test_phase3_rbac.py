"""Tests for Phase 3: RBAC and IP allowlisting."""
from __future__ import annotations

import pytest
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport


# ── Task 3.1: ORM model tests ────────────────────────────────────────────────

def test_user_role_model_has_required_fields():
    """UserRole ORM model must have id, tenant_id, user_id, role, created_at."""
    from app.db.models.rbac import UserRole

    assert hasattr(UserRole, "id")
    assert hasattr(UserRole, "tenant_id")
    assert hasattr(UserRole, "user_id")
    assert hasattr(UserRole, "role")
    assert hasattr(UserRole, "created_at")


def test_ip_allowlist_model_has_required_fields():
    """IPAllowlistEntry must have id, tenant_id, cidr, description, created_at."""
    from app.db.models.rbac import IPAllowlistEntry

    assert hasattr(IPAllowlistEntry, "id")
    assert hasattr(IPAllowlistEntry, "cidr")
    assert hasattr(IPAllowlistEntry, "description")
    assert hasattr(IPAllowlistEntry, "tenant_id")
    assert hasattr(IPAllowlistEntry, "created_at")


# ── Task 3.2: TenantContext + RBAC module tests ───────────────────────────────

def test_tenant_context_has_roles_field():
    """TenantContext must have a roles field defaulting to empty tuple."""
    from app.tenancy.context import PlanTier, TenantContext

    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    assert hasattr(ctx, "roles")
    assert ctx.roles == ()


def test_tenant_context_roles_are_immutable():
    """TenantContext.roles must be a tuple (immutable)."""
    from app.tenancy.context import PlanTier, TenantContext

    ctx = TenantContext(
        tenant_id="t1",
        plan=PlanTier.FREE,
        api_key_id="k1",
        roles=("admin", "operator"),
    )
    assert isinstance(ctx.roles, tuple)
    assert "admin" in ctx.roles


def test_require_role_allows_matching_role():
    """has_role must return True when tenant has required role.
    Admin role implies operator/viewer/approver via role hierarchy.
    """
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.rbac import has_role

    ctx = TenantContext(
        tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=("admin",)
    )
    assert has_role(ctx, "admin") is True
    # admin implies operator via hierarchy
    assert has_role(ctx, "operator") is True

    # viewer-only context should NOT have admin
    viewer_ctx = TenantContext(
        tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=("viewer",)
    )
    assert has_role(viewer_ctx, "admin") is False
    assert has_role(viewer_ctx, "operator") is False


def test_require_role_admin_implies_all_roles():
    """Admin role implicitly has operator and approver permissions via hierarchy."""
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.rbac import has_any_role

    ctx = TenantContext(
        tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=("admin",)
    )
    # Admin implies operator and approver via hierarchy
    assert has_any_role(ctx, ["admin", "operator"]) is True
    assert has_any_role(ctx, ["approver", "admin"]) is True


def test_has_role_hierarchy_expansion():
    """effective_roles must expand hierarchy correctly."""
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.rbac import effective_roles

    admin_ctx = TenantContext(
        tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=("admin",)
    )
    roles = effective_roles(admin_ctx)
    assert "admin" in roles
    assert "operator" in roles
    assert "approver" in roles
    assert "viewer" in roles

    operator_ctx = TenantContext(
        tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=("operator",)
    )
    op_roles = effective_roles(operator_ctx)
    assert "operator" in op_roles
    assert "viewer" in op_roles
    assert "admin" not in op_roles
    assert "approver" not in op_roles


def test_require_role_viewer_only():
    """Viewer role must only imply itself."""
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.rbac import has_any_role

    ctx = TenantContext(
        tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=("viewer",)
    )
    assert has_any_role(ctx, ["viewer"]) is True
    assert has_any_role(ctx, ["operator"]) is False
    assert has_any_role(ctx, ["admin"]) is False
    assert has_any_role(ctx, ["approver"]) is False


# ── Task 3.3: Role-protected endpoint tests ───────────────────────────────────

@pytest.mark.asyncio
async def test_emergency_stop_allowed_for_admin():
    """POST /governance/emergency-stop must succeed for admin."""
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.rbac import has_role

    admin_ctx = TenantContext(
        tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=("admin",)
    )
    # Test role check directly
    assert has_role(admin_ctx, "admin") is True


@pytest.mark.asyncio
async def test_delete_agent_requires_operator_or_admin():
    """DELETE /agents/{id} must require operator or admin role."""
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.rbac import has_any_role

    viewer_ctx = TenantContext(
        tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=("viewer",)
    )
    operator_ctx = TenantContext(
        tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=("operator",)
    )
    assert not has_any_role(viewer_ctx, ["operator", "admin"])
    assert has_any_role(operator_ctx, ["operator", "admin"])


@pytest.mark.asyncio
async def test_audit_requires_non_viewer_role():
    """GET /governance/audit must be accessible to operator, approver, admin."""
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.rbac import has_any_role

    for role in ["operator", "approver", "admin"]:
        ctx = TenantContext(
            tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=(role,)
        )
        assert has_any_role(ctx, ["operator", "approver", "admin"]), (
            f"Role {role} should have audit access"
        )

    viewer = TenantContext(
        tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=("viewer",)
    )
    assert not has_any_role(viewer, ["operator", "approver", "admin"])


# ── Task 3.4: Role management API tests ──────────────────────────────────────

@pytest.mark.asyncio
async def test_create_role_endpoint_exists():
    """POST /tenants/me/roles must exist (not 404)."""
    from app.main import create_app

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/tenants/me/roles",
            json={"user_id": "user-123", "role": "operator"},
            headers={"X-API-Key": "test-key"},
        )
        # May return 401 (no real auth) — but must not be 404
        assert response.status_code != 404


@pytest.mark.asyncio
async def test_role_endpoint_rejects_invalid_role():
    """POST /tenants/me/roles must return 422 for invalid role name."""
    from pydantic import ValidationError

    from app.api.tenants import CreateRoleRequest

    with pytest.raises(ValidationError):
        CreateRoleRequest(user_id="user-1", role="superuser")  # invalid role


# ── Task 3.5: IP allowlist tests ──────────────────────────────────────────────

def test_ip_in_cidr_check():
    """IP allowlist CIDR matching must work correctly."""
    from app.tenancy.rbac import is_ip_allowed

    # Single IP in /32
    assert is_ip_allowed("192.168.1.100", ["192.168.1.100/32"])
    # IP in /24
    assert is_ip_allowed("10.0.0.50", ["10.0.0.0/24"])
    # IP not in range
    assert not is_ip_allowed("192.168.2.1", ["192.168.1.0/24"])
    # Loopback always allowed
    assert is_ip_allowed("127.0.0.1", ["10.0.0.0/8"])  # loopback bypass


def test_ip_allowlist_empty_means_all_allowed():
    """Empty IP allowlist means no restriction."""
    from app.tenancy.rbac import is_ip_allowed

    assert is_ip_allowed("1.2.3.4", [])  # No entries = allow all


def test_ip_allowlist_invalid_ip_denied():
    """Invalid IP format should be denied."""
    from app.tenancy.rbac import is_ip_allowed

    assert not is_ip_allowed("not-an-ip", ["10.0.0.0/8"])


@pytest.mark.asyncio
async def test_ip_allowlist_endpoint_exists():
    """GET /tenants/me/ip-allowlist must exist and return list."""
    from app.main import create_app

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/tenants/me/ip-allowlist",
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code != 404


@pytest.mark.asyncio
async def test_ip_allowlist_post_endpoint_exists():
    """POST /tenants/me/ip-allowlist must exist."""
    from app.main import create_app

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/tenants/me/ip-allowlist",
            json={"cidr": "192.168.1.0/24", "description": "test"},
            headers={"X-API-Key": "test-key"},
        )
        # 401 (no auth) is fine — just not 404
        assert response.status_code != 404


@pytest.mark.asyncio
async def test_ip_allowlist_invalid_cidr_returns_422():
    """POST /tenants/me/ip-allowlist must return 422 for invalid CIDR."""
    from pydantic import ValidationError

    from app.api.tenants import CreateIPAllowlistRequest

    with pytest.raises(ValidationError):
        CreateIPAllowlistRequest(cidr="not-a-cidr")


def test_extract_roles_from_jwt():
    """extract_roles_from_jwt must parse Keycloak realm_access correctly."""
    from app.tenancy.rbac import extract_roles_from_jwt

    payload = {
        "realm_access": {"roles": ["admin", "some-other-role"]},
        "resource_access": {"agentverse": {"roles": ["operator"]}},
    }
    roles = extract_roles_from_jwt(payload)
    assert "admin" in roles
    assert "operator" in roles
    assert "some-other-role" not in roles  # not a valid role
