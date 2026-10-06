"""QA-3: role -> scope resolution honours the role hierarchy.

Regression: keys created from the UI default to ``operator``, whose
``ROLE_SCOPES`` entry lacks ``audit:read`` / ``costs:read`` /
``governance:read`` that ``viewer`` holds. The role fallback never expanded
``app.tenancy.rbac``'s hierarchy (operator ⊇ viewer, admin ⊇ all), so an
operator key could do more than a viewer yet could not read audit, costs or
governance. Every role -> scope computation now expands implied roles first.
"""

from __future__ import annotations

import pytest

from app.auth.scope_enforcement import ROLE_SCOPES, ScopeEnforcementMiddleware, scopes_for_roles
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.rbac import expand_roles


def test_expand_roles_follows_hierarchy() -> None:
    assert expand_roles(["operator"]) == {"operator", "viewer"}
    assert expand_roles(["approver"]) == {"approver", "viewer"}
    assert expand_roles(["admin"]) == {"admin", "operator", "approver", "viewer"}
    # Unknown roles (e.g. the agent-JWT "agent" role) map to themselves only.
    assert expand_roles(["agent"]) == {"agent"}


@pytest.mark.parametrize("scope", ["audit:read", "costs:read", "governance:read"])
def test_operator_inherits_viewer_read_scopes(scope: str) -> None:
    assert scope in scopes_for_roles(("operator",))


def test_operator_still_lacks_admin_scopes() -> None:
    granted = scopes_for_roles(("operator",))
    assert "costs:admin" not in granted
    assert "tenancy:write" not in granted
    assert "governance:write" not in granted


def test_viewer_scopes_are_a_subset_of_operator_and_admin() -> None:
    viewer = scopes_for_roles(("viewer",))
    assert viewer <= scopes_for_roles(("operator",))
    assert viewer <= scopes_for_roles(("approver",))
    assert scopes_for_roles(("operator",)) <= scopes_for_roles(("admin",))


def test_agent_role_is_not_expanded() -> None:
    assert scopes_for_roles(("agent",)) == ROLE_SCOPES["agent"]


@pytest.mark.asyncio
async def test_middleware_role_fallback_expands_hierarchy() -> None:
    granted = await ScopeEnforcementMiddleware._load_scopes(
        db_factory=None, tenant_id="t1", key_id="k1", roles=("operator",)
    )
    assert {"audit:read", "costs:read", "governance:read"} <= granted


def test_websocket_scope_check_expands_hierarchy() -> None:
    from app.tenancy.ws_auth import _scope_denied

    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=("operator",))
    assert _scope_denied(ctx, "audit:read", write=False, path="/ws/audit") is False


def test_slack_principal_expands_hierarchy() -> None:
    from app.integrations.slack.identity import SlackPrincipal

    p = SlackPrincipal(tenant_id="t1", principal_id="k1", roles=("operator",))
    assert p.can("costs:read")
    assert not p.can("costs:admin")
