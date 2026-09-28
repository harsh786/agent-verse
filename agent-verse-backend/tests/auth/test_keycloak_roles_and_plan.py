"""Regression tests: the Keycloak SSO TenantContext carries RBAC roles, and the
plan comes from the tenant record — never from IdP realm roles.

1. ``resolve_tenant_from_jwt`` built the TenantContext with no roles, so every
   write by an SSO user was 403 (or, with the legacy flag, everything allowed).
2. ``map_roles_to_plan`` made a realm ``admin`` an ``enterprise`` tenant: anyone
   who could get a realm role self-granted a paid tier.
3. ``create_tenant_from_sso`` INSERTed the non-existent ``tenants.plan`` column
   and swallowed the error, leaving an in-memory ghost tenant.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.auth.keycloak import resolve_tenant_from_jwt
from app.services.tenant_service import TenantService
from app.tenancy.context import PlanTier


def _payload(roles: list[str]) -> dict[str, object]:
    return {"sub": "kc-sub-1", "email": "sso@corp.test", "realm_access": {"roles": roles}}


@pytest.mark.asyncio
async def test_jit_context_has_mapped_roles_and_the_free_plan() -> None:
    svc = TenantService()
    with patch("app.auth.keycloak.validate_jwt", AsyncMock(return_value=_payload(["admin"]))):
        ctx = await resolve_tenant_from_jwt("a.b.c", svc)
    assert ctx is not None
    assert ctx.roles == ("admin",)
    assert ctx.plan == PlanTier.FREE  # realm admin is NOT enterprise


@pytest.mark.asyncio
async def test_user_without_rbac_realm_roles_is_a_viewer() -> None:
    svc = TenantService()
    with patch(
        "app.auth.keycloak.validate_jwt", AsyncMock(return_value=_payload(["offline_access"]))
    ):
        ctx = await resolve_tenant_from_jwt("a.b.c", svc)
    assert ctx is not None
    assert ctx.roles == ("viewer",)


@pytest.mark.asyncio
async def test_existing_tenant_plan_comes_from_the_tenant_record() -> None:
    svc = TenantService()
    t = await svc.create_tenant_from_sso(sso_sub="kc-sub-1", email="sso@corp.test", name="S")
    svc._tenants[t["tenant_id"]]["plan"] = "professional"  # upgraded via billing
    with patch("app.auth.keycloak.validate_jwt", AsyncMock(return_value=_payload(["viewer"]))):
        ctx = await resolve_tenant_from_jwt("a.b.c", svc)
    assert ctx is not None
    assert ctx.tenant_id == t["tenant_id"]
    assert ctx.plan == PlanTier.PROFESSIONAL
    assert ctx.api_key_id == t["api_key_id"]


@pytest.mark.asyncio
async def test_sso_login_never_claims_an_existing_tenant_by_email() -> None:
    svc = TenantService()
    await svc.create_tenant(name="Owner", email="sso@corp.test")
    with patch("app.auth.keycloak.validate_jwt", AsyncMock(return_value=_payload(["admin"]))):
        assert await resolve_tenant_from_jwt("a.b.c", svc) is None
