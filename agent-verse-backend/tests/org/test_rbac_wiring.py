"""Gap #3: org RBAC is now WIRED — the dependency resolves a real role and is
attached to the sensitive org endpoints.

Role model: the authenticated tenant owns any org it can reach (every org query
is RLS-scoped to its own tenant_id), so with no assigned sub-role it resolves to
org_admin; an explicitly-assigned lower role (in TenantContext.roles) is honored
and enforced; an unauthenticated request is denied (viewer).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.org.rbac import OrgRole, _resolve_actor_role, enforce_org_role


def _req(*, tenant=None, org_role=None):
    state = SimpleNamespace(tenant=tenant, org_role=org_role)
    return SimpleNamespace(state=state)


def _tenant(*, tenant_id="t1", roles=()):
    return SimpleNamespace(tenant_id=tenant_id, roles=tuple(roles))


def test_owner_admin_key_resolves_to_org_admin() -> None:
    # The tenant owner key carries roles=("admin",) → org_admin.
    assert _resolve_actor_role(_req(tenant=_tenant(roles=("admin",)))) == OrgRole.ORG_ADMIN


def test_fail_closed_when_no_role_assigned() -> None:
    # FAIL-CLOSED: an authenticated key with no admin/org role → viewer (denied),
    # never silently elevated to admin. Unauthenticated → viewer too.
    assert _resolve_actor_role(_req(tenant=_tenant(roles=()))) == OrgRole.VIEWER
    assert _resolve_actor_role(_req(tenant=_tenant(roles=("operator",)))) == OrgRole.VIEWER
    assert _resolve_actor_role(_req(tenant=None)) == OrgRole.VIEWER


def test_assigned_sub_role_is_honored() -> None:
    assert _resolve_actor_role(_req(tenant=_tenant(roles=("viewer",)))) == OrgRole.VIEWER
    assert _resolve_actor_role(_req(tenant=_tenant(roles=("team_lead",)))) == OrgRole.TEAM_LEAD
    # Highest wins when several are present.
    assert (
        _resolve_actor_role(_req(tenant=_tenant(roles=("viewer", "team_lead", "agent"))))
        == OrgRole.TEAM_LEAD
    )


def test_explicit_org_role_overrides() -> None:
    assert _resolve_actor_role(_req(tenant=_tenant(roles=("viewer",)), org_role="org_admin")) == "org_admin"


def test_enforce_denies_viewer_writes_and_allows_owner() -> None:
    viewer_req = _req(tenant=_tenant(roles=("viewer",)))
    with pytest.raises(HTTPException) as exc:
        enforce_org_role(viewer_req, OrgRole.TEAM_LEAD)
    assert exc.value.status_code == 403

    owner_req = _req(tenant=_tenant(roles=("admin",)))
    assert enforce_org_role(owner_req, OrgRole.TEAM_LEAD) == OrgRole.ORG_ADMIN


def test_sensitive_endpoints_have_rbac_dependency_attached() -> None:
    """The dependency is genuinely wired onto the sensitive endpoints (not dead)."""
    import importlib
    import inspect

    org_router = importlib.import_module("app.org.router")

    for fn_name, minimum in [
        ("delete_organization", OrgRole.ORG_ADMIN),
        ("update_organization", OrgRole.DEPT_ADMIN),
        ("create_mission", OrgRole.TEAM_LEAD),
        ("approve_org_request", OrgRole.TEAM_LEAD),
        ("reject_org_request", OrgRole.TEAM_LEAD),
    ]:
        fn = getattr(org_router, fn_name)
        assert "_rbac" in inspect.signature(fn).parameters, f"{fn_name} missing RBAC dependency"
