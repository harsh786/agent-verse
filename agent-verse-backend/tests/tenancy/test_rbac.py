"""Tests for app/tenancy/rbac.py — RBAC role hierarchy and helpers."""
from __future__ import annotations

import pytest
from app.tenancy.rbac import (
    ROLE_ADMIN,
    ROLE_APPROVER,
    ROLE_OPERATOR,
    ROLE_VIEWER,
    effective_roles,
    has_any_role,
    has_role,
)
from app.tenancy.context import PlanTier, TenantContext


# ── helpers ───────────────────────────────────────────────────────────────────

def _ctx(*roles: str) -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=roles)


# ── role constant values ──────────────────────────────────────────────────────

def test_role_constants_are_strings():
    assert ROLE_ADMIN == "admin"
    assert ROLE_OPERATOR == "operator"
    assert ROLE_APPROVER == "approver"
    assert ROLE_VIEWER == "viewer"


# ── admin has all roles ───────────────────────────────────────────────────────

def test_admin_has_all():
    ctx = _ctx(ROLE_ADMIN)
    for role in [ROLE_ADMIN, ROLE_OPERATOR, ROLE_APPROVER, ROLE_VIEWER]:
        assert has_role(ctx, role) is True, f"admin should have role {role}"


def test_admin_effective_roles_contains_all():
    roles = effective_roles(_ctx(ROLE_ADMIN))
    assert {ROLE_ADMIN, ROLE_OPERATOR, ROLE_APPROVER, ROLE_VIEWER} <= roles


# ── operator hierarchy ────────────────────────────────────────────────────────

def test_operator_has_operator_and_viewer():
    ctx = _ctx(ROLE_OPERATOR)
    assert has_role(ctx, ROLE_OPERATOR) is True
    assert has_role(ctx, ROLE_VIEWER) is True
    assert has_role(ctx, ROLE_ADMIN) is False
    assert has_role(ctx, ROLE_APPROVER) is False


# ── viewer only ───────────────────────────────────────────────────────────────

def test_viewer_only():
    ctx = _ctx(ROLE_VIEWER)
    assert has_role(ctx, ROLE_VIEWER) is True
    assert has_role(ctx, ROLE_OPERATOR) is False
    assert has_role(ctx, ROLE_ADMIN) is False
    assert has_role(ctx, ROLE_APPROVER) is False


# ── empty roles ───────────────────────────────────────────────────────────────

def test_empty_roles():
    ctx = _ctx()
    assert has_role(ctx, ROLE_VIEWER) is False
    assert has_any_role(ctx, [ROLE_VIEWER, ROLE_ADMIN]) is False


# ── multiple roles ────────────────────────────────────────────────────────────

def test_multiple_roles_viewer_and_approver():
    ctx = _ctx(ROLE_VIEWER, ROLE_APPROVER)
    assert has_role(ctx, ROLE_APPROVER) is True
    assert has_role(ctx, ROLE_VIEWER) is True
    assert has_role(ctx, ROLE_ADMIN) is False
    assert has_role(ctx, ROLE_OPERATOR) is False


# ── IP allowlist endpoint auth ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ip_allowlist_list_requires_auth():
    from httpx import AsyncClient, ASGITransport
    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/tenants/me/ip-allowlist")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_ip_allowlist_add_and_list():
    from httpx import AsyncClient, ASGITransport
    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "RBACTest", "email": "rbac_new@t.com"})
        assert r.status_code == 201
        c.headers["X-API-Key"] = r.json()["api_key"]

        r2 = await c.get("/tenants/me/ip-allowlist")
        assert r2.status_code == 200
        assert isinstance(r2.json(), list)

        r3 = await c.post(
            "/tenants/me/ip-allowlist",
            json={"cidr": "10.0.0.0/8", "description": "internal"},
        )
        assert r3.status_code == 201
        assert r3.json()["cidr"] == "10.0.0.0/8"


@pytest.mark.asyncio
async def test_ip_allowlist_invalid_cidr():
    from httpx import AsyncClient, ASGITransport
    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "RBACTest2", "email": "rbac2_new@t.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        r2 = await c.post(
            "/tenants/me/ip-allowlist",
            json={"cidr": "not-a-cidr"},
        )
        assert r2.status_code == 422
