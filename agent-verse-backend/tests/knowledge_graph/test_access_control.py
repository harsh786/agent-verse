"""Tests for app.knowledge_graph.access_control — RBAC for knowledge graphs."""
from __future__ import annotations

import pytest

from app.knowledge_graph.access_control import (
    ACCESS_LEVELS,
    GraphAccessControl,
    get_graph_access_control,
)


def test_get_graph_access_control_returns_singleton() -> None:
    a = get_graph_access_control()
    b = get_graph_access_control()
    assert a is b
    assert isinstance(a, GraphAccessControl)


# ---------------------------------------------------------------------------
# allowed_operations / can
# ---------------------------------------------------------------------------


def test_allowed_operations_known_role() -> None:
    gac = GraphAccessControl()
    assert gac.allowed_operations("tenant_admin") == ACCESS_LEVELS["tenant_admin"]


def test_allowed_operations_unknown_role_returns_empty() -> None:
    gac = GraphAccessControl()
    assert gac.allowed_operations("nonexistent_role") == []


def test_can_true_for_allowed_operation() -> None:
    gac = GraphAccessControl()
    assert gac.can("org_member", "query") is True


def test_can_false_for_disallowed_operation() -> None:
    gac = GraphAccessControl()
    assert gac.can("org_viewer", "write") is False


def test_can_approver_has_no_access() -> None:
    gac = GraphAccessControl()
    assert gac.can("approver", "read") is False
    assert gac.allowed_operations("approver") == []


# ---------------------------------------------------------------------------
# require
# ---------------------------------------------------------------------------


def test_require_passes_for_allowed_operation() -> None:
    gac = GraphAccessControl()
    gac.require("org_member", "query")  # must not raise


def test_require_raises_for_disallowed_operation() -> None:
    gac = GraphAccessControl()
    with pytest.raises(PermissionError, match="approver"):
        gac.require("approver", "write")


def test_require_error_message_lists_allowed_operations() -> None:
    gac = GraphAccessControl()
    with pytest.raises(PermissionError) as exc_info:
        gac.require("org_viewer", "export")
    assert "Allowed:" in str(exc_info.value)


# ---------------------------------------------------------------------------
# set_override
# ---------------------------------------------------------------------------


def test_set_override_changes_role_permissions_globally() -> None:
    gac = GraphAccessControl()
    gac.set_override("org_viewer", ["read", "query", "write"])
    assert gac.can("org_viewer", "write") is True
    assert gac.allowed_operations("org_viewer") == ["read", "query", "write"]


def test_set_override_filters_invalid_operations() -> None:
    gac = GraphAccessControl()
    gac.set_override("org_viewer", ["read", "not_a_real_op"])
    assert gac.allowed_operations("org_viewer") == ["read"]


def test_set_override_is_tenant_scoped() -> None:
    gac = GraphAccessControl()
    gac.set_override("org_viewer", ["read", "write"], tenant_id="tenant-a")
    # Tenant-specific override applies only for that tenant.
    assert gac.can("org_viewer", "write", tenant_id="tenant-a") is True
    assert gac.can("org_viewer", "write", tenant_id="tenant-b") is False
    assert gac.can("org_viewer", "write") is False


# ---------------------------------------------------------------------------
# validate_export_request
# ---------------------------------------------------------------------------


def test_validate_export_request_tenant_admin_gets_full_access() -> None:
    gac = GraphAccessControl()
    config = gac.validate_export_request(
        tenant_id="t1", org_id="org1", requesting_role="tenant_admin", export_format="json"
    )
    assert config["allowed"] is True
    assert config["include_embeddings"] is True
    assert config["max_nodes"] == 10_000
    assert config["format"] == "json"


def test_validate_export_request_org_admin_gets_embeddings_but_lower_cap() -> None:
    gac = GraphAccessControl()
    config = gac.validate_export_request(
        tenant_id="t1", org_id="org1", requesting_role="org_admin"
    )
    assert config["include_embeddings"] is True
    assert config["max_nodes"] == 1_000


def test_validate_export_request_org_member_no_embeddings() -> None:
    gac = GraphAccessControl()
    gac.set_override("org_member", ["read", "query", "export"], tenant_id="t1")
    config = gac.validate_export_request(
        tenant_id="t1", org_id="org1", requesting_role="org_member"
    )
    assert config["include_embeddings"] is False
    assert config["max_nodes"] == 1_000


def test_validate_export_request_denied_role_raises() -> None:
    gac = GraphAccessControl()
    with pytest.raises(PermissionError):
        gac.validate_export_request(tenant_id="t1", org_id="org1", requesting_role="org_viewer")
